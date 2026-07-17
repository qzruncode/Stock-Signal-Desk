from __future__ import annotations

from unittest.mock import patch

from src.tools.get_multi_stock_decision_evidence import get_multi_stock_decision_evidence


def test_decision_evidence_collects_all_seven_dimensions_without_early_stop() -> None:
    resolved = [
        {"input": "甲公司", "name": "甲公司", "symbol": "000001"},
        {"input": "乙公司", "name": "乙公司", "symbol": "000002"},
    ]
    snapshot = {
        "success": True,
        "items": [
            {
                "symbol": entity["symbol"],
                "name": entity["name"],
                "technical": {"success": True, "indicators": {"return_20d_pct": -20}},
            }
            for entity in resolved
        ],
        "data_time": "2026-07-17",
        "quote_basis": "收盘快照",
        "warnings": [],
        "errors": [],
    }

    def caller_payload(dimension: str, code: str) -> dict:
        if dimension == "profile":
            return {"success": True, "main_business": f"{code}主营"}
        if dimension == "financials":
            return {
                "success": True,
                "items": [
                    {"report_date": f"202{year}-12-31", "parent_net_profit": -1, "debt_ratio": 20}
                    for year in range(2, 6)
                ],
            }
        if dimension == "business_segments":
            return {"success": True, "items": [{"segment_name": "核心产品"}]}
        if dimension == "valuation":
            return {"success": True, "pe_ttm": 100, "pb_mrq": 5}
        if dimension == "consensus":
            return {"success": True, "coverage_available": True, "estimates": []}
        if dimension == "peer_comparison":
            return {"success": True, "dimensions": {"roe": {"target": 5}}}
        if dimension in {"risk_events", "announcements"}:
            return {"success": True, "items": []}
        if dimension == "capital_flow":
            return {"success": True, "windows": {"10d": {"main_net_inflow": -1}}}
        raise AssertionError(dimension)

    callers = {
        dimension: (
            lambda code, _dimension=dimension, **kwargs: caller_payload(_dimension, code),
            {},
            lambda payload: payload,
        )
        for dimension in (
            "profile", "financials", "business_segments", "valuation", "consensus",
            "peer_comparison", "risk_events", "announcements", "capital_flow",
        )
    }

    with patch(
        "src.tools.get_multi_stock_decision_evidence.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch(
        "src.tools.get_multi_stock_decision_evidence.get_multi_stock_snapshot",
        return_value=snapshot,
    ), patch(
        "src.tools.get_multi_stock_decision_evidence._callers",
        return_value=callers,
    ):
        result = get_multi_stock_decision_evidence("甲公司,乙公司", thesis="测试产业")

    assert result["success"] is True
    assert result["total"] == 2
    assert all(item["evidence_coverage"]["complete_count"] == 7 for item in result["items"])
    assert all(item["evidence_coverage"]["complete"] for item in result["items"])
    # Negative profit and weak trend are flags, not early-stop conditions: all
    # later evidence dimensions must still be present.
    assert all("peer_comparison" in item and "announcements" in item for item in result["items"])
    assert all(item["screening_flags"]["negative"] for item in result["items"])


def test_decision_evidence_marks_a_failed_dimension_instead_of_dropping_it() -> None:
    resolved = [{"input": "甲公司", "name": "甲公司", "symbol": "000001"}]
    snapshot = {
        "success": True,
        "items": [{"symbol": "000001", "name": "甲公司", "technical": {"success": True}}],
        "warnings": [],
        "errors": [],
    }

    def fail(code: str, **kwargs) -> dict:
        raise RuntimeError("upstream unavailable")

    callers = {
        "profile": (fail, {}, lambda payload: payload),
    }
    with patch(
        "src.tools.get_multi_stock_decision_evidence.resolve_securities_csv",
        return_value=(resolved, []),
    ), patch(
        "src.tools.get_multi_stock_decision_evidence.get_multi_stock_snapshot",
        return_value=snapshot,
    ), patch(
        "src.tools.get_multi_stock_decision_evidence._callers",
        return_value=callers,
    ):
        result = get_multi_stock_decision_evidence("甲公司")

    item = result["items"][0]
    assert item["profile"]["success"] is False
    assert "RuntimeError" in item["profile"]["errors"][0]
    assert result["partial"] is True
    assert "business_reality" in item["evidence_coverage"]["missing"]
