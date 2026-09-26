from __future__ import annotations

import asyncio

import pytest

from src.agent.model_runtime import (
    GuardedModelRuntime,
    ManagedModelStream,
    ModelProviderReportedTimeoutError,
)


def test_configured_request_deadline_releases_and_does_not_retry() -> None:
    calls = 0

    async def stalled_completion(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(10)

    runtime = GuardedModelRuntime(
        database=None,
        run_id="run",
        worker_id="worker",
        model="test-model",
        token_estimator=lambda _messages, _model: 1,
        request_timeout_seconds=0.01,
    )

    async def scenario():
        with pytest.raises(ModelProviderReportedTimeoutError, match="request timeout"):
            await runtime.complete(stalled_completion, messages=[], max_tokens=1)

    asyncio.run(scenario())
    assert calls == 1


def test_configured_stream_idle_deadline_closes_provider_lease_as_timeout() -> None:
    finalized: list[BaseException | None] = []

    async def stalled_stream():
        await asyncio.sleep(10)
        yield "unreachable"

    async def finalize(error: BaseException | None) -> None:
        finalized.append(error)

    stream = ManagedModelStream(
        stalled_stream(),
        on_close=finalize,
        idle_timeout_seconds=0.01,
    )

    async def scenario():
        with pytest.raises(ModelProviderReportedTimeoutError, match="stream produced no response"):
            await anext(stream)

    asyncio.run(scenario())
    assert len(finalized) == 1
    assert isinstance(finalized[0], ModelProviderReportedTimeoutError)
