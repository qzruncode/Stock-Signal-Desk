"""Business readiness and Agent contracts; durable execution is tested in the service."""

from unittest.mock import MagicMock, patch
import pytest
from src.services import data_maintenance
from src.services.market_data_client import DataNotReady, MarketDataError
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
