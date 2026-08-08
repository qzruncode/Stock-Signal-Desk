from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pandas as pd

from src.tools.get_financials import read_core_financial_indicators_ths
from src.tools.get_index_data import read_index_daily_history_sina, read_index_quote_sina
from src.tools.get_stock_info import read_stock_capital_snapshot_eastmoney
from src.tools.get_stock_capital_flow import (
    read_stock_capital_flow_history_eastmoney,
    read_stock_capital_flow_quote_eastmoney,
)
from src.tools.get_valuation_ratios import (
    read_dividend_history_eastmoney,
    read_peer_valuation_eastmoney,
    read_valuation_history_eastmoney,
    read_valuation_quote_eastmoney,
)
from src.tools.market_snapshot_tools import (
    read_market_breadth_legu,
    read_market_breadth_sina,
    read_market_limit_up_pool_eastmoney,
)
from src.tools.search_stocks import search_stocks


def test_core_financial_indicator_read_does_not_merge_statement_sources() -> None:
    with patch(
        "src.tools.get_financials.fetch_core_indicators",
        return_value=[{"report_date": "2026-03-31", "revenue": 100.0}],
    ) as fetch:
        result = read_core_financial_indicators_ths("600519", periods=2, use_cache=False)

    fetch.assert_called_once_with("600519", 2)
    assert result["success"] is True
    assert result["source_scope"] == "reported_core_financial_indicators"
    assert result["items"] == [{"report_date": "2026-03-31", "revenue": 100.0}]


def test_capital_flow_history_and_quote_are_separate_source_reads() -> None:
    history = pd.DataFrame(
        [{"date": "2026-08-06", "main_net_inflow": 10.0, "main_net_inflow_pct": 1.0}]
    )
    history.attrs["transport"] = "curl_cffi"
    current = {"date": "2026-08-07", "_data_time": "2026-08-07T10:00:00+08:00", "main_net_inflow": 20.0}

    with (
        patch("src.tools.get_stock_capital_flow._fetch_eastmoney_direct", return_value=history) as fetch_history,
        patch("src.tools.get_stock_capital_flow._fetch_current_flow", return_value=current) as fetch_quote,
        patch("src.tools.get_stock_capital_flow._is_stale", return_value=(False, None)),
    ):
        history_result = read_stock_capital_flow_history_eastmoney("600519", use_cache=False)
        quote_result = read_stock_capital_flow_quote_eastmoney("600519", use_cache=False)

    assert history_result["source_scope"] == "daily_capital_flow_history"
    assert quote_result["source_scope"] == "current_session_capital_flow_quote"
    assert fetch_history.call_count == 1
    assert fetch_quote.call_count == 1


def test_index_history_and_quote_do_not_call_each_other() -> None:
    history = pd.DataFrame(
        [
            {"date": "2026-08-05", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1, "amount": 2},
            {"date": "2026-08-06", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 3, "amount": 4},
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "代码": "sh000001",
                "最新价": 102,
                "今开": 101,
                "最高": 103,
                "最低": 100,
                "昨收": 101,
                "成交量": 5,
                "成交额": 6,
                "涨跌额": 1,
                "涨跌幅": 0.99,
            }
        ]
    )
    with (
        patch("src.tools.get_index_data._daily_frame", return_value=history) as fetch_history,
        patch("src.tools.get_index_data._spot_frame", return_value=snapshot) as fetch_quote,
        patch("src.tools.get_index_data._expected_session_date", return_value="2026-08-07"),
    ):
        history_result = read_index_daily_history_sina(days=5, use_cache=False)
        quote_result = read_index_quote_sina(use_cache=False)

    assert history_result["source_scope"] == "index_daily_history"
    assert quote_result["source_scope"] == "index_realtime_quote"
    assert fetch_history.call_count == 1
    assert fetch_quote.call_count == 1


def test_valuation_reads_remain_source_specific() -> None:
    history = pd.DataFrame(
        [{"数据日期": "2026-08-06", "当日收盘价": 10, "PE(TTM)": 20, "市净率": 3}]
    )
    dividends = pd.DataFrame(
        [{"报告期": "2025-12-31", "除权除息日": "2026-06-01", "方案进度": "实施", "现金分红-现金分红比例": 10}]
    )
    quote = {"price": 10.0, "pe_ttm": 20.0, "quote_time": "2026-08-07T10:00:00+08:00"}
    peer = {"report_date": "2026-08-06", "median": {"pe_ttm": 18.0}, "average": {}, "total": 10}
    with (
        patch("src.tools.get_valuation_ratios._fetch_history", return_value=history) as fetch_history,
        patch("src.tools.get_valuation_ratios._fetch_quote", return_value=quote) as fetch_quote,
        patch("src.tools.get_valuation_ratios._fetch_comparison", return_value=peer) as fetch_peer,
        patch("src.tools.get_valuation_ratios._fetch_dividends", return_value=dividends) as fetch_dividends,
    ):
        history_result = read_valuation_history_eastmoney("600519", use_cache=False)
        quote_result = read_valuation_quote_eastmoney("600519", use_cache=False)
        peer_result = read_peer_valuation_eastmoney("600519", use_cache=False)
        dividend_result = read_dividend_history_eastmoney("600519", use_cache=False)

    assert history_result["source_scope"] == "daily_valuation_history"
    assert quote_result["source_scope"] == "realtime_valuation_quote"
    assert peer_result["source_scope"] == "peer_valuation_comparison"
    assert dividend_result["source_scope"] == "dividend_implementation_history"
    assert fetch_history.call_count == fetch_quote.call_count == fetch_peer.call_count == fetch_dividends.call_count == 1


def test_valuation_quote_marks_missing_source_timestamp_as_unavailable() -> None:
    quote = {"price": 10.0, "pe_ttm": 20.0, "quote_time": None}
    with patch("src.tools.get_valuation_ratios._fetch_quote", return_value=quote):
        result = read_valuation_quote_eastmoney("600519", use_cache=False)

    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert "quote_time" in result["data_time_note"]
    assert result["freshness_unknown"] is True


def test_market_source_reads_do_not_activate_legacy_snapshot_fallbacks() -> None:
    breadth = {
        "up_count": 3000,
        "down_count": 1000,
        "flat_count": 100,
        "data_time": "2026-08-07T10:00:00+08:00",
    }
    with (
        patch("src.tools.market_snapshot_tools._fetch_legu_activity", return_value=breadth) as fetch_breadth,
        patch(
            "src.tools.market_snapshot_tools._fetch_pool",
            return_value={"count": 42, "source": "stock_zt_pool_em"},
        ) as fetch_pool,
    ):
        breadth_result = read_market_breadth_legu(use_cache=False)
        pool_result = read_market_limit_up_pool_eastmoney("20260807", use_cache=False)

    fetch_breadth.assert_called_once_with()
    fetch_pool.assert_called_once_with("stock_zt_pool_em", "20260807")
    assert breadth_result["source_scope"] == "market_breadth"
    assert pool_result["source_scope"] == "market_limit_pool"


def test_stock_capital_snapshot_never_uses_fetch_time_as_quote_time() -> None:
    snapshot = {
        "symbol": "600519",
        "market_code": "sh",
        "short_name": "贵州茅台",
        "latest_price": 1500.0,
        "total_shares": 1.0,
        "circulating_shares": 1.0,
        "total_market_cap": 1.0,
        "circulating_market_cap": 1.0,
        "quote_time": None,
    }
    with patch(
        "src.tools.get_stock_info._fetch_eastmoney_capital",
        return_value=snapshot,
    ):
        result = read_stock_capital_snapshot_eastmoney("600519", use_cache=False)

    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert "quote_time" in result["data_time_note"]
    assert result["freshness_unknown"] is True


def test_inferred_market_breadth_time_is_not_treated_as_source_time() -> None:
    breadth = {
        "up_count": 3000,
        "down_count": 1000,
        "flat_count": 100,
        "data_time": "2026-08-07T10:00:00+08:00",
        "data_time_inferred": True,
    }
    with patch(
        "src.tools.market_snapshot_tools._fetch_sina_a_breadth",
        return_value=breadth,
    ):
        result = read_market_breadth_sina(use_cache=False)

    assert result["data_time_provenance"] == "inferred"
    assert result["freshness_unknown"] is True
    assert result["is_stale"] is None


def test_stock_search_is_a_local_read_and_never_starts_maintenance() -> None:
    item = MagicMock()
    item.to_dict.return_value = {
        "code": "600519",
        "name": "贵州茅台",
        "market": "sh",
        "sector": "白酒",
        "status": "active",
    }

    class Session:
        def query(self, _model):
            return self

        def filter(self, *_conditions):
            return self

        def order_by(self, *_columns):
            return self

        def limit(self, _value):
            return self

        def all(self):
            return [item]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Database:
        def get_session(self):
            return Session()

    with patch(
        "src.tools.search_stocks.DatabaseManager.get_instance",
        return_value=Database(),
    ):
        result = search_stocks("600519")

    assert result["items"] == [item.to_dict.return_value]
    assert result["data_source"] == "local_stock_meta"
    assert result["source_scope"] == "local_security_master_identity_lookup"
    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert result["freshness_unknown"] is True
    assert result["is_stale"] is None
    assert "maintenance" not in result
