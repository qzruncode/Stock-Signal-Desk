from __future__ import annotations

import asyncio

from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.model_runtime import GuardedModelRuntime, ManagedModelStream


def test_chat_runtime_has_no_application_model_response_deadline(monkeypatch) -> None:
    # These settings were previously wired into every conversational model
    # request. Keep them present to prove stale deployment variables cannot
    # reintroduce an application-owned deadline.
    monkeypatch.setenv("AGENT_MODEL_REQUEST_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setenv("AGENT_MODEL_STREAM_IDLE_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setenv("RAG_CHAT_MODEL_REQUEST_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setenv("RAG_CHAT_MODEL_STREAM_IDLE_TIMEOUT_SECONDS", "0.001")

    context = LangGraphRuntimeManager()._context(
        llm_config={"model": "test-model"},
        database=None,
        controller=None,
        run_id="run",
        conversation_id="conversation",
        run_attempt=1,
        tenant_id="tenant",
        owner_id="owner",
        knowledge_base_ids=("kb",),
    )

    assert not hasattr(context.model.gateway.runtime, "request_timeout_seconds")
    assert not hasattr(context.model.gateway.runtime, "stream_idle_timeout_seconds")


def test_model_completion_is_not_cancelled_by_a_local_deadline(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_REQUEST_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setenv("AGENT_PROVIDER_MAX_ATTEMPTS", "1")
    completed = False

    async def completion(**_kwargs):
        nonlocal completed
        await asyncio.sleep(0.02)
        completed = True
        return {"choices": [{"message": {"content": "完整回复"}}]}

    runtime = GuardedModelRuntime(
        database=None,
        run_id="run",
        worker_id="worker",
        model="test-model",
        token_estimator=lambda _messages, _model: 1,
    )
    response = asyncio.run(runtime.complete(completion, messages=[], max_tokens=1))

    assert completed is True
    assert response["choices"][0]["message"]["content"] == "完整回复"


def test_model_stream_waits_for_a_delayed_chunk_without_idle_deadline() -> None:
    finalized: list[BaseException | None] = []

    async def delayed_stream():
        await asyncio.sleep(0.02)
        yield "完整片段"

    async def finalize(error: BaseException | None) -> None:
        finalized.append(error)

    stream = ManagedModelStream(delayed_stream(), on_close=finalize)

    async def collect() -> list[str]:
        return [item async for item in stream]

    assert asyncio.run(collect()) == ["完整片段"]
    assert finalized == [None]
