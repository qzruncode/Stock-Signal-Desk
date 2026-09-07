"""Business readiness and Agent contracts; durable execution is tested in the service."""

from unittest.mock import MagicMock, patch
import pytest
from src.services import data_maintenance
from src.services.market_data_client import DataNotReady, MarketDataError
from src.tools.get_data_health import get_data_health
from src.tools.manage_watchlist import manage_watchlist
from src.tools.registry import ToolRegistry


def test_ready_universe_reads_service_without_local_sync():
    client = MagicMock()
    client.securities.return_value = {"total": 5200, "freshness": "fresh"}
    with patch.object(data_maintenance, "get_market_data_client", return_value=client):
        result = data_maintenance.ensure_stock_universe()
    assert result["maintenance_status"] == "ready" and not result["is_stale"]
    client.post.assert_not_called()


def test_force_waits_for_durable_service_job():
    client = MagicMock()
    client.securities.return_value = {"total": 5200, "freshness": "fresh"}
    job = {"id": "durable-id", "status": "queued"}
    client.post.return_value = job
    progress = MagicMock()
    with patch.object(data_maintenance, "get_market_data_client", return_value=client):
        result = data_maintenance.ensure_stock_universe(
            force=True, on_progress=progress
        )
    client.post.assert_called_once_with(
        "/v1/jobs", {"dataset": "securities", "mode": "all"}
    )
    client.wait_job.assert_called_once_with(job, on_progress=progress)
    assert result["refreshed"]


@pytest.mark.parametrize(
    "error", [DataNotReady({"job": {"id": "durable-id"}}), MarketDataError("offline")]
)
def test_unavailable_or_stale_master_never_becomes_fresh(error):
    client = MagicMock()
    client.securities.side_effect = error
    with (
        patch.object(data_maintenance, "get_market_data_client", return_value=client),
        pytest.raises(MarketDataError),
    ):
        data_maintenance.ensure_stock_universe()


def test_agent_registry_excludes_aggregate_maintenance_dashboard() -> None:
    names = set(ToolRegistry().get_tool_names())
    assert {"search_stocks", "list_watchlist", "add_watchlist_items"}.issubset(names)
    assert "get_data_health" not in names


def test_manage_watchlist_tool_delegates_explicit_action() -> None:
    with (
        patch(
            "src.services.name_to_code_resolver.get_database_stock_indexes",
            return_value=({"贵州茅台": "600519"}, {"600519": "贵州茅台"}),
        ),
        patch(
            "src.tools.manage_watchlist._manage",
            return_value={"codes": ["600519"], "count": 1, "changed": ["600519"]},
        ) as manage,
    ):
        result = manage_watchlist("add", "600519")

    assert result["success"] is True
    assert result["changed"] == ["600519"]
    manage.assert_called_once_with("add", ["600519"])


def test_manage_watchlist_list_ignores_symbols_field() -> None:
    with patch(
        "src.tools.manage_watchlist._manage",
        return_value={"codes": ["300850"], "count": 1, "changed": []},
    ) as manage:
        result = manage_watchlist("list", "新强联")

    assert result["success"] is True
    manage.assert_called_once_with("list", [])


def test_registry_list_watchlist_has_no_multiplexed_action_field() -> None:
    with patch(
        "src.tools.manage_watchlist._manage",
        return_value={"codes": ["300850"], "count": 1, "changed": []},
    ) as manage:
        result = ToolRegistry().execute(
            "list_watchlist",
            {},
        )

    assert result["success"] is True
    manage.assert_called_once_with("list", [])


def test_manage_watchlist_resolves_chinese_name_for_direct_tool_execution() -> None:
    with (
        patch(
            "src.tools.manage_watchlist.resolve_securities_csv",
            return_value=(
                [{"input": "新强联", "symbol": "300850", "name": "新强联"}],
                [],
            ),
        ),
        patch(
            "src.tools.manage_watchlist._manage",
            return_value={"codes": ["300850"], "count": 1, "changed": ["300850"]},
        ) as manage,
    ):
        result = manage_watchlist("add", "新强联")

    assert result["changed"] == ["300850"]
    manage.assert_called_once_with("add", ["300850"])


def test_data_health_tool_marks_empty_universe_as_unknown_freshness() -> None:
    payload = {
        "stock_universe": {"total": 0, "data_time": None, "is_stale": True},
        "kline": {"covered_stocks": 0, "coverage_ratio": 0, "latest_trade_date": None},
        "financials": {
            "covered_stocks": 0,
            "coverage_ratio": 0,
            "latest_report_period": None,
            "last_fetched_at": None,
        },
        "recent_jobs": [],
    }
    with patch("src.tools.get_data_health._get_health", return_value=payload):
        result = get_data_health()

    assert result["success"] is True
    assert result["freshness_unknown"] is True
    assert result["is_stale"] is None
    assert result["warnings"]
