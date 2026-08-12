"""Sector-list source fallback tests."""

from unittest.mock import patch

import pandas as pd

from api.v1.endpoints import sectors


def test_industry_uses_sina_when_eastmoney_is_empty() -> None:
    sina = pd.DataFrame(
        [
            {
                "label": "new_jrhy",
                "板块": "金融行业",
                "公司家数": 12,
                "涨跌幅": 1.23,
                "总成交量": 1000,
                "总成交额": 2000,
                "股票代码": "sh600000",
                "股票名称": "浦发银行",
                "个股-涨跌幅": 2.5,
                "个股-当前价": 12.3,
            }
        ]
    )

    with (
        patch("akshare.stock_board_industry_name_em", return_value=pd.DataFrame()),
        patch("akshare.stock_sector_spot", return_value=sina) as fallback,
    ):
        result = sectors._fetch_industry()

    fallback.assert_called_once_with(indicator="行业")
    assert len(result) == 1
    assert result[0]["name"] == "金融行业"
    assert result[0]["lead_stock"] == "浦发银行"
    assert result[0]["data_source"] == "新浪"


def test_today_cache_hit_does_not_start_another_provider_refresh() -> None:
    cached = [{"name": "金融行业", "data_source": "东方财富"}]
    with (
        patch.object(sectors, "_cache_get", return_value=(cached, "2026-08-12T09:30:00", False)),
        patch.object(sectors, "_fetch_industry") as fetch,
    ):
        result = sectors.get_sector_list("industry")

    fetch.assert_not_called()
    assert result["_cached"] is True
    assert result["is_stale"] is False
