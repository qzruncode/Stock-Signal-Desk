import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.v1.endpoints.stocks import sync as stocks


class _DeferredThread:
    started = 0

    def __init__(self, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        type(self).started += 1


class StocksSyncStateTest(unittest.TestCase):
    def setUp(self):
        _DeferredThread.started = 0
        stocks._set_sync_state(
            status="idle",
            progress=0,
            total=0,
            started_at=None,
            finished_at=None,
            message="",
            error=None,
        )

    def test_sync_is_marked_running_before_background_thread_runs(self):
        with patch.object(stocks.threading, "Thread", _DeferredThread):
            result = stocks.sync_stocks(service=None)

            self.assertTrue(result["success"])
            self.assertEqual(_DeferredThread.started, 1)
            self.assertEqual(stocks._get_sync_state_copy()["status"], "running")

            with self.assertRaises(HTTPException) as ctx:
                stocks.sync_stocks(service=None)
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(_DeferredThread.started, 1)

    def test_sync_status_returns_a_snapshot(self):
        stocks._set_sync_state(status="running", total=1)

        snapshot = stocks.get_sync_status()
        snapshot["status"] = "mutated"

        self.assertEqual(stocks._get_sync_state_copy()["status"], "running")


if __name__ == "__main__":
    unittest.main()