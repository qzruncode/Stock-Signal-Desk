# -*- coding: utf-8 -*-
"""Analysis task SSE stream tests — /analysis/tasks/stream.

Covers api.v1.endpoints.analysis.task.task_stream:
- Normal stream: emits 'connected' then pending tasks then subscribed events
- Heartbeat: 30s timeout with no events emits a 'heartbeat' event
- Client disconnect: unsubscribe is called in finally
- _format_sse_event helper: formats event type and JSON data
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.analysis import task as task_mod
import src.auth as auth


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def _sse_events(response):
    """Parse SSE event stream into (event_type, data) tuples."""
    event_type = None
    data_parts = []
    for line in response.iter_lines():
        if line is None:
            break
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        if line.startswith("event: "):
            event_type = line[len("event: "):]
        elif line.startswith("data: "):
            data_parts.append(line[len("data: "):])
        elif line == "" and event_type is not None:
            yield event_type, json.loads("".join(data_parts))
            event_type = None
            data_parts = []


# ---------------------------------------------------------------------------
# _format_sse_event helper
# ---------------------------------------------------------------------------

def test_format_sse_event_emits_event_and_data_lines():
    out = task_mod._format_sse_event("task_created", {"id": "t1"})
    assert out.startswith("event: task_created\n")
    assert "data: " in out
    assert out.endswith("\n\n")
    payload = json.loads(out.split("data: ", 1)[1].strip())
    assert payload == {"id": "t1"}


def test_format_sse_event_serializes_non_ascii():
    out = task_mod._format_sse_event("progress", {"msg": "中文"})
    assert "中文" in out  # ensure_ascii=False


# ---------------------------------------------------------------------------
# task_stream route
# ---------------------------------------------------------------------------

def test_task_stream_emits_connected_and_pending_tasks(client):
    pending_task = MagicMock()
    pending_task.to_dict.return_value = {"task_id": "t1", "status": "pending"}

    queue = asyncio.Queue()
    # Pre-load an event then a sentinel to end the stream
    queue.put_nowait({"type": "task_created", "data": {"task_id": "t2"}})
    queue.put_nowait({"type": "_stop", "data": {}})

    def fake_subscribe(q):
        # The route will read from this queue; inject a stop sentinel by
        # making wait_for return the _stop event then raising to end stream.
        pass

    with patch("api.v1.endpoints.analysis.task.get_task_queue") as gtq:
        tq = MagicMock()
        tq.list_pending_tasks.return_value = [pending_task]
        tq.subscribe = MagicMock(side_effect=lambda q: None)
        tq.unsubscribe = MagicMock()
        gtq.return_value = tq

        # Patch asyncio.wait_for to return immediately with our queued events
        original_wait_for = asyncio.wait_for

        async def fast_wait_for(coro, timeout):
            # ignore the real coroutine, return from our queue
            try:
                coro.close()
            except Exception:
                pass
            item = queue.get_nowait()
            if item["type"] == "_stop":
                raise asyncio.CancelledError()
            return item

        with patch("api.v1.endpoints.analysis.task.asyncio.wait_for", side_effect=fast_wait_for):
            with client.stream("GET", "/api/v1/analysis/tasks/stream") as response:
                events = []
                for evt in _sse_events(response):
                    events.append(evt)
                    if evt[0] == "task_created" and evt[1].get("task_id") == "t2":
                        break

    event_types = [t for t, _ in events]
    assert event_types[0] == "connected"
    assert "task_created" in event_types
    # pending task from list_pending_tasks was emitted
    pending_emitted = any(
        t == "task_created" and d.get("task_id") == "t1" for t, d in events
    )
    assert pending_emitted
    tq.unsubscribe.assert_called_once()


def test_task_stream_heartbeat_on_timeout(client):
    queue = asyncio.Queue()

    with patch("api.v1.endpoints.analysis.task.get_task_queue") as gtq:
        tq = MagicMock()
        tq.list_pending_tasks.return_value = []
        tq.subscribe = MagicMock()
        tq.unsubscribe = MagicMock()
        gtq.return_value = tq

        call_count = {"n": 0}

        async def heartbeat_then_stop(coro, timeout):
            try:
                coro.close()
            except Exception:
                pass
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First wait_for times out -> heartbeat
                raise asyncio.TimeoutError()
            # Second wait_for -> stop the stream via CancelledError
            raise asyncio.CancelledError()

        with patch("api.v1.endpoints.analysis.task.asyncio.wait_for", side_effect=heartbeat_then_stop):
            with client.stream("GET", "/api/v1/analysis/tasks/stream") as response:
                events = []
                for evt in _sse_events(response):
                    events.append(evt)
                    if evt[0] == "heartbeat":
                        break

    event_types = [t for t, _ in events]
    assert "connected" in event_types
    assert "heartbeat" in event_types
    tq.unsubscribe.assert_called_once()


def test_task_stream_unsubscribes_on_disconnect(client):
    queue = asyncio.Queue()

    with patch("api.v1.endpoints.analysis.task.get_task_queue") as gtq:
        tq = MagicMock()
        tq.list_pending_tasks.return_value = []
        tq.subscribe = MagicMock()
        tq.unsubscribe = MagicMock()
        gtq.return_value = tq

        async def immediate_cancel(coro, timeout):
            try:
                coro.close()
            except Exception:
                pass
            raise asyncio.CancelledError()

        with patch("api.v1.endpoints.analysis.task.asyncio.wait_for", side_effect=immediate_cancel):
            try:
                with client.stream("GET", "/api/v1/analysis/tasks/stream") as response:
                    list(_sse_events(response))
            except Exception:
                pass

    tq.unsubscribe.assert_called_once()
