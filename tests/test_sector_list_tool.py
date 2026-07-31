from unittest.mock import patch

from src.tools.get_sector_list import get_sector_list


def test_sector_list_projects_complete_bounded_flow_directory():
    flow = {
        "success": True,
        "records": [
            {
                "sector_code": "BK0477",
                "name": "酿酒行业",
                "pct_chg": 1.25,
                "main_net_inflow": 123.0,
                "main_net_inflow_pct": 2.3,
                "leading_stock": "贵州茅台",
                "leading_stock_code": "600519",
            }
        ],
        "source": "东方财富板块资金流",
        "data_time": "2026-07-17T15:00:00+08:00",
        "is_stale": False,
        "freshness_unknown": False,
        "fallback_used": False,
        "errors": [],
        "warnings": [],
        "_cached": True,
        "_fetched_at": "2026-07-19T21:00:00+08:00",
    }
    with patch("src.tools.get_sector_list.get_sector_flow", return_value=flow) as fetch:
        result = get_sector_list("industry")

    fetch.assert_called_once_with(type="industry", period="today", top_n=30)
    assert result["success"] is True
    assert result["item_count"] == 1
    assert result["items"][0] == {
        "name": "酿酒行业",
        "code": "BK0477",
        "change_pct": 1.25,
        "lead_stock": "贵州茅台",
        "lead_stock_code": "600519",
        "lead_stock_price": None,
        "lead_stock_change_pct": None,
        "up_count": None,
        "down_count": None,
        "company_count": None,
        "net_flow": 123.0,
        "net_flow_pct": 2.3,
        "data_source": "东方财富",
    }
