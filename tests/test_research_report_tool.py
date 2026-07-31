from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from src.tools.get_research_report import get_research_report


def _uncached(key, fn, **kwargs):
    del key, kwargs
    return fn(), False


def test_research_report_discovers_forecast_years_from_actual_columns() -> None:
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
    with (
        patch("src.tools.get_research_report.cached_call", side_effect=_uncached),
        patch("src.tools.get_research_report._fetch_akshare", return_value=frame),
    ):
        result = get_research_report("600519")

    forecasts = result["items"][0]["profit_forecasts"]
    assert [item["year"] for item in forecasts] == [2027, 2029]
    assert forecasts[0]["eps_unit"] == "元/股"
    assert forecasts[0]["pe_unit"] == "倍"
    assert result["source_chain"] == ["AKShare/东方财富个股研报"]
    assert result["analysis"]["forecast_years"] == [2027, 2029]


def test_no_recent_research_report_is_valid_zero_result() -> None:
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
    with (
        patch("src.tools.get_research_report.cached_call", side_effect=_uncached),
        patch("src.tools.get_research_report._fetch_akshare", return_value=frame),
    ):
        result = get_research_report("920000", days=365)

    assert result["success"] is True
    assert result["has_reports"] is False
    assert result["fallback_attempted"] is False
    assert result["warnings"]


def test_research_report_uses_rss_only_when_structured_source_failed() -> None:
    rss_item = {
        "symbol": "300850",
        "name": "新强联",
        "title": "RSS 研报",
        "org": "测试机构",
        "rating": None,
        "industry": None,
        "publish_date": date.today().isoformat(),
        "url": "https://example.com/rss-report",
        "summary": "研报摘要",
        "profit_forecasts": [],
        "monthly_report_count": None,
        "source": "RSSHub/东方财富个股研报",
        "source_type": "rss_research_report",
    }
    with (
        patch("src.tools.get_research_report.cached_call", side_effect=RuntimeError("down")),
        patch("src.tools.get_research_report._fetch_rss_fallback", return_value=([rss_item], [])),
    ):
        result = get_research_report("300850")

    assert result["success"] is True
    assert result["fallback_attempted"] is True
    assert result["fallback_used"] is True
    assert result["items"][0]["url"] == "https://example.com/rss-report"


def test_research_report_empty_successful_rss_fallback_is_valid_zero_result() -> None:
    with (
        patch("src.tools.get_research_report.cached_call", side_effect=RuntimeError("down")),
        patch("src.tools.get_research_report._fetch_rss_fallback", return_value=([], [])),
    ):
        result = get_research_report("300850")

    assert result["success"] is True
    assert result["fallback_attempted"] is True
    assert result["fallback_used"] is False
    assert result["item_count"] == 0
    assert result["source"] == "RSSHub/东方财富个股研报"
