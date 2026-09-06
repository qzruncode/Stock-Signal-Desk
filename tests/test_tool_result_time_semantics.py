from __future__ import annotations

from src.tools.base import enforce_result_contract


def test_missing_source_time_cannot_be_marked_fresh_from_transport_completion() -> None:
    result = enforce_result_contract(
        "read_example",
        {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "data_time": None,
            "freshness_unknown": False,
            "is_stale": None,
            "_fetched_at": "2026-08-08T00:34:36+08:00",
        },
    )

    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert result["freshness_unknown"] is True
    assert "原始数据时间" in result["data_time_note"]


def test_inferred_time_is_explicitly_distinguished_from_source_time() -> None:
    result = enforce_result_contract(
        "read_example",
        {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "data_time": "2026-08-07",
            "data_time_inferred": True,
            "freshness_unknown": True,
            "is_stale": None,
        },
    )

    assert result["data_time_provenance"] == "inferred"


def test_malformed_source_time_is_downgraded_without_rejecting_the_payload() -> None:
    result = enforce_result_contract(
        "read_example",
        {
            "success": True,
            "partial": False,
            "errors": [],
            "warnings": [],
            "data_time": "2024-07-25 2024-07-25 20:06:12",
            "data_time_provenance": "source",
            "freshness_unknown": False,
            "is_stale": False,
        },
    )

    assert result["success"] is True
    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert result["freshness_unknown"] is True
    assert result["is_stale"] is None
    assert any("无法解析" in warning for warning in result["warnings"])
    assert "无法解析" in result["data_time_note"]
