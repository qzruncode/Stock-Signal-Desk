"""Provider normalization and native, atomic data-service persistence."""

from datetime import timedelta
import pandas as pd
import pytest
from sqlalchemy import select
from tests.market_data.test_service import service as service, seed
from market_data_service.providers.common import bare_symbol, exchange_prefix


@pytest.mark.parametrize(
    "value,code,prefix",
    [
        ("sh600519", "600519", "SH600519"),
        ("sz000001", "000001", "SZ000001"),
        ("bj830799", "830799", "BJ830799"),
        ("920099", "920099", "BJ920099"),
    ],
)
def test_exchange_normalization(value, code, prefix):
    assert bare_symbol(value) == code and exchange_prefix(value) == prefix


def test_beijing_primary_is_sina_not_a_failed_eastmoney_fallback(service, monkeypatch):
    seed(service, "calendar", "securities")
    from market_data_service.calendar import latest_completed_trade_day
    from market_data_service.providers.live import LiveProvider
    import akshare as ak

    day = latest_completed_trade_day().isoformat()
    frame = pd.DataFrame(
        [
            {
                "date": day,
                "open": 10,
                "close": 11,
                "high": 12,
                "low": 9,
                "volume": 100,
                "turnover": 0.01,
            }
        ]
    )
    monkeypatch.setattr(
        ak,
        "stock_zh_a_hist",
        lambda **k: pytest.fail("Beijing must skip unsupported source"),
    )
    monkeypatch.setattr(ak, "stock_zh_a_daily", lambda **k: frame)
    monkeypatch.setattr(
        "market_data_service.data_provider.rate_limiter.akshare_rate_limiter.wait",
        lambda: None,
    )
    result = LiveProvider().kline("830799", count=1)
    assert result["source"] == "sina" and not result["fallback_used"]
    assert (
        result["data"][0]["turnover_rate"] == 0.01
        and result["data"][0]["pct_chg"] is None
    )


def test_missing_amount_does_not_destroy_existing_amount_and_new_version_stays_honest(
    service,
):
    _, db, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.jobs import publish
    from market_data_service.control_models import utcnow, Observation
    from market_data_service.models import StockDaily

    now = utcnow()
    bar = {
        "date": "2026-01-05",
        "open": 10,
        "close": 11,
        "high": 12,
        "low": 9,
        "volume": 100,
        "amount": 1000,
    }
    with db.session_scope() as session:
        publish(
            session,
            "kline",
            "000001",
            {"data": [bar], "source": "sina", "data_time": bar["date"]},
            now,
        )
    with db.session_scope() as session:
        version = publish(
            session,
            "kline",
            "000001",
            {
                "data": [{**bar, "volume": 200, "amount": None}],
                "source": "tencent",
                "data_time": bar["date"],
            },
            now + timedelta(seconds=1),
        )
    with db.get_session() as session:
        row = session.scalar(select(StockDaily).where(StockDaily.code == "000001"))
        assert row.amount == 1000 and row.volume == 200
        assert session.get(Observation, version).payload["data"][0]["amount"] is None


def test_historical_publish_cannot_move_current_watermark_backwards(service):
    _, db, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.jobs import publish
    from market_data_service.control_models import utcnow, DataState

    now = utcnow()

    def payload(day):
        return {
            "data": [
                {
                    "date": day,
                    "open": 10,
                    "close": 11,
                    "high": 12,
                    "low": 9,
                    "volume": 100,
                }
            ],
            "source": "sina",
            "data_time": day,
        }

    with db.session_scope() as session:
        current = publish(session, "kline", "000001", payload("2026-09-04"), now)
    with db.session_scope() as session:
        publish(
            session,
            "kline",
            "000001",
            payload("2026-01-05"),
            now + timedelta(seconds=1),
        )
    with db.get_session() as session:
        state = session.scalar(
            select(DataState).where(
                DataState.dataset == "kline", DataState.symbol == "000001"
            )
        )
        assert state.data_time == "2026-09-04" and state.version == current
