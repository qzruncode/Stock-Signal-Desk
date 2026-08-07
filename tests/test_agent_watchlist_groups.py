from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.manage_watchlist_groups import manage_watchlist_groups
from src.tools.registry import ToolRegistry


def test_registry_keeps_atomic_watchlist_operations_only() -> None:
    names = set(ToolRegistry().get_tool_names())
    assert {"manage_watchlist", "manage_watchlist_groups"} <= names
    assert {
        "filter_watchlist_by_theme",
        "screen_atr_volatility_stocks",
        "run_batch_analysis",
    }.isdisjoint(names)


def test_list_watchlist_groups_includes_default_and_custom_groups() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = [
        {"id": 3, "name": "新能源", "codes": ["300750"], "source": "manual"}
    ]
    with (
        patch("src.tools.manage_watchlist_groups.DatabaseManager.get_instance", return_value=database),
        patch(
            "src.tools.manage_watchlist_groups._manage_default_watchlist",
            return_value={"codes": ["600519"], "count": 1, "changed": []},
        ),
    ):
        result = manage_watchlist_groups("list")

    assert result["item_count"] == 2
    assert result["groups"][0]["is_default"] is True
    assert result["groups"][1]["name"] == "新能源"


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
        patch(
            "src.tools.manage_watchlist_groups._manage_default_watchlist",
            return_value={"codes": ["300750", "002594"], "count": 2, "changed": ["002594"]},
        ) as manage_default,
    ):
        result = manage_watchlist_groups("add", group="新能源", symbols="比亚迪")

    manage_default.assert_called_once_with("add", ["002594"])
    database.update_watchlist_group.assert_called_once_with(3, codes=["300750", "002594"])
    assert result["changed"] == ["002594"]


def test_delete_business_adapter_still_requires_injected_confirmation() -> None:
    database = MagicMock()
    database.list_watchlist_groups.return_value = [{"id": 3, "name": "新能源", "codes": []}]
    with patch("src.tools.manage_watchlist_groups.DatabaseManager.get_instance", return_value=database):
        with pytest.raises(ValueError, match="confirmed=true"):
            manage_watchlist_groups("delete", group="新能源", confirmed=False)
    database.delete_watchlist_group.assert_not_called()
