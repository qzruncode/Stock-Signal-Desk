# src/services/buy_criteria/data_service.py
"""Unified data-fetching wrapper for criterion evaluators.

Wraps existing endpoint functions so evaluators don't import endpoints directly.
All functions return plain dicts and use per-day SQLite caching upstream.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DataService:
    """Provides data to evaluators by wrapping existing endpoint functions."""

    def get_stock_info(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.stock_info import get_stock_info
        return get_stock_info(symbol)

    def get_sector_list(self, sector_type: str = "industry") -> dict[str, Any]:
        from api.v1.endpoints.sectors import get_sector_list
        return get_sector_list(type=sector_type)

    def get_valuation_ratios(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_valuation_ratios
        return get_valuation_ratios(symbol, with_history=True)

    def get_financials(self, symbol: str, periods: int = 4, force: bool = False) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_financials
        return get_financials(symbol=symbol, periods=periods, force=force)

    def get_price_overdraft_signal(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_price_overdraft_signal
        return get_price_overdraft_signal(symbol)

    def get_shareholder_structure(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_shareholder_structure
        return get_shareholder_structure(symbol)

    def get_sentiment(self, symbol: str, days: int = 90) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_sentiment
        return get_sentiment(symbol, days=days)

    def get_social_sentiment(self, symbol: str, days: int = 90) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_social_sentiment
        return get_social_sentiment(symbol, days=days)

    def get_risk_events(self, symbol: str, days: int = 90) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_risk_events
        return get_risk_events(symbol, days=days)

    def get_sector_flow_industry(self) -> list[dict[str, Any]]:
        from api.v1.endpoints.macro import _fetch_sector_flow_industry
        return _fetch_sector_flow_industry()

    def get_macro_indicator(self, indicator: str, months: int = 6) -> dict[str, Any]:
        from api.v1.endpoints.macro import get_macro_indicator
        return get_macro_indicator(indicator=indicator, months=months)

    def get_market_mainline_report(self) -> dict[str, Any]:
        from src.services.market_theme_service import MarketThemeService
        return MarketThemeService().get_model_report(force=False)

    def get_market_mainline_evidence(self) -> dict[str, Any]:
        from src.services.market_theme_service import MarketThemeService
        return MarketThemeService().get_evidence(force=False)

    def search_news(self, symbol: str, days: int = 90) -> dict[str, Any]:
        from api.v1.endpoints.financials import search_news
        return search_news(symbol=symbol, days=days, source="all", force=False)

    def get_research_report(self, symbol: str, days: int = 365) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_research_report
        return get_research_report(symbol=symbol, days=days, force=False)

