"""Request windows are part of the independent service's immutable identity."""

from unittest.mock import patch
import pytest
from src.tools import _kline
from src.services.market_data_client import MarketDataError


@pytest.mark.parametrize("count", [20, 500, 5000])
def test_latest_read_forwards_count_without_local_cache(count):
    with (
        patch.object(_kline, "resolve_local_symbol", return_value="000001"),
        patch.object(
            _kline, "read_source", return_value={"data": [], "source": "sina"}
        ) as read,
        patch(
            "src.storage.DatabaseManager.get_instance",
            side_effect=AssertionError("no local cache"),
        ),
    ):
        _kline.get_kline("000001", count=count)
    read.assert_called_once_with("kline", {"symbol": "000001", "count": count})


def test_history_forwards_exact_requested_window():
    with (
        patch.object(_kline, "resolve_local_symbol", return_value="000001"),
        patch.object(_kline, "read_source") as read,
    ):
        _kline.get_history_data("000001", "2026-01-01", "2026-01-31")
    read.assert_called_once_with(
        "kline", {"symbol": "000001", "start_date": "20260101", "end_date": "20260131"}
    )


def test_service_outage_never_falls_back_to_business_sql():
    with (
        patch.object(_kline, "resolve_local_symbol", return_value="000001"),
        patch.object(_kline, "read_source", side_effect=MarketDataError("offline")),
        patch(
            "src.storage.DatabaseManager.get_instance",
            side_effect=AssertionError("local fallback forbidden"),
        ),
        pytest.raises(MarketDataError, match="offline"),
    ):
        _kline.get_kline("000001")


def test_service_identity_includes_range_count_and_source(service):
    from market_data_service.sources import identity

    a = {"symbol": "000001", "count": 20}
    assert (
        len(
            {
                identity("kline", a),
                identity("kline", {**a, "count": 21}),
                identity("kline", {**a, "source_id": "sina"}),
                identity("kline", {**a, "start_date": "20260101"}),
            }
        )
        == 4
    )


from tests.market_data.test_service import service as service
