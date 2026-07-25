"""Multi-domain discovery uses planner-bound live catalog identifiers only."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.get_domain_stock_candidates import get_domain_stock_candidates


def _theme_result(theme: str) -> dict:
    items_by_theme = {
        "机器人执行器": [
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
        ],
        "减速器": [{
            "symbol": "688017",
            "name": "绿的谐波",
            "boards": ["减速器"],
            "evidence_level": "L1",
        }],
    }
    return {
        "success": True,
        "partial": False,
        "coverage_complete": True,
        "theme": theme,
        "local_universe_count": 5879,
        "items": items_by_theme.get(theme, []),
        "matched_boards": [{
            "name": theme,
            "coverage": "full",
            "primary_theme": True,
        }],
        "warnings": [],
        "errors": [],
    }


def _patch_runtime(fetch):
    return (
        patch(
            "src.services.data_maintenance.ensure_stock_universe",
            return_value={"total": 5879},
        ),
        patch(
            "src.tools.get_domain_stock_candidates._load_local_universe",
            return_value={"300580": {}, "688017": {}},
        ),
        patch(
            "src.tools.get_domain_stock_candidates.get_theme_stock_candidates",
            side_effect=fetch,
        ),
    )


def test_multi_domain_tool_fetches_each_catalog_board_once_and_merges_union() -> None:
    calls: list[str] = []

    def fetch(theme: str, **_kwargs) -> dict:
        calls.append(theme)
        return _theme_result(theme)

    maintenance, universe, candidate_fetch = _patch_runtime(fetch)
    with maintenance, universe, candidate_fetch:
        result = get_domain_stock_candidates([
            {
                "label": "行星滚柱丝杠",
                "board_queries": ["机器人执行器"],
                "mapping_type": "catalog_binding",
                "rationale": "模型从本轮实时目录绑定",
                "unresolved_parts": [],
            },
            {
                "label": "减速器",
                "board_queries": ["减速器"],
                "mapping_type": "catalog_binding",
                "rationale": "模型从本轮实时目录绑定",
                "unresolved_parts": [],
            },
            {
                "label": "空心杯电机",
                "board_queries": ["机器人执行器"],
                "mapping_type": "catalog_binding",
                "rationale": "模型从本轮实时目录绑定",
                "unresolved_parts": [],
            },
        ])

    assert sorted(calls) == ["减速器", "机器人执行器"]
    assert [item["domain"] for item in result["domain_results"]] == [
        "行星滚柱丝杠",
        "减速器",
        "空心杯电机",
    ]
    assert result["candidate_count"] == 2
    green = next(
        item for item in result["items"] if item["symbol"] == "688017"
    )
    assert green["matched_domains"] == [
        "行星滚柱丝杠",
        "减速器",
        "空心杯电机",
    ]
    assert green["lookup_themes"] == ["机器人执行器", "减速器"]


def test_unresolved_domain_does_not_guess_or_fetch_a_proxy_board() -> None:
    calls: list[str] = []

    def fetch(theme: str, **_kwargs) -> dict:
        calls.append(theme)
        return _theme_result(theme)

    maintenance, universe, candidate_fetch = _patch_runtime(fetch)
    with maintenance, universe, candidate_fetch:
        result = get_domain_stock_candidates([{
            "label": "不存在于目录的精密部件",
            "board_queries": [],
            "mapping_type": "unresolved",
            "rationale": "本轮实时目录中没有可验证板块",
            "unresolved_parts": ["不存在于目录的精密部件"],
        }])

    assert calls == []
    assert result["success"] is False
    assert result["items"] == []
    domain = result["domain_results"][0]
    assert domain["mapping_type"] == "unresolved"
    assert domain["lookup_themes"] == []
    assert domain["candidate_count"] == 0


def test_incomplete_exact_board_fetch_is_rejected_without_fallback() -> None:
    calls: list[str] = []

    def fetch(theme: str, **_kwargs) -> dict:
        calls.append(theme)
        return {
            **_theme_result(theme),
            "success": True,
            "partial": True,
            "coverage_complete": False,
            "items": [{
                "symbol": "002459",
                "name": "晶澳科技",
                "boards": ["其他板块"],
            }],
            "matched_boards": [{
                "name": "其他板块",
                "coverage": "partial_pages",
            }],
            "warnings": ["精确板块未完成全量抓取"],
        }

    maintenance, universe, candidate_fetch = _patch_runtime(fetch)
    with maintenance, universe, candidate_fetch:
        result = get_domain_stock_candidates([{
            "label": "电池",
            "board_queries": ["电池"],
            "mapping_type": "catalog_binding",
            "rationale": "模型从本轮实时目录绑定",
            "unresolved_parts": [],
        }])

    assert calls == ["电池"]
    assert result["success"] is False
    assert result["items"] == []
    assert result["domain_results"][0]["rejected_boards"][0]["name"] == "其他板块"


def test_plain_string_internal_call_is_rejected_by_typed_contract() -> None:
    with pytest.raises(Exception):
        get_domain_stock_candidates(["减速器"])
