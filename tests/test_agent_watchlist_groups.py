from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.tools.manage_watchlist_groups import (
    add_watchlist_group_members,
    list_watchlist_groups,
    read_watchlist_group,
)
from src.tools.registry import ToolRegistry


def test_registry_keeps_atomic_watchlist_operations_only() -> None:
    names = set(ToolRegistry().get_tool_names())
    assert {
        "list_watchlist",
        "add_watchlist_items",
        "remove_watchlist_items",
        "list_watchlist_groups",
        "create_watchlist_group",
        "rename_watchlist_group",
        "delete_watchlist_group",
        "add_watchlist_group_members",
        "remove_watchlist_group_members",
        "read_watchlist_group",
    } <= names
    assert {"manage_watchlist", "manage_watchlist_groups"}.isdisjoint(names)
    assert {
        "filter_watchlist_by_theme",
        "run_batch_analysis",
    }.isdisjoint(names)
    assert "screen_atr_volatility_stocks" in names


def test_list_watchlist_groups_returns_compact_default_and_custom_summaries() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = [
        {"id": 3, "name": "新能源", "codes": ["300750"], "source": "manual"}
    ]
    with patch(
        "src.tools.manage_watchlist_groups.DatabaseManager.get_instance",
        return_value=database,
    ), patch(
        "src.tools.manage_watchlist_groups._manage_watchlist",
        return_value={"codes": ["600519", "000001"]},
    ):
        result = list_watchlist_groups()

    assert result["item_count"] == 2
    assert result["groups"] == [
        {"id": "default", "name": "我的自选股", "count": 2, "source": "system", "kind": "default"},
        {"id": "3", "name": "新能源", "count": 1, "source": "manual", "kind": "custom"},
    ]


def test_read_watchlist_group_returns_member_page_and_context_reference() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = [
        {
            "id": "3",
            "name": "新能源",
            "codes": ["300750", "002594", "601012"],
            "source": "manual",
        }
    ]
    with patch(
        "src.tools.manage_watchlist_groups.DatabaseManager.get_instance",
        return_value=database,
    ), patch(
        "src.services.name_to_code_resolver.get_database_stock_indexes",
        return_value=({}, {"002594": "比亚迪"}),
    ):
        result = read_watchlist_group("新能源", offset=1, limit=1)

    assert result["members"] == [{"symbol": "002594", "name": "比亚迪"}]
    assert result["has_more"] is True
    assert result["next_offset"] == 2
    assert result["group"] == {
        "id": "3",
        "name": "新能源",
        "count": 3,
        "source": "manual",
        "kind": "custom",
    }
    assert result["_agent_context"] == {
        "type": "stock_group",
        "group_id": "3",
        "group_name": "新能源",
        "member_count": 3,
        "source": "manual",
    }


def test_read_default_watchlist_group_uses_default_group_reference() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = []
    with (
        patch(
            "src.tools.manage_watchlist_groups.DatabaseManager.get_instance",
            return_value=database,
        ),
        patch(
            "src.tools.manage_watchlist_groups._manage_watchlist",
            return_value={"codes": ["600519"]},
        ),
        patch(
            "src.services.name_to_code_resolver.get_database_stock_indexes",
            return_value=({}, {"600519": "贵州茅台"}),
        ),
    ):
        result = read_watchlist_group("我的自选股")

    assert result["members"] == [{"symbol": "600519", "name": "贵州茅台"}]
    assert result["group"]["id"] == "default"
    assert result["_agent_context"]["group_name"] == "我的自选股"


def test_add_to_custom_group_is_one_external_operation() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = [
        {"id": 3, "name": "新能源", "codes": ["300750"]}
    ]
    database.update_watchlist_group.return_value = {
        "id": 3,
        "name": "新能源",
        "codes": ["300750", "002594"],
    }
    with (
        patch("src.tools.manage_watchlist_groups.DatabaseManager.get_instance", return_value=database),
        patch(
            "src.tools.manage_watchlist_groups.resolve_securities_csv",
            return_value=([{"symbol": "002594", "name": "比亚迪"}], []),
        ),
    ):
        result = add_watchlist_group_members("新能源", "比亚迪")

    database.update_watchlist_group.assert_called_once_with(3, codes=["300750", "002594"])
    assert result["changed"] == ["002594"]
