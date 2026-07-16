from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from src.tools.get_announcements import get_announcements


def _uncached(key, fn, **kwargs):
    del key, kwargs
    return fn(), False


def test_announcements_keep_buyback_and_shareholder_increase_separate() -> None:
    frame = pd.DataFrame([
        {
            "代码": "600519",
            "名称": "贵州茅台",
            "公告标题": "贵州茅台关于回购股份进展的公告",
            "公告类型": "回购事项进展",
            "公告日期": date(2026, 7, 15),
            "网址": "https://example.com/buyback",
        },
        {
            "代码": "600519",
            "名称": "贵州茅台",
            "公告标题": "控股股东增持股份计划公告",
            "公告类型": "股东增持股份",
            "公告日期": date(2026, 7, 14),
            "网址": "https://example.com/increase",
        },
    ])
    with patch("src.tools.get_announcements.cached_call", side_effect=_uncached), \
         patch("src.tools.get_announcements._fetch_akshare", return_value=frame):
        all_items = get_announcements("600519", type="all")
        buybacks = get_announcements("600519", type="回购")

    assert [item["notice_type"] for item in all_items["items"]] == ["回购", "增持"]
    assert [item["url"] for item in buybacks["items"]] == ["https://example.com/buyback"]
    assert all_items["source"] == "AKShare/东方财富公司公告"
    assert all_items["success"] is True


def test_announcements_empty_window_is_a_successful_zero_result() -> None:
    with patch("src.tools.get_announcements.cached_call", side_effect=_uncached), \
         patch("src.tools.get_announcements._fetch_akshare", return_value=pd.DataFrame()):
        result = get_announcements("000001", days=7)

    assert result["success"] is True
    assert result["has_announcements"] is False
    assert result["fallback_attempted"] is False
    assert result["item_count"] == 0


def test_announcements_primary_failure_uses_exchange_rss_with_real_filter_shape() -> None:
    rss_rows = [{
        "代码": "000001",
        "名称": "平安银行",
        "公告标题": "平安银行董事会决议公告",
        "公告类型": "交易所公告",
        "公告日期": "2026-07-15",
        "网址": "https://example.com/notice",
    }]
    with patch("src.tools.get_announcements.cached_call", side_effect=RuntimeError("upstream down")), \
         patch(
             "src.tools.get_announcements._fetch_exchange_rss",
             return_value=(rss_rows, "/szse/disclosure/listed/notice/:query?", []),
         ):
        result = get_announcements("000001")

    assert result["success"] is True
    assert result["fallback_attempted"] is True
    assert result["fallback_used"] is True
    assert result["source"] == "RSSHub/交易所官方披露"
    assert result["items"][0]["url"] == "https://example.com/notice"
