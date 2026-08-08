from __future__ import annotations

from src.tools.get_realtime_quotes import _build_response


def test_realtime_quote_never_promotes_transport_fetch_time_to_data_time() -> None:
    result = _build_response(
        [
            {
                "code": "600519",
                "name": "贵州茅台",
                "source": "eastmoney_push",
                "_fetched_at": "2026-08-08T01:30:00+08:00",
            }
        ],
        requested=["600519"],
        trading=False,
    )

    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert result["freshness_unknown"] is True
    assert "_fetched_at" in result["data_time_note"]


def test_realtime_quote_preserves_provider_trade_time_as_source_time() -> None:
    result = _build_response(
        [
            {
                "code": "600519",
                "name": "贵州茅台",
                "source": "eastmoney_push",
                "trade_time": "2026-08-07T15:00:00+08:00",
                "_fetched_at": "2026-08-08T01:30:00+08:00",
            }
        ],
        requested=["600519"],
        trading=False,
    )

    assert result["data_time"] == "2026-08-07T15:00:00+08:00"
    assert result["data_time_provenance"] == "source"
    assert result["data_time_note"] is None
