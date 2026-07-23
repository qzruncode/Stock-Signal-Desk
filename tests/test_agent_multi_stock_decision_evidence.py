from __future__ import annotations

from unittest.mock import patch

from src.tools.get_multi_stock_decision_evidence import (
    _compact_announcements,
    _compact_risks,
    _coverage,
    get_multi_stock_decision_evidence,
)


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


def test_decision_evidence_treats_verified_zero_consensus_as_complete() -> None:
    item = {
        "profile": {"success": True},
        "business_segments": {"success": True},
        "financials": {"success": True, "items": [{}, {}, {}, {}]},
        "valuation": {"success": True, "pe_ttm": 50},
        "consensus": {
            "success": True,
            "coverage_available": False,
            "coverage_status": "no_sell_side_coverage",
            "source_query_complete": True,
        },
        "peer_comparison": {"success": True, "dimensions": {"roe": {}}},
        "snapshot": {"technical": {"success": True}},
        "capital_flow": {"success": True},
        "announcements": {"success": True},
        "risk_events": {"success": True},
    }

    coverage = _coverage(item)

    assert coverage["dimensions"]["expectations"] is True
    assert coverage["complete"] is True
    assert coverage["missing"] == []


def test_event_compactors_keep_long_timeline_and_lifecycle_evidence() -> None:
    announcements = _compact_announcements({
        "success": True,
        "items": [
            {
                "title": f"事项进展公告{index}",
                "notice_type": "再融资",
                "publish_date": f"2026-{(index % 12) + 1:02d}-01",
                "importance": "medium",
                "url": f"https://example.com/a{index}",
            }
            for index in range(25)
        ],
    })
    risks = _compact_risks({
        "success": True,
        "items": [{
            "title": "审核问询函",
            "date": "2026-04-01",
            "risk_summary": "交易所提出审核问题",
            "status": "detected",
            "severity": "medium",
            "lifecycle_basis": "occurrence_only_requires_chronological_resolution",
            "requires_fulltext_verification": True,
        }],
    })

    assert len(announcements["items"]) == 20
    assert risks["items"][0]["risk_summary"] == "交易所提出审核问题"
    assert risks["items"][0]["status"] == "detected"
    assert "chronological" in risks["items"][0]["lifecycle_basis"]
