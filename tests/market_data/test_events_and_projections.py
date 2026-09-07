"""Focused event/read-model tests; SQL query budgets are part of the contract."""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import event, func, select, update

from market_data_service.control_models import (
    ChangeOutbox,
    DatasetPolicy,
    DataState,
    SourceSubscription,
    SyncJob,
    utcnow,
)
from market_data_service.event_relay import drain
from market_data_service.projections import refresh_projections
from tests.market_data import test_service as fixtures

service = fixtures.service
seed = fixtures.seed


def flush(db):
    delivered = []
    while drain(db, publisher=delivered.append):
        pass
    return delivered


def pending(db):
    with db.get_session() as session:
        return session.scalar(
            select(func.count())
            .select_from(ChangeOutbox)
            .where(ChangeOutbox.dispatched_at.is_(None))
        )


def test_rolled_back_writes_never_escape_the_transactional_outbox(service):
    _, db, _, _ = service
    flush(db)
    with pytest.raises(RuntimeError):
        with db.session_scope() as session:
            session.add(DataState(dataset="kline", symbol="000001", status="failed"))
            session.flush()
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ChangeOutbox)
                    .where(ChangeOutbox.dispatched_at.is_(None))
                )
                == 1
            )
            raise RuntimeError("rollback")
    assert pending(db) == 0


def test_core_and_orm_writes_are_captured_but_leases_are_not(service):
    client, db, _, _ = service
    job = client.post("/v1/jobs", json={"dataset": "news"}).json()
    flush(db)
    with db.session_scope() as session:
        session.execute(
            update(SyncJob).where(SyncJob.id == job["id"]).values(lease_until=utcnow())
        )
    assert pending(db) == 0
    with db.session_scope() as session:
        session.execute(
            update(SyncJob).where(SyncJob.id == job["id"]).values(progress=1)
        )
    assert pending(db) == 1
    assert flush(db)[0]["jobs"][0]["progress"] == 1


def test_broker_failure_retains_replayable_changes(service):
    client, db, _, _ = service
    flush(db)
    client.post("/v1/jobs", json={"dataset": "news"})
    with pytest.raises(ConnectionError):
        drain(db, publisher=Mock(side_effect=ConnectionError("offline")))
    assert pending(db) > 0
    delivered = flush(db)
    assert delivered[0]["jobs"][0]["dataset"] == "news"
    assert pending(db) == 0


def test_overview_and_coverage_never_read_observation_bodies_or_market_tables(service):
    client, db, _, _ = service
    seed(service, "calendar", "securities", "financials")
    flush(db)
    statements = []

    def record(_connection, _cursor, statement, *_):
        statements.append(statement.lower())

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        for _ in range(3):
            assert client.get("/v1/datasets").status_code == 200
            response = client.get(
                "/v1/datasets/financials/coverage",
                params={"search": "000001", "page_size": 1},
            )
            assert response.json()["total"] == 1
            assert response.json()["items"][0]["symbol"] == "000001"
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert not any(
        "from md_data_state" in sql
        or "from stock_meta" in sql
        or "from md_observation" in sql
        for sql in statements
    )
    selects = [sql for sql in statements if sql.lstrip().startswith("select")]
    assert len(selects) <= 21  # 5 constant-size overview queries + count + page.
    assert (
        sum("limit" in sql and "md_coverage_projection" in sql for sql in selects) == 3
    )


def test_incremental_projection_only_rebuilds_the_changed_security(
    service, monkeypatch
):
    _, db, _, _ = service
    seed(service, "calendar", "securities", "financials")
    flush(db)
    import market_data_service.projections as projections

    build = Mock(wraps=projections._coverage_records)
    monkeypatch.setattr(projections, "_coverage_records", build)
    with db.session_scope() as session:
        session.execute(
            update(DataState)
            .where(DataState.dataset == "financials", DataState.symbol == "000001")
            .values(status="partial")
        )
    delivered = flush(db)
    assert build.call_count == 1
    assert build.call_args.args[1:3] == ("financials", {"000001"})
    assert delivered[0]["coverage"] == {"financials": ["000001"]}


def test_passage_of_time_expires_projection_without_a_browser_read(service):
    _, db, _, _ = service
    seed(service, "calendar", "securities", "financials")
    flush(db)
    now = utcnow()
    with db.session_scope() as session:
        policy = session.get(DatasetPolicy, "financials")
        policy.max_age_seconds = 30
    flush(db)
    payload = refresh_projections(db, now=now + timedelta(seconds=31))
    row = next(item for item in payload["datasets"] if item["id"] == "financials")
    assert row["stale"] == 3 and row["fresh"] == 0


def test_subscription_expiry_removes_coverage_without_loading_source_history(service):
    _, db, _, _ = service
    now = utcnow()
    with db.session_scope() as session:
        session.add(
            SourceSubscription(
                request_key="rss-test",
                operation="rss.read_feed",
                dataset="rss",
                arguments={},
                interval_seconds=30,
                last_requested_at=now,
            )
        )
    flush(db)
    payload = refresh_projections(db, now=now + timedelta(days=7, seconds=1))
    assert (
        next(item for item in payload["datasets"] if item["id"] == "rss")["total"] == 0
    )


def test_relay_batches_progress_without_putting_source_bodies_on_the_stream(service):
    client, db, _, _ = service
    flush(db)
    job = client.post("/v1/jobs", json={"dataset": "financials"}).json()
    for progress in range(5):
        with db.session_scope() as session:
            session.execute(
                update(SyncJob)
                .where(SyncJob.id == job["id"])
                .values(progress=progress + 1)
            )
    messages = flush(db)
    assert len(messages) == 1 and len(messages[0]["jobs"]) == 1
    assert messages[0]["jobs"][0]["progress"] == 5
    assert "payload" not in messages[0]


def test_event_matching_ignores_unrelated_symbols_and_health():
    from market_data_service.events import affects_data

    selector = {
        "datasets": ["kline"],
        "symbols": ["000001"],
        "request_keys": ["requested"],
    }
    assert not affects_data({"service": {}}, **selector)
    assert not affects_data({"states": {"kline": ["000002"]}}, **selector)
    assert affects_data({"states": {"kline": ["000001"]}}, **selector)
    assert affects_data({"observations": {"kline": ["requested"]}}, **selector)
    assert affects_data({"reset": True}, **selector)


def test_validates_stream_cursor_before_using_redis():
    from market_data_service.events import validate_cursor

    assert validate_cursor("123-0") == "123-0"
    for value in ("$", "abc", "1-2\nretry: 0", "1", "-1-0"):
        with pytest.raises(ValueError):
            validate_cursor(value)
