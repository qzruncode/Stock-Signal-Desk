# -*- coding: utf-8 -*-
"""Watchlist endpoint behavior tests."""

import unittest

from api.v1.endpoints.watchlist import add_to_watchlist, remove_from_watchlist


class _FakeWatchlistConfigService:
    def __init__(self, stock_list: str = "600519", version: str = "v1"):
        self.stock_list = stock_list
        self.version = version
        self.updated_items = None

    def get_config(self, include_schema: bool = False, mask_token: str = ""):
        return {
            "config_version": self.version,
            "mask_token": mask_token,
            "items": [{"key": "STOCK_LIST", "value": self.stock_list}],
        }

    def update(self, *, config_version, items, mask_token, reload_now):
        self.updated_items = items
        self.stock_list = items[0]["value"]
        self.version = "v2"
        return {
            "success": True,
            "config_version": self.version,
            "applied_count": 1,
            "skipped_masked_count": 0,
            "reload_triggered": reload_now,
            "updated_keys": ["STOCK_LIST"],
            "warnings": [],
        }


class WatchlistApiTestCase(unittest.TestCase):
    def test_add_returns_new_config_version_after_write(self) -> None:
        service = _FakeWatchlistConfigService()

        response = add_to_watchlist(["000001"], service=service)

        self.assertEqual(response["codes"], ["600519", "000001"])
        self.assertEqual(response["configVersion"], "v2")

    def test_remove_returns_new_config_version_after_write(self) -> None:
        service = _FakeWatchlistConfigService(stock_list="600519,000001")

        response = remove_from_watchlist(["600519"], service=service)

        self.assertEqual(response["codes"], ["000001"])
        self.assertEqual(response["configVersion"], "v2")


if __name__ == "__main__":
    unittest.main()
