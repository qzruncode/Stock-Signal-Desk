"""Source time is never synthesized from a transport timestamp."""

from unittest.mock import patch
from src.tools.get_realtime_quotes import get_realtime_quotes
from src.services.market_data_client import DataNotReady


def test_realtime_quote_never_promotes_transport_fetch_time_to_data_time():
    with (
        patch(
            "src.tools.get_realtime_quotes.resolve_local_symbol", return_value="600519"
        ),
        patch(
            "src.tools.get_realtime_quotes.read_source",
            side_effect=DataNotReady(
                {"status": "unknown", "_fetched_at": "2026-09-07T01:00:00Z"}
            ),
        ),
    ):
        result = get_realtime_quotes(["600519"])
    assert (
        not result["success"]
        and result["data_time"] is None
        and result["freshness_unknown"]
    )


def test_realtime_quote_preserves_provider_trade_time_as_source_time():
    source = {
        "success": True,
        "items": [{"code": "600519", "trade_time": "2026-09-07T15:00:00+08:00"}],
        "data_time": "2026-09-07T15:00:00+08:00",
        "data_service": {
            "version": "source-version",
            "checked_at": "2026-09-07T08:00:00Z",
        },
    }
    with (
        patch(
            "src.tools.get_realtime_quotes.resolve_local_symbol", return_value="600519"
        ),
        patch("src.tools.get_realtime_quotes.read_source", return_value=source),
    ):
        result = get_realtime_quotes(["600519"])
    assert (
        result["data_time"] == source["data_time"]
        and result["data_time_provenance"] == "source"
    )
    assert result["data_versions"]["600519"]["version"] == "source-version"
