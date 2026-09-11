from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from market_data_service.providers.research_reports import (
    read_company_research_reports_akshare,
)


def test_research_source_discovers_forecast_years_from_actual_columns() -> None:
    frame = pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "股票简称": "贵州茅台",
                "报告名称": "公司事件点评报告",
                "东财评级": "买入",
                "机构": "测试证券",
                "近一月个股研报数": 3,
                "2027-盈利预测-收益": 70.5,
                "2027-盈利预测-市盈率": 18.2,
                "2029-盈利预测-收益": 80.1,
                "2029-盈利预测-市盈率": 16.0,
                "行业": "白酒Ⅱ",
                "日期": date.today(),
                "报告PDF链接": "https://example.com/report.pdf",
            }
        ]
    )
    with patch(
        "market_data_service.providers.research_reports._fetch_akshare",
        return_value=frame,
    ):
        result = read_company_research_reports_akshare("600519", use_cache=False)

    forecasts = result["items"][0]["profit_forecasts"]
    assert [item["year"] for item in forecasts] == [2027, 2029]
    assert forecasts[0]["eps_unit"] == "元/股"
    assert forecasts[0]["pe_unit"] == "倍"
    assert result["source"] == "AKShare/东方财富个股研报"
    assert result["content_access"]["content_read"] is False


def test_research_source_returns_a_valid_empty_result_for_an_old_window() -> None:
    frame = pd.DataFrame(
        [
            {
                "股票代码": "920000",
                "股票简称": "安徽凤凰",
                "报告名称": "历史研报",
                "日期": date(2020, 12, 21),
            }
        ]
    )
    with patch(
        "market_data_service.providers.research_reports._fetch_akshare",
        return_value=frame,
    ):
        result = read_company_research_reports_akshare(
            "920000", days=365, use_cache=False
        )

    assert result["success"] is True
    assert result["has_reports"] is False
    assert result["item_count"] == 0
    assert result["freshness_unknown"] is True
