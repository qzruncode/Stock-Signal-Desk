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
            [
                {
                    "label": label,
                    "board_queries": [board],
                    "mapping_type": "exact_board" if label == board else "proxy_board",
                    "rationale": "对照当前板块目录完成的测试映射",
                    "unresolved_parts": [],
                }
                for label, board in (
                    ("行星滚柱丝杠", "机器人执行器"),
                    ("减速器", "减速器"),
                    ("无框力矩电机", "机器人执行器"),
                    ("空心杯电机", "机器人执行器"),
                )
            ],
            context_theme="人形机器人",
            limit_per_domain=300,
        )

    assert sorted(calls) == [("人形机器人", 1000), ("减速器", 1000), ("机器人执行器", 1000)]
    assert [item["domain"] for item in result["domain_results"]] == [
        "行星滚柱丝杠", "减速器", "无框力矩电机", "空心杯电机",
    ]
    assert result["domain_results"][0]["lookup_themes"] == ["机器人执行器"]
    assert result["domain_results"][1]["mapping_basis"] == "catalog_exact_board_intersected_with_context_theme"
    assert all(item["context_filter_applied"] for item in result["domain_results"])
    assert result["domain_results"][2]["lookup_themes"] == ["机器人执行器"]
    assert result["domain_results"][3]["lookup_themes"] == ["机器人执行器"]
    assert result["candidate_count"] == 2
    assert result["inferred_context_themes"] == ["人形机器人"]
    robot_item = next(item for item in result["items"] if item["symbol"] == "300580")
    assert robot_item["matched_domains"] == ["行星滚柱丝杠", "无框力矩电机", "空心杯电机"]
    reducer_item = next(item for item in result["items"] if item["symbol"] == "688017")
    assert reducer_item["boards"] == ["机器人执行器", "减速器"]
    assert reducer_item["lookup_themes"] == ["机器人执行器", "减速器"]
    assert result["source_scope"] == (
        "structured_concept_constituents_intersected_with_context_theme_and_local_stock_meta"
    )


def test_compound_domains_use_validated_catalog_proxies_without_whole_domain_loss() -> None:
    calls: list[str] = []

    def fetch(theme: str, _limit: int, **_kwargs) -> dict:
        calls.append(theme)
        return _theme_result(theme)

    domains = [
        {
            "label": "灵巧手及力控部件",
            "board_queries": ["机器人执行器"],
            "mapping_type": "proxy_board",
            "rationale": "当前目录无同名板块，使用最窄的机器人执行机构板块召回。",
            "unresolved_parts": [],
        },
        {
            "label": "电机（伺服电机/步进电机）",
            "board_queries": ["机器人执行器"],
            "mapping_type": "proxy_board",
            "rationale": "机器人关节驱动电机归入最窄执行器板块。",
            "unresolved_parts": [],
        },
    ]
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
        result = get_domain_stock_candidates(domains, context_theme="人形机器人")

    assert sorted(calls) == ["人形机器人", "机器人执行器"]
    assert result["success"] is True
    assert result["partial"] is False
    assert [item["candidate_count"] for item in result["domain_results"]] == [2, 2]
    assert all(item["mapping_type"] == "proxy_board" for item in result["domain_results"])
    assert all(item["lookup_themes"] == ["机器人执行器"] for item in result["domain_results"])
    assert result["candidate_count"] == 2


def test_proxy_guard_rejects_cross_application_motor_boards_before_fetch() -> None:
    calls: list[str] = []

    def fetch(theme: str, _limit: int, **_kwargs) -> dict:
        calls.append(theme)
        return _theme_result(theme)

    spec = {
        "label": "电机（伺服电机/步进电机）",
        "board_queries": ["机器人执行器", "轮毂电机", "同步磁阻电机"],
        "mapping_type": "proxy_board",
        "rationale": "模型候选映射",
        "unresolved_parts": [],
    }
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
        result = get_domain_stock_candidates([spec], context_theme="人形机器人")

    assert sorted(calls) == ["人形机器人", "机器人执行器"]
    domain = result["domain_results"][0]
    assert domain["lookup_themes"] == ["机器人执行器"]
    assert domain["candidate_count"] == 2
    assert domain["coverage_complete"] is False
    assert "轮毂电机" in domain["mapping_rationale"]
    assert "同步磁阻电机" in domain["mapping_rationale"]


def test_approximate_cross_industry_board_is_rejected_even_when_symbol_overlaps_context() -> None:
    def fetch(theme: str, _limit: int, **_kwargs) -> dict:
        if theme == "人形机器人":
            return {
                **_theme_result(theme),
                "items": [{
                    "symbol": "002459", "name": "晶澳科技",
                    "boards": ["人形机器人"], "evidence_level": "L1",
                }],
            }
        return {
            "success": True,
            "partial": True,
            "coverage_complete": False,
            "theme": theme,
            "local_universe_count": 5879,
            "items": [{
                "symbol": "002459", "name": "晶澳科技",
                "boards": ["HJT电池", "BC电池"], "evidence_level": "L1",
            }],
            "matched_boards": [{"name": "HJT电池", "coverage": "full", "primary_theme": False}],
            "warnings": ["没有精确电池板块"],
            "errors": [],
        }

    with patch(
        "src.services.data_maintenance.ensure_stock_universe",
        return_value={"total": 5879},
    ), patch(
        "src.tools.get_domain_stock_candidates._load_local_universe",
        return_value={"002459": {}},
    ), patch(
        "src.tools.get_domain_stock_candidates.get_theme_stock_candidates",
        side_effect=fetch,
    ):
        result = get_domain_stock_candidates(["电池"], context_theme="人形机器人")

    domain = result["domain_results"][0]
    assert result["success"] is False
    assert result["items"] == []
    assert domain["candidate_count"] == 0
    assert domain["context_filter_applied"] is True
    assert domain["matched_boards"] == []
    assert domain["rejected_boards"][0]["name"] == "HJT电池"
    assert any("拒绝近似或跨行业板块候选" in error for error in domain["errors"])


def test_empty_context_intersection_returns_zero_without_unfiltered_fallback() -> None:
    def fetch(theme: str, _limit: int, **_kwargs) -> dict:
        if theme == "人形机器人":
            return {
                **_theme_result(theme),
                "items": [{
                    "symbol": "300580", "name": "贝斯特",
                    "boards": ["人形机器人"], "evidence_level": "L1",
                }],
            }
        return {
            **_theme_result(theme),
            "items": [{
                "symbol": "688017", "name": "绿的谐波",
                "boards": ["减速器"], "evidence_level": "L1",
            }],
        }

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
        result = get_domain_stock_candidates(["减速器"], context_theme="人形机器人")

    domain = result["domain_results"][0]
    assert result["items"] == []
    assert domain["pre_context_candidate_count"] == 1
    assert domain["post_context_candidate_count"] == 0
    assert domain["context_filter_applied"] is True
    assert any("未回退到未交集候选" in error for error in domain["errors"])


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
