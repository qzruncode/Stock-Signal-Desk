"""A range read cannot be certified from unrelated or mixed-version daily rows."""

from datetime import date, timedelta
from sqlalchemy import select
from tests.market_data.test_service import service as service, seed


def test_daily_archive_without_observation_does_not_certify_a_range(service):
    client, db, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.models import StockDaily

    with db.session_scope() as session:
        session.add(
            StockDaily(
                code="000001",
                date=date(2026, 1, 5),
                open=10,
                close=10,
                high=11,
                low=9,
                volume=100,
            )
        )
    response = client.post(
        "/v1/snapshots",
        json={
            "symbols": ["000001"],
            "datasets": ["kline"],
            "start_date": "2026-01-05",
            "end_date": "2026-01-07",
        },
    )
    assert response.status_code == 202 and response.json()["not_ready"]


def test_explicit_historical_window_preserves_version_and_is_not_today(service):
    client, db, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.jobs import publish
    from market_data_service.control_models import utcnow

    args = {"symbol": "000001", "start_date": "20260105", "end_date": "20260107"}
    payload = {
        "success": True,
        "data": [
            {
                "date": "2026-01-07",
                "open": 10,
                "close": 10,
                "high": 11,
                "low": 9,
                "volume": 100,
            }
        ],
        "source": "sina",
        "data_time": "2026-01-07",
        "is_stale": False,
    }
    with db.session_scope() as session:
        version = publish(session, "kline", "000001", payload, utcnow(), arguments=args)
    result = client.post(
        "/v1/observations", json={"operation": "kline", "arguments": args}
    )
    assert (
        result.status_code == 200
        and result.json()["data_service"]["version"] == version
    )
    assert result.json()["data_time"] == "2026-01-07"
    today = client.post(
        "/v1/snapshots", json={"symbols": ["000001"], "datasets": ["kline"], "count": 1}
    )
    assert today.status_code == 202


def test_expired_exchange_calendar_cannot_certify_current_bars(service):
    _, db, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.calendar import trade_dates, _calendar_bucket
    from market_data_service.control_models import DataState, utcnow
    import pytest

    with db.session_scope() as session:
        state = session.scalar(select(DataState).where(DataState.dataset == "calendar"))
        state.last_success_at = utcnow() - timedelta(days=20)
    _calendar_bucket.cache_clear()
    with pytest.raises(RuntimeError, match="过期"):
        trade_dates()
