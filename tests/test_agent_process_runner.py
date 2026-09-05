from __future__ import annotations

import json
from io import StringIO
import signal
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.agent.tool_dispatch import ToolDispatcher, ToolDispatchRequest
from src.tools.base import (
    current_tool_effect_approval,
    current_tool_execution_context,
    current_tool_idempotency_key,
)
from src.tools.process_runner import ToolProcessTimeout, execute_tool_isolated
from src.tools.process_worker import _exit_after_result, main as process_worker_main
from src.tools.registry import ToolRegistry


class _CompletedProcess:
    pid = 4343
    returncode = 0

    def __init__(self, _command, *, stdin, stdout, stderr, **_kwargs):
        del stderr
        self.request = json.load(stdin)
        stdout.write(
            "progress\n__DSA_TOOL_RESULT__="
            + json.dumps({"ok": True, "result": {"success": True, "value": 1}})
            + "\n"
        )
        stdout.flush()

    def poll(self):
        return self.returncode


def test_isolated_runner_executes_exactly_one_atomic_request() -> None:
    created: list[_CompletedProcess] = []

    def create(*args, **kwargs):
        process = _CompletedProcess(*args, **kwargs)
        created.append(process)
        return process

    with patch("src.tools.process_runner.subprocess.Popen", side_effect=create):
        result = execute_tool_isolated(
            "read_market_indices_sina",
            {},
            idempotency_key="durable-step",
            execution_context={"conversation_id": "conversation", "run_id": "run"},
            effect_approved=False,
        )

    assert result == {"success": True, "value": 1}
    assert len(created) == 1
    request = created[0].request
    assert request["name"] == "read_market_indices_sina"
    assert request["arguments"] == {}
    assert len(request["idempotency_key"]) == 64
    assert request["execution_context"] == {"conversation_id": "conversation", "run_id": "run"}
    assert request["effect_approved"] is False
    assert "actions" not in request and "workflow" not in request


def test_isolated_runner_derives_stable_scoped_idempotency_key() -> None:
    keys: list[str] = []

    class Capture(_CompletedProcess):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            keys.append(self.request["idempotency_key"])

    with patch("src.tools.process_runner.subprocess.Popen", side_effect=Capture):
        for _ in range(2):
            execute_tool_isolated(
                "read_market_indices_sina",
                {},
                idempotency_key="durable-step",
            )

    assert keys[0] == keys[1]
    assert keys[0] != "durable-step"


def test_dispatcher_forwards_server_approval_only_to_isolated_worker() -> None:
    captured: dict[str, object] = {}

    def isolated(_name, _arguments, **kwargs):
        captured.update(kwargs)
        return {"success": True, "partial": False, "errors": []}

    dispatcher = ToolDispatcher(
        ToolRegistry(),
        isolated_executor=isolated,
        compact_result=lambda _name, result: result,
        attach_fallback=lambda _name, _arguments, result: result,
    )
    outcome = dispatcher.execute(
        ToolDispatchRequest(
            tool_name="read_rss_item",
            arguments={
                "item_ref": {
                    "route_path": "/example",
                    "params": {},
                    "options": {},
                    "item_id": "item",
                    "title": "title",
                    "link": "https://example.test/item",
                    "content_hash": "a" * 64,
                }
            },
            idempotency_key="key",
            force_isolation=True,
            conversation_id="conversation",
            run_id="run",
            approved=True,
        ),
        cancel_event=threading.Event(),
        progress_observer=lambda _update: None,
    )

    assert outcome.canonical_result["success"] is True
    assert captured["execution_context"] == {"conversation_id": "conversation", "run_id": "run"}
    assert captured["effect_approved"] is True


def test_dispatcher_rejects_malformed_result_instead_of_synthesizing_success() -> None:
    dispatcher = ToolDispatcher(
        ToolRegistry(),
        isolated_executor=lambda *_args, **_kwargs: "malformed",
        compact_result=lambda _name, result: result,
        attach_fallback=lambda _name, _arguments, result: result,
    )

    with pytest.raises(TypeError, match="must return an object"):
        dispatcher.execute(
            ToolDispatchRequest(
                tool_name="read_market_indices_sina",
                arguments={},
                idempotency_key="key",
                force_isolation=True,
            ),
            cancel_event=threading.Event(),
            progress_observer=lambda _update: None,
        )


def test_worker_exposes_and_resets_server_owned_contexts() -> None:
    request_stream = StringIO(
        json.dumps(
            {
                "name": "read_market_indices_sina",
                "arguments": {},
                "idempotency_key": "worker-key",
                "execution_context": {"conversation_id": "conversation", "run_id": "run"},
                "effect_approved": True,
            }
        )
    )
    response_stream = StringIO()
    with (
        patch("src.tools.process_worker.sys.stdin", request_stream),
        patch("src.tools.process_worker.sys.stdout", response_stream),
        patch(
            "src.tools.registry.ToolRegistry.execute",
            side_effect=lambda *_args, **_kwargs: {
                "success": True,
                "key": current_tool_idempotency_key(),
                "execution_context": current_tool_execution_context(),
                "effect_approved": current_tool_effect_approval(),
            },
        ),
    ):
        assert process_worker_main() == 0

    payload = response_stream.getvalue()
    assert '"key": "worker-key"' in payload
    assert '"conversation_id": "conversation"' in payload
    assert '"effect_approved": true' in payload
    assert current_tool_idempotency_key() is None
    assert current_tool_execution_context() == {}
    assert current_tool_effect_approval() is False


class _BlockingProcess:
    pid = 4242
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        self.returncode = -signal.SIGTERM
        return self.returncode


def test_isolated_runner_terminates_process_group_when_cancelled() -> None:
    cancel_event = threading.Event()
    timer = threading.Timer(0.03, cancel_event.set)
    timer.start()
    try:
        with (
            patch("src.tools.process_runner.subprocess.Popen", return_value=_BlockingProcess()),
            patch("src.tools.process_runner.os.killpg") as kill_group,
        ):
            with pytest.raises(RuntimeError, match="已取消"):
                execute_tool_isolated("read_market_indices_sina", {}, cancel_event=cancel_event)
    finally:
        timer.cancel()
    kill_group.assert_called_once_with(4242, signal.SIGTERM)


def test_isolated_runner_enforces_atomic_tool_deadline() -> None:
    with (
        patch("src.tools.process_runner.subprocess.Popen", return_value=_BlockingProcess()),
        patch("src.tools.process_runner.os.killpg"),
        pytest.raises(ToolProcessTimeout, match="超过"),
    ):
        execute_tool_isolated("read_market_indices_sina", {}, deadline_seconds=0.1)


def test_one_shot_worker_exits_without_thread_finalization() -> None:
    stdout = MagicMock()
    stderr = MagicMock()
    with (
        patch("src.tools.process_worker.sys.stdout", stdout),
        patch("src.tools.process_worker.sys.stderr", stderr),
        patch("src.tools.process_worker.os._exit", side_effect=SystemExit(7)) as immediate_exit,
    ):
        with pytest.raises(SystemExit) as captured:
            _exit_after_result(7)

    assert captured.value.code == 7
    stdout.flush.assert_called_once_with()
    stderr.flush.assert_called_once_with()
    immediate_exit.assert_called_once_with(7)
