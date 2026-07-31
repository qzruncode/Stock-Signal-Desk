"""Agent coverage for the former Portfolio page."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.manage_watchlist_groups import manage_watchlist_groups
from src.tools.filter_watchlist_by_theme import filter_watchlist_by_theme
from src.tools.registry import ToolRegistry
from src.tools.run_batch_analysis import run_batch_analysis
from src.tools.screen_atr_volatility_stocks import screen_atr_volatility_stocks


def test_registry_exposes_default_and_custom_watchlist_management() -> None:
    names = set(ToolRegistry().get_tool_names())
    assert {"manage_watchlist", "manage_watchlist_groups", "filter_watchlist_by_theme"} <= names


def test_filter_watchlist_by_theme_returns_only_collection_intersection() -> None:
    group_payload = {
        "success": True,
        "partial": False,
        "groups": [
            {
                "id": "default",
                "name": "我的自选股",
                "codes": ["300750", "002230", "603662", "未上市/无代码"],
                "is_default": True,
            }
        ],
        "warnings": [],
    }

    domain_result = {
        "success": True,
        "partial": False,
        "warnings": [],
        "errors": [],
        "domain_results": [
            {
                "domain": "人工智能",
                "mapping_type": "catalog_binding",
                "mapping_rationale": "测试绑定",
                "candidate_count": 2,
                "coverage_complete": True,
                "matched_boards": [],
                "items": [
                    {"symbol": "002230", "name": "科大讯飞", "boards": ["人工智能"], "source": {"name": "概念源"}},
                    {"symbol": "000977", "name": "浪潮信息", "boards": ["人工智能"], "source": {"name": "概念源"}},
                ],
            },
            {
                "domain": "机器人",
                "mapping_type": "catalog_binding",
                "mapping_rationale": "测试绑定",
                "candidate_count": 1,
                "coverage_complete": True,
                "matched_boards": [],
                "items": [
                    {"symbol": "603662", "name": "柯力传感", "boards": ["机器人概念"], "source": {"name": "概念源"}},
                ],
            },
        ],
    }
    domains = [
        {
            "label": label,
            "board_queries": [label],
            "mapping_type": "catalog_binding",
            "rationale": "测试绑定",
            "unresolved_parts": [],
        }
        for label in ("人工智能", "机器人")
    ]

    with (
        patch(
            "src.tools.filter_watchlist_by_theme.manage_watchlist_groups",
            return_value=group_payload,
        ),
        patch(
            "src.tools.filter_watchlist_by_theme.get_domain_stock_candidates",
            return_value=domain_result,
        ),
    ):
        result = filter_watchlist_by_theme(domains)

    assert result["requested_themes"] == ["人工智能", "机器人"]
    assert [item["symbol"] for item in result["items"]] == ["002230", "603662"]
    assert result["matched_count"] == 2
    assert "000977" not in {item["symbol"] for item in result["items"]}
    assert result["invalid_entries"] == ["未上市/无代码"]


def test_list_watchlist_groups_includes_default_and_custom_groups() -> None:
    db = MagicMock()
    db.list_watchlist_groups.return_value = [
        {
            "id": 3,
            "name": "新能源",
            "codes": ["300750"],
            "source": "manual",
        }
    ]
    with (
        patch(
            "src.tools.manage_watchlist_groups.DatabaseManager.get_instance",
            return_value=db,
        ),
        patch(
            "src.tools.manage_watchlist_groups._manage_default_watchlist",
            return_value={"codes": ["600519"], "count": 1, "changed": []},
        ),
    ):
        result = manage_watchlist_groups("list")

    assert result["item_count"] == 2
    assert result["groups"][0] == {
        "id": "default",
        "name": "我的自选股",
        "codes": ["600519"],
        "count": 1,
        "is_default": True,
        "source": "system_config",
    }
    assert result["groups"][1]["name"] == "新能源"


def test_add_to_custom_group_also_keeps_default_watchlist_in_sync() -> None:
    db = MagicMock()
    db.list_watchlist_groups.return_value = [
        {
            "id": 3,
            "name": "新能源",
            "codes": ["300750"],
        }
    ]
    db.update_watchlist_group.return_value = {
        "id": 3,
        "name": "新能源",
        "codes": ["300750", "002594"],
    }
    with (
        patch(
            "src.tools.manage_watchlist_groups.DatabaseManager.get_instance",
            return_value=db,
        ),
        patch(
            "src.tools.manage_watchlist_groups.resolve_securities_csv",
            return_value=([{"symbol": "002594", "name": "比亚迪"}], []),
        ),
        patch(
            "src.tools.manage_watchlist_groups._manage_default_watchlist",
            return_value={"codes": ["300750", "002594"], "count": 2, "changed": ["002594"]},
        ) as manage_default,
    ):
        result = manage_watchlist_groups("add", group="新能源", symbols="比亚迪")

    manage_default.assert_called_once_with("add", ["002594"])
    db.update_watchlist_group.assert_called_once_with(3, codes=["300750", "002594"])
    assert result["changed"] == ["002594"]


def test_delete_custom_group_requires_explicit_confirmation() -> None:
    db = MagicMock()
    db.list_watchlist_groups.return_value = [{"id": 3, "name": "新能源", "codes": []}]
    with patch(
        "src.tools.manage_watchlist_groups.DatabaseManager.get_instance",
        return_value=db,
    ):
        with pytest.raises(ValueError, match="confirmed=true"):
            manage_watchlist_groups("delete", group="新能源", confirmed=False)
    db.delete_watchlist_group.assert_not_called()


def test_full_market_screen_can_save_all_matches_as_a_group() -> None:
    screen_result = {
        "success": True,
        "partial": False,
        "items": [{"code": "000001"}],
        "total": 2,
        "matched_codes": ["000001", "000002"],
        "warnings": [],
    }
    db = MagicMock()
    db.upsert_watchlist_group.return_value = {
        "id": 7,
        "name": "高波动股",
        "codes": ["000001", "000002"],
    }
    with (
        patch(
            "src.tools.screen_atr_volatility_stocks.run_atr_volatility_screen",
            return_value=screen_result,
        ) as run_screen,
        patch(
            "src.tools.screen_atr_volatility_stocks.DatabaseManager.get_instance",
            return_value=db,
        ),
    ):
        result = screen_atr_volatility_stocks(
            {"version": "1.0"},
            save_group_name="高波动股",
        )

    run_screen.assert_called_once_with(
        screen_spec={"version": "1.0"},
        refresh_if_stale=True,
        include_matched_codes=True,
    )
    db.upsert_watchlist_group.assert_called_once_with(
        "高波动股",
        ["000001", "000002"],
        "agent_screener",
    )
    assert result["saved_group"]["count"] == 2
    assert "matched_codes" not in result


def test_batch_analysis_accepts_a_named_watchlist_group() -> None:
    db = MagicMock()
    db.list_watchlist_groups.return_value = [
        {
            "id": 4,
            "name": "核心观察",
            "codes": ["600519", "000858"],
        }
    ]
    with (
        patch(
            "src.tools.run_batch_analysis.DatabaseManager.get_instance",
            return_value=db,
        ),
        patch(
            "api.v1.endpoints.batches.run.trigger_batch_run",
            new=lambda _request: None,
        ),
        patch(
            "src.tools.run_batch_analysis.run_async",
            return_value={"batch_id": "batch-1", "status": "pending"},
        ),
    ):
        result = run_batch_analysis(
            scope="group",
            group_name="核心观察",
            prompt_template_id="template-1",
        )

    assert result["scope"] == "group"
    assert result["group_name"] == "核心观察"
    assert result["stock_codes"] == ["600519", "000858"]
