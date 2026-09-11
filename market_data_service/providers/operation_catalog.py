"""Explicit allow-list of relocated provider APIs; no arbitrary import/RPC."""

OPERATIONS = {
    "financials.read_core_financial_indicators_ths": (
        "financials",
        "financials",
        "read_core_financial_indicators_ths",
    ),
    "news.read_company_news_akshare": ("news", "news", "read_company_news_akshare"),
    "announcements.read_company_announcements_akshare": (
        "announcements",
        "announcements",
        "read_company_announcements_akshare",
    ),
    "research_reports.read_company_research_reports_akshare": (
        "announcements",
        "research_reports",
        "read_company_research_reports_akshare",
    ),
    "get_stock_info.read_company_profile_cninfo": (
        "financials",
        "get_stock_info",
        "read_company_profile_cninfo",
    ),
    "get_stock_info.read_stock_capital_snapshot_eastmoney": (
        "financials",
        "get_stock_info",
        "read_stock_capital_snapshot_eastmoney",
    ),
    "get_business_segments.read_business_segments_eastmoney": (
        "financials",
        "get_business_segments",
        "read_business_segments_eastmoney",
    ),
    "get_consensus_estimates.read_consensus_metric_ths": (
        "financials",
        "get_consensus_estimates",
        "read_consensus_metric_ths",
    ),
    "get_consensus_estimates.read_consensus_institution_forecasts_ths": (
        "financials",
        "get_consensus_estimates",
        "read_consensus_institution_forecasts_ths",
    ),
    "get_consensus_estimates.read_consensus_financial_estimates_ths": (
        "financials",
        "get_consensus_estimates",
        "read_consensus_financial_estimates_ths",
    ),
    "get_peer_comparison.read_peer_comparison_dimension_eastmoney": (
        "financials",
        "get_peer_comparison",
        "read_peer_comparison_dimension_eastmoney",
    ),
    "get_shareholder_structure.read_shareholder_f10_profile_eastmoney": (
        "financials",
        "get_shareholder_structure",
        "read_shareholder_f10_profile_eastmoney",
    ),
    "get_shareholder_structure.read_institutional_holdings_eastmoney": (
        "financials",
        "get_shareholder_structure",
        "read_institutional_holdings_eastmoney",
    ),
    "get_shareholder_structure.read_major_shareholder_changes_ths": (
        "financials",
        "get_shareholder_structure",
        "read_major_shareholder_changes_ths",
    ),
    "get_valuation_ratios.read_valuation_history_eastmoney": (
        "market",
        "get_valuation_ratios",
        "read_valuation_history_eastmoney",
    ),
    "get_valuation_ratios.read_valuation_quote_eastmoney": (
        "market",
        "get_valuation_ratios",
        "read_valuation_quote_eastmoney",
    ),
    "get_valuation_ratios.read_peer_valuation_eastmoney": (
        "market",
        "get_valuation_ratios",
        "read_peer_valuation_eastmoney",
    ),
    "get_valuation_ratios.read_dividend_history_eastmoney": (
        "market",
        "get_valuation_ratios",
        "read_dividend_history_eastmoney",
    ),
    "get_sector_flow.read_sector_flow_eastmoney": (
        "market",
        "get_sector_flow",
        "read_sector_flow_eastmoney",
    ),
    "get_stock_capital_flow.read_stock_capital_flow_history_eastmoney": (
        "market",
        "get_stock_capital_flow",
        "read_stock_capital_flow_history_eastmoney",
    ),
    "get_stock_capital_flow.read_stock_capital_flow_quote_eastmoney": (
        "market",
        "get_stock_capital_flow",
        "read_stock_capital_flow_quote_eastmoney",
    ),
    "get_index_data.read_index_daily_history_sina": (
        "macro",
        "get_index_data",
        "read_index_daily_history_sina",
    ),
    "get_index_data.read_index_quote_sina": (
        "macro",
        "get_index_data",
        "read_index_quote_sina",
    ),
    "get_bond_yield.read_bond_yield_eastmoney": (
        "macro",
        "get_bond_yield",
        "read_bond_yield_eastmoney",
    ),
    "get_macro_indicator.read_macro_indicator_akshare": (
        "macro",
        "get_macro_indicator",
        "read_macro_indicator_akshare",
    ),
    "market_snapshot_tools.read_market_breadth_legu": (
        "market",
        "market_snapshot_tools",
        "read_market_breadth_legu",
    ),
    "market_snapshot_tools.read_market_breadth_sina": (
        "market",
        "market_snapshot_tools",
        "read_market_breadth_sina",
    ),
    "market_snapshot_tools.read_market_indices_sina": (
        "market",
        "market_snapshot_tools",
        "read_market_indices_sina",
    ),
    "market_snapshot_tools.read_market_limit_up_pool_eastmoney": (
        "market",
        "market_snapshot_tools",
        "read_market_limit_up_pool_eastmoney",
    ),
    "market_snapshot_tools.read_market_limit_down_pool_eastmoney": (
        "market",
        "market_snapshot_tools",
        "read_market_limit_down_pool_eastmoney",
    ),
    "market_snapshot_tools.read_market_broken_board_pool_eastmoney": (
        "market",
        "market_snapshot_tools",
        "read_market_broken_board_pool_eastmoney",
    ),
}
