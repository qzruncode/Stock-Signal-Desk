"""Waits must block on events, not periodically re-read HTTP or SQL state."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.responses import JSONResponse


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("arrives", [False, True])
async def test_read_is_only_repeated_after_a_matching_event(monkeypatch, arrives):
    from market_data_service import events
    from market_data_service.api import _read_when_ready
    from market_data_service.schemas import SourceRequest

    reader = Mock(
        tail=AsyncMock(return_value="0-0"),
        wait=AsyncMock(return_value=("1-0", {} if arrives else None)),
    )

    @asynccontextmanager
    async def subscription():
        yield reader

    monkeypatch.setattr(events, "event_reader", subscription)
    waiting = JSONResponse({"status": "refreshing"}, status_code=202)
    read = Mock(side_effect=[waiting, {"ready": True}])
    result = await _read_when_ready(
        SourceRequest(operation="news", max_wait_seconds=1), read, lambda event: True
    )
    assert read.call_count == (2 if arrives else 1)
    assert reader.wait.await_count == 1
    assert result == ({"ready": True} if arrives else waiting)


@pytest.mark.anyio
async def test_unrelated_events_do_not_wake_a_data_read():
    from market_data_service.events import EventReader, affects_data

    redis = Mock(
        xread=AsyncMock(
            side_effect=[
                [("stream", [("1-0", {"payload": json.dumps({"service": {}})})])],
                [
                    (
                        "stream",
                        [
                            (
                                "2-0",
                                {
                                    "payload": json.dumps(
                                        {"states": {"kline": ["000001"]}}
                                    )
                                },
                            )
                        ],
                    )
                ],
            ]
        )
    )
    reader = EventReader(redis)
    cursor, event = await reader.wait(
        "0-0",
        1,
        lambda payload: affects_data(payload, datasets=["kline"], symbols=["000001"]),
    )
    assert cursor == "2-0" and event["states"]["kline"] == ["000001"]
    assert all(call.kwargs["block"] > 0 for call in redis.xread.await_args_list)


def test_business_client_never_resubmits_a_pending_read():
    from src.services.market_data_client import DataNotReady, MarketDataClient

    transport = Mock(return_value=httpx.Response(202, json={"status": "refreshing"}))
    client = MarketDataClient(transport=httpx.MockTransport(transport))
    try:
        with pytest.raises(DataNotReady):
            client.source("news", {"symbol": "000001"}, wait=10)
        assert transport.call_count == 1
        assert json.loads(transport.call_args.args[0].content)["max_wait_seconds"] == 10
    finally:
        client.close()


def test_business_progress_uses_the_standard_sse_decoder():
    from src.services.market_data_client import MarketDataClient

    job = {
        "id": "job",
        "status": "running",
        "progress": 0,
        "total": 1,
        "message": "running",
    }
    done = {**job, "status": "success", "progress": 1}
    transport = Mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=f"event: job\ndata: {json.dumps(job)}\n\nevent: job\ndata: {json.dumps(done)}\n\n",
        )
    )
    client, progress = MarketDataClient(transport=httpx.MockTransport(transport)), []
    try:
        client.wait_job(
            job, on_progress=lambda current, total, message: progress.append(current)
        )
        assert progress == [0, 1] and transport.call_count == 1
        assert transport.call_args.args[0].url.path == "/v1/jobs/job/events"
    finally:
        client.close()
