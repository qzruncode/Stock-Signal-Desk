from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from market_data_service.providers.announcements import (
    read_company_announcements_akshare,
)


def test_announcement_source_preserves_source_types_without_semantic_filtering() -> None:
    frame = pd.DataFrame(
        [
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
        ]
    )
    with patch(
        "market_data_service.providers.announcements._fetch_akshare",
        return_value=frame,
    ):
        result = read_company_announcements_akshare("600519", use_cache=False)

    assert [item["notice_type"] for item in result["items"]] == [
        "回购事项进展",
        "股东增持股份",
    ]
    assert all(item["semantic_status"] == "model_required" for item in result["items"])
    assert result["source"] == "AKShare/东方财富公司公告"
    assert result["success"] is True


def test_announcement_source_returns_a_valid_empty_result() -> None:
    with patch(
        "market_data_service.providers.announcements._fetch_akshare",
        return_value=pd.DataFrame(),
    ):
        result = read_company_announcements_akshare("000001", days=7, use_cache=False)

    assert result["success"] is True
    assert result["has_announcements"] is False
    assert result["item_count"] == 0
    assert result["freshness_unknown"] is True
