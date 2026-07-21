from __future__ import annotations

from unittest.mock import patch

from src.tools.get_domain_stock_candidates import get_domain_stock_candidates


def _theme_result(theme: str) -> dict:
    if theme == "减速器":
        items = [{
            "symbol": "688017",
            "name": "绿的谐波",
            "boards": ["减速器"],
            "evidence_level": "L1",
        }]
    elif theme == "人形机器人":
        items = [
            {"symbol": "300580", "name": "贝斯特", "boards": ["人形机器人"], "evidence_level": "L1"},
            {"symbol": "688017", "name": "绿的谐波", "boards": ["人形机器人"], "evidence_level": "L1"},
        ]
    else:
        items = [
            {
                "symbol": "300580",
                "name": "贝斯特",
                "boards": ["机器人执行器"],
                "evidence_level": "L1",
            },
            {
                "symbol": "688017",
                "name": "绿的谐波",
                "boards": ["机器人执行器"],
                "evidence_level": "L1",
            },
        ]
    return {
        "success": True,
        "partial": False,
        "coverage_complete": True,
        "theme": theme,
        "local_universe_count": 5879,
        "items": items,
        "matched_boards": [{"name": theme, "coverage": "full"}],
        "warnings": [],
        "errors": [],
    }


def test_multi_domain_tool_maps_every_domain_and_reuses_shared_board_fetch() -> None:
    calls: list[tuple[str, int]] = []

    def fetch(theme: str, limit: int, **_kwargs) -> dict:
        calls.append((theme, limit))
        return _theme_result(theme)

    with patch(
        "src.services.data_maintenance.ensure_stock_universe",
        return_value={"total": 5879},
    ), patch(
        "src.tools.get_domain_stock_candidates._load_local_universe",
        return_value={"300580": {}, "688017": {}},
    ), patch(
        "src.tools.get_domain_stock_candidates.get_theme_stock_candidates",
        side_effect=fetch,
    ):
        result = get_domain_stock_candidates(
            ["行星滚柱丝杠", "减速器", "无框力矩电机", "空心杯电机"],
            context_theme="人形机器人",
            limit_per_domain=300,
        )

    assert sorted(calls) == [("人形机器人", 300), ("减速器", 300), ("机器人执行器", 300)]
    assert [item["domain"] for item in result["domain_results"]] == [
        "行星滚柱丝杠", "减速器", "无框力矩电机", "空心杯电机",
    ]
    assert result["domain_results"][0]["lookup_themes"] == ["机器人执行器"]
    assert result["domain_results"][1]["mapping_basis"] == "exact_concept_board_intersected_with_context_theme"
    assert all(item["context_filter_applied"] for item in result["domain_results"])
    assert result["domain_results"][2]["lookup_themes"] == ["机器人执行器"]
    assert result["domain_results"][3]["lookup_themes"] == ["机器人执行器"]
    assert result["candidate_count"] == 2
    assert result["inferred_context_themes"] == ["人形机器人", "机器人概念"]
    robot_item = next(item for item in result["items"] if item["symbol"] == "300580")
    assert robot_item["matched_domains"] == ["行星滚柱丝杠", "无框力矩电机", "空心杯电机"]
    reducer_item = next(item for item in result["items"] if item["symbol"] == "688017")
    assert reducer_item["boards"] == ["机器人执行器", "减速器"]
    assert reducer_item["lookup_themes"] == ["机器人执行器", "减速器"]
    assert result["source_scope"] == "structured_concept_constituents_intersected_with_local_stock_meta"


def test_failed_domain_is_reported_without_inventing_candidates() -> None:
    with patch(
        "src.services.data_maintenance.ensure_stock_universe",
        return_value={"total": 5879},
    ), patch(
        "src.tools.get_domain_stock_candidates._load_local_universe",
        return_value={},
    ), patch(
        "src.tools.get_domain_stock_candidates.get_theme_stock_candidates",
        side_effect=RuntimeError("board unavailable"),
    ):
        result = get_domain_stock_candidates(["减速器"])

    assert result["success"] is False
    assert result["items"] == []
    assert result["domain_results"][0]["success"] is False
    assert "board unavailable" in result["domain_results"][0]["errors"][0]
