from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import json

import pandas as pd
import pytest
from sqlalchemy import select

from tests.market_data.test_service import service as service, seed


def test_missing_quote_time_is_never_promoted_from_transport_time(service):
    client, database, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.jobs import publish
    from market_data_service.control_models import utcnow
    from market_data_service.sources import latest_observation, observation_payload

    arguments = {"symbol": "000001"}
    with database.session_scope() as session:
        publish(
            session,
            "quotes",
            "000001",
            {
                "success": True,
                "items": [{"price": 10, "_fetched_at": utcnow().isoformat()}],
            },
            utcnow(),
            arguments=arguments,
        )
    observation = latest_observation("quotes", arguments)
    assert observation_payload(observation) is None
    historical = observation_payload(observation, allow_stale=True)
    assert historical["data_time"] is None
    assert (
        historical["freshness_unknown"]
        and historical["data_service"]["status"] == "unknown"
    )


def test_non_temporal_consensus_observation_is_not_blocked_by_freshness_gate(service):
    _, database, _, _ = service
    from market_data_service.control_models import utcnow
    from market_data_service.jobs import publish
    from market_data_service.sources import latest_observation, observation_payload

    arguments = {"symbol": "000001", "metric": "net_profit"}
    with database.session_scope() as session:
        publish(
            session,
            "financials",
            "000001",
            {
                "success": True,
                "estimates": [{"year": 2026, "mean": 10}],
                "data_time": None,
                "data_time_applicable": False,
                "freshness_unknown": True,
            },
            utcnow(),
            operation="get_consensus_estimates.read_consensus_metric_ths",
            arguments=arguments,
        )

    observation = latest_observation(
        "get_consensus_estimates.read_consensus_metric_ths", arguments
    )
    payload = observation_payload(observation)
    assert payload is not None
    assert payload["data_service"]["status"] == "fresh"


def test_one_immutable_kline_window_serves_smaller_reads_without_refetch(service):
    client, _, data, fixture = service
    seed(service, "calendar", "securities")
    from market_data_service.calendar import latest_completed_trade_day

    last = latest_completed_trade_day().isoformat()
    data["kline"]["000001"]["data"] = [
        {"date": last, "open": 10, "close": 11, "high": 12, "low": 9, "volume": 10}
    ]
    data["kline"]["000001"].update(data_time=last, requested_count=1)
    fixture.write_text(json.dumps(data))
    seed(service, "kline")
    good = client.post(
        "/v1/snapshots", json={"symbols": ["000001"], "datasets": ["kline"], "count": 1}
    )
    assert good.status_code == 200
    partial = client.post(
        "/v1/snapshots",
        json={"symbols": ["000001"], "datasets": ["kline"], "count": 120},
    )
    assert (
        partial.status_code == 202
        and partial.json()["freshness"]["000001"]["kline"] == "partial"
    )


def test_request_ranges_types_and_closed_source_choices_are_validated_before_work(
    service,
):
    client, _, _, _ = service
    for arguments in [
        {"symbol": "000001", "count": 0},
        {"symbol": "000001", "count": "bad"},
        {"symbol": "000001", "source_id": "unregistered"},
        {"symbol": "000001", "surprise": True},
        {"symbol": "000001", "start_date": "2026-02-31"},
        {"symbol": "000001", "start_date": "2026-09-01", "end_date": "2026-01-01"},
    ]:
        assert (
            client.post(
                "/v1/observations", json={"operation": "kline", "arguments": arguments}
            ).status_code
            == 422
        )
    for payload in [
        {"symbols": ["bad"]},
        {"symbols": ["000001"], "start_date": "2026-02-31"},
        {"symbols": ["000001"], "start_date": "2026-02-02", "end_date": "2026-01-01"},
    ]:
        assert client.post("/v1/snapshots", json=payload).status_code == 422


def test_security_endpoint_does_not_leak_unvalidated_financial_values(service):
    client, _, _, _ = service
    seed(service, "securities", "financials")
    assert "revenue_ttm" not in client.get("/v1/securities").json()["items"][0]
    snapshot = client.post(
        "/v1/snapshots", json={"symbols": ["000001"], "datasets": ["securities"]}
    ).json()
    assert snapshot["items"]["000001"]["financials"] is None


@pytest.mark.parametrize(
    "source,volume,amount",
    [("eastmoney", 10000, 100000), ("sina", 100, 100000), ("tencent", 10000, None)],
)
def test_native_provider_volume_units_and_amount_are_not_confused(
    service, monkeypatch, source, volume, amount
):
    seed(service, "calendar", "securities")
    from market_data_service.calendar import latest_completed_trade_day
    from market_data_service.providers.live import LiveProvider
    import akshare as ak

    last = latest_completed_trade_day().isoformat()
    em = pd.DataFrame(
        [
            {
                "日期": last,
                "开盘": 10,
                "收盘": 11,
                "最高": 12,
                "最低": 9,
                "成交量": 100,
                "成交额": 100000,
            }
        ]
    )
    generic = pd.DataFrame(
        [
            {
                "date": last,
                "open": 10,
                "close": 11,
                "high": 12,
                "low": 9,
                "volume": 100,
                "amount": 100000,
            }
        ]
    )
    tx = generic.drop(columns=["volume"]).assign(amount=100)
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **_: em)
    monkeypatch.setattr(ak, "stock_zh_a_daily", lambda **_: generic)
    monkeypatch.setattr(ak, "stock_zh_a_hist_tx", lambda **_: tx)
    monkeypatch.setattr(
        "market_data_service.data_provider.rate_limiter.akshare_rate_limiter.wait",
        lambda: None,
    )
    result = LiveProvider().kline(
        "000001", count=1, source_id=source, allow_fallback=False
    )
    assert result["data"][0]["volume"] == volume
    assert result["data"][0].get("amount") == amount
    assert result["bar_complete"] and not result["is_stale"]


def test_provider_fallback_skips_successful_but_stale_bars(service, monkeypatch):
    seed(service, "calendar", "securities")
    from market_data_service.calendar import latest_completed_trade_day
    from market_data_service.providers.live import LiveProvider
    import akshare as ak

    last = latest_completed_trade_day()
    stale = {
        "日期": (last - timedelta(days=10)).isoformat(),
        "开盘": 10,
        "收盘": 11,
        "最高": 12,
        "最低": 9,
        "成交量": 100,
    }
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **_: pd.DataFrame([stale]))
    monkeypatch.setattr(
        ak,
        "stock_zh_a_daily",
        lambda **_: pd.DataFrame(
            [
                {
                    "date": last,
                    "open": 10,
                    "close": 11,
                    "high": 12,
                    "low": 9,
                    "volume": 100,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "market_data_service.data_provider.rate_limiter.akshare_rate_limiter.wait",
        lambda: None,
    )
    result = LiveProvider().kline("000001", count=1)
    assert (
        result["source"] == "sina"
        and result["fallback_used"]
        and result["source_attempts"]
    )


def test_short_same_day_query_preserves_complete_canonical_window(service):
    _, database, _, _ = service
    seed(service, "calendar", "securities")
    from market_data_service.jobs import publish
    from market_data_service.control_models import DataState, utcnow
    from market_data_service.calendar import latest_completed_trade_day

    last = latest_completed_trade_day()
    bars = [
        {
            "date": (last - timedelta(days=i)).isoformat(),
            "open": 10,
            "close": 11,
            "high": 12,
            "low": 9,
        }
        for i in (2, 1, 0)
    ]
    with database.session_scope() as session:
        full = publish(
            session,
            "kline",
            "000001",
            {"success": True, "data": bars, "data_time": last.isoformat()},
            utcnow(),
        )
    with database.session_scope() as session:
        short = publish(
            session,
            "kline",
            "000001",
            {"success": True, "data": bars[-1:], "data_time": last.isoformat()},
            utcnow(),
            arguments={"symbol": "000001", "count": 1},
        )
    with database.get_session() as session:
        state = session.scalar(
            select(DataState).where(
                DataState.dataset == "kline", DataState.symbol == "000001"
            )
        )
        assert state.version == full and short != full


def test_source_task_refreshes_at_interval_before_cache_expires(service):
    from market_data_service.worker import source_task

    with patch("market_data_service.sources.refresh_source") as refresh:
        source_task.run("scheduled-request-key")
    refresh.assert_called_once_with("scheduled-request-key", force=True)


def test_refresh_context_is_propagated_into_native_provider_threads(monkeypatch):
    from market_data_service.providers import financials
    from market_data_service.providers.common import force_source_read

    seen = []

    def core(*args, **kwargs):
        seen.append(force_source_read.get())
        return [{"report_date": "2026-06-30", "revenue": 120}]

    def bundle(*args, **kwargs):
        seen.append(force_source_read.get())
        return {"success": True}

    monkeypatch.setattr(financials, "fetch_core_indicators", core)
    monkeypatch.setattr(financials, "get_financial_bundle", bundle)
    token = force_source_read.set(True)
    try:
        assert financials._build("000001", 2)["success"]
    finally:
        force_source_read.reset(token)
    assert seen == [True, True] and not force_source_read.get()


def test_every_closed_operation_exposes_a_native_validation_schema(service):
    from market_data_service.sources import registry
    from market_data_service.contracts import operation_schema

    for operation in registry():
        schema = operation_schema(operation).model_json_schema()
        assert schema["type"] == "object" and schema["additionalProperties"] is False


def test_rss_subject_link_uses_the_normalized_source_id():
    from market_data_service.providers.gelonghui_subjects import _normalize_subject

    assert (
        _normalize_subject({"subjectId": 123, "name": "主题"})["link"]
        == "https://www.gelonghui.com/subject/123"
    )


def test_completed_day_still_requires_a_recent_source_check(service):
    seed(service, "calendar", "securities")
    from market_data_service.freshness import state_status
    from market_data_service.calendar import latest_completed_trade_day
    from market_data_service.control_models import utcnow

    now = utcnow()
    state = SimpleNamespace(
        dataset="kline",
        status="ready",
        last_success_at=now - timedelta(days=3),
        data_time=latest_completed_trade_day().isoformat(),
        error=None,
    )
    assert (
        state_status(state, SimpleNamespace(max_age_seconds=86400), now=now) == "stale"
    )


@pytest.mark.parametrize(
    "hour,quote_time,expected",
    [
        (4, "2026-09-07T10:00:00+08:00", "stale"),
        (4, "2026-09-07T11:30:00+08:00", "fresh"),
        (8, "2026-09-07T14:00:00+08:00", "stale"),
        (8, "2026-09-07T15:00:00+08:00", "fresh"),
        (8, "2026-09-07T07:00:00Z", "fresh"),
    ],
)
def test_quotes_after_session_end_require_session_closing_time(
    service, hour, quote_time, expected
):
    seed(service, "calendar", "securities")
    from market_data_service.freshness import state_status

    now = datetime(2026, 9, 7, hour)
    state = SimpleNamespace(
        dataset="quotes",
        status="ready",
        last_success_at=now,
        data_time=quote_time,
        error=None,
    )
    assert (
        state_status(state, SimpleNamespace(max_age_seconds=120), now=now) == expected
    )
