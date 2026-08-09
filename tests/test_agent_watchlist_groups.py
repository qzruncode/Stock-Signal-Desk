from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.manage_watchlist import manage_watchlist
from src.tools.manage_watchlist_groups import manage_watchlist_groups
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
    } <= names
    assert {"manage_watchlist", "manage_watchlist_groups"}.isdisjoint(names)
    assert {
        "filter_watchlist_by_theme",
        "run_batch_analysis",
    }.isdisjoint(names)
    assert "screen_atr_volatility_stocks" in names


def test_list_watchlist_groups_reads_only_persisted_custom_groups() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = [
        {"id": 3, "name": "新能源", "codes": ["300750"], "source": "manual"}
    ]
    with patch(
        "src.tools.manage_watchlist_groups.DatabaseManager.get_instance",
        return_value=database,
    ):
        result = manage_watchlist_groups("list")

    assert result["item_count"] == 1
    assert result["groups"] == [
        {"id": 3, "name": "新能源", "codes": ["300750"], "source": "manual", "count": 1}
    ]


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
        result = manage_watchlist_groups("add", group="新能源", symbols="比亚迪")

    database.update_watchlist_group.assert_called_once_with(3, codes=["300750", "002594"])
    assert result["changed"] == ["002594"]


def test_removing_default_watchlist_item_does_not_modify_custom_groups() -> None:
    with (
        patch(
            "src.tools.manage_watchlist.resolve_securities_csv",
            return_value=([{"symbol": "600519", "name": "贵州茅台"}], []),
        ),
        patch(
            "src.tools.manage_watchlist._manage",
            return_value={"codes": [], "count": 0, "changed": ["600519"]},
        ) as manage,
    ):
        result = manage_watchlist("remove", "贵州茅台")

    manage.assert_called_once_with("remove", ["600519"])
    assert "cascaded_groups" not in result


def test_delete_business_adapter_still_requires_injected_confirmation() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = [{"id": 3, "name": "新能源", "codes": []}]
    with patch("src.tools.manage_watchlist_groups.DatabaseManager.get_instance", return_value=database):
        with pytest.raises(ValueError, match="confirmed=true"):
            manage_watchlist_groups("delete", group="新能源", confirmed=False)
    database.delete_watchlist_group.assert_not_called()
