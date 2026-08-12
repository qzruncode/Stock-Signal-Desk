import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.v1.endpoints.stocks import sync as stocks


class StocksSyncStateTest(unittest.TestCase):
    def setUp(self):
        stocks._set_list_state(
            status="idle",
            progress=0,
            total=0,
            kline_progress=0,
            kline_total=0,
            started_at=None,
            finished_at=None,
            message="",
            error=None,
        )

    def test_list_sync_is_marked_running_before_background_thread_runs(self):
        with (
            patch.object(stocks, "_latest_stock_universe_status", return_value=None),
            patch.object(stocks, "_claim_persisted_sync_job", return_value=("list-job-1", True)),
            patch.object(stocks, "_launch_detached_worker") as launch_worker,
        ):
            result = stocks.sync_stock_list(service=None)

            self.assertTrue(result["success"])
            launch_worker.assert_called_once_with("src.services.stock_list_sync_worker", "list-job-1")
            self.assertEqual(stocks._get_list_state_copy()["status"], "running")

            with self.assertRaises(HTTPException) as ctx:
                stocks.sync_stock_list(service=None)
            self.assertEqual(ctx.exception.status_code, 409)
            launch_worker.assert_called_once_with("src.services.stock_list_sync_worker", "list-job-1")

    def test_list_sync_status_returns_a_snapshot(self):
        stocks._set_list_state(status="running", total=1)

        snapshot = stocks.get_stock_list_sync_status()
        snapshot["status"] = "mutated"

        self.assertEqual(stocks._get_list_state_copy()["status"], "running")


if __name__ == "__main__":
    unittest.main()
