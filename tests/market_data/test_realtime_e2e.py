"""Real PostgreSQL notifications, Redis replay, HTTP waits and business SSE proxy."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from httpx_sse import connect_sse
from sqlalchemy import update

from tests.market_data import test_e2e as fixtures

stack = fixtures.stack
control = fixtures.control
terminal = fixtures.terminal

pytestmark = pytest.mark.skipif(
    os.getenv("MARKET_DATA_E2E") != "1", reason="Isolated real stack required"
)


def read_change(client, cursor, predicate, ready=None):
    with connect_sse(
        client, "GET", "/api/v1/data-service/events", params={"after": cursor}
    ) as stream:
        stream.response.raise_for_status()
        for event in stream.iter_sse():
            if event.event == "ready" and ready:
                ready.set()
            if event.event == "change" and predicate(event.json()):
                return event.id, event.json()
    raise AssertionError("Stream ended before the expected committed change")


def test_committed_policy_changes_reach_two_subscribers_without_overview_refresh(stack):
    data, business, _, _ = stack
    old = next(
        item["policy"]
        for item in data.get("/v1/datasets").json()["items"]
        if item["id"] == "financials"
    )
    cursor = business.get("/api/v1/data-service/datasets").json()["event_cursor"]
    ready = [threading.Event(), threading.Event()]
    predicate = lambda payload: any(
        item["id"] == "financials" and item["policy"]["enabled"] is not old["enabled"]
        for item in payload["datasets"]
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(read_change, business, cursor, predicate, signal)
                for signal in ready
            ]
            assert all(signal.wait(5) for signal in ready)
            response = data.put(
                "/v1/datasets/financials/policy",
                json={
                    **{
                        key: old[key] for key in ("interval_seconds", "max_age_seconds")
                    },
                    "enabled": not old["enabled"],
                },
            )
            assert response.status_code == 200
            results = [future.result(timeout=8) for future in futures]
        assert results[0][0] == results[1][0]
        assert len(results[0][1]["datasets"]) == 1
    finally:
        data.put(
            "/v1/datasets/financials/policy",
            json={
                key: old[key]
                for key in ("enabled", "interval_seconds", "max_age_seconds")
            },
        )


@pytest.mark.parametrize("cursor_transport", ["header", "query"])
def test_last_event_id_replays_a_change_committed_while_disconnected(
    stack, cursor_transport
):
    data, business, _, _ = stack
    cursor = business.get("/api/v1/data-service/datasets").json()["event_cursor"]
    job = data.post(
        "/v1/jobs", json={"dataset": "news", "symbols": ["000028"], "mode": "all"}
    ).json()
    terminal(data, job["id"])
    with connect_sse(
        business,
        "GET",
        "/api/v1/data-service/events",
        headers={"Last-Event-ID": cursor} if cursor_transport == "header" else {},
        params={
            "after": "0-0",
            "lastEventId": cursor if cursor_transport == "query" else "0-0",
        },
    ) as stream:
        for event in stream.iter_sse():
            if event.event == "change" and any(
                item["id"] == job["id"] for item in event.json()["jobs"]
            ):
                assert event.id != cursor
                return
    raise AssertionError("No durable replay")


def test_trimmed_stream_requests_a_snapshot_reset(stack):
    _, business, _, _ = stack
    with connect_sse(
        business, "GET", "/api/v1/data-service/events", params={"after": "1-0"}
    ) as stream:
        first = next(stream.iter_sse())
        assert first.event == "reset" and first.id != "1-0"


def test_source_read_waits_for_a_real_event_with_one_http_request(stack):
    _, _, db, env = stack
    from src.services.market_data_client import MarketDataClient

    path = Path(env["MARKET_DATA_FIXTURE_PATH"])
    original = path.read_text()
    fixture = json.loads(original)
    fixture["news"]["000030"]["delay_seconds"] = 2
    path.write_text(json.dumps(fixture))
    from market_data_service.control_models import (
        Observation,
        SourceSubscription,
        utcnow,
    )
    from market_data_service.sources import identity

    key = identity("news", {"symbol": "000030"})
    # Deliberately expire only this isolated fixture so reruns exercise a cold
    # event wait too, rather than accidentally accepting a warm-cache read.
    with db.session_scope() as session:
        session.execute(
            update(Observation)
            .where(Observation.request_key == key)
            .values(fetched_at=utcnow() - timedelta(days=1))
        )
        session.execute(
            update(SourceSubscription)
            .where(SourceSubscription.request_key == key)
            .values(next_run_at=utcnow())
        )
    requests = []
    client = MarketDataClient()
    client.http.event_hooks["request"].append(
        lambda request: requests.append(request.url.path)
    )
    try:
        started = time.monotonic()
        payload = client.source("news", {"symbol": "000030"}, wait=12)
        assert payload["data_service"]["status"] == "fresh"
        assert requests == ["/v1/observations"]
        assert 1.8 <= time.monotonic() - started < 12
    finally:
        client.close()
        path.write_text(original)


def test_job_progress_uses_one_sse_connection_not_repeated_detail_reads(stack):
    data, _, _, _ = stack
    from src.services.market_data_client import MarketDataClient

    job = data.post(
        "/v1/jobs", json={"dataset": "news", "symbols": ["000029"], "mode": "all"}
    ).json()
    seen, progress = [], []
    client = MarketDataClient()
    client.http.event_hooks["request"].append(
        lambda request: seen.append(request.url.path)
    )
    try:
        client.wait_job(
            job,
            timeout=15,
            on_progress=lambda done, total, message: progress.append(done),
        )
        assert seen == [f"/v1/jobs/{job['id']}/events"]
        assert progress and progress[-1] == 1
    finally:
        client.close()


def test_relay_restart_delivers_commits_that_happened_during_its_outage(stack):
    data, business, db, _ = stack
    from market_data_service.control_models import DatasetPolicy

    cursor = business.get("/api/v1/data-service/datasets").json()["event_cursor"]
    with db.get_session() as session:
        original = session.get(DatasetPolicy, "rss").max_age_seconds
    control("stop", "e2e-events")
    try:
        with db.session_scope() as session:
            session.execute(
                update(DatasetPolicy)
                .where(DatasetPolicy.dataset == "rss")
                .values(max_age_seconds=original + 30)
            )
    finally:
        control("start", "e2e-events")
    try:
        _, payload = read_change(
            business,
            cursor,
            lambda payload: any(
                item["id"] == "rss"
                and item["policy"]["max_age_seconds"] == original + 30
                for item in payload["datasets"]
            ),
        )
        assert payload["datasets"]
    finally:
        with db.session_scope() as session:
            session.execute(
                update(DatasetPolicy)
                .where(DatasetPolicy.dataset == "rss")
                .values(max_age_seconds=original)
            )
