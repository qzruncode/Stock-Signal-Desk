"""No API process memory is authoritative for synchronization."""

from unittest.mock import Mock, patch
from api.v1.endpoints.stocks import sync


def test_status_observes_new_service_state_on_each_request():
    client = Mock()
    client.get.side_effect = [
        {"items": [{"id": "same", "status": "running"}]},
        {"items": [{"id": "same", "status": "cancelled"}]},
    ]
    with patch.object(sync, "get_market_data_client", return_value=client):
        assert sync.get_stock_list_sync_status()["status"] == "running"
        assert sync.get_stock_list_sync_status()["status"] == "cancelled"


def test_all_sync_state_belongs_to_data_service():
    assert not any(
        hasattr(sync, name)
        for name in (
            "_list_sync_state",
            "_kline_sync_state",
            "_financial_sync_state",
            "_launch_worker",
        )
    )
