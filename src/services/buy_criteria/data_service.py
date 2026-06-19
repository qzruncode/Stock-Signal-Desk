# src/services/buy_criteria/data_service.py
"""Unified data-fetching wrapper for criterion evaluators.

Wraps existing endpoint functions so evaluators don't import endpoints directly.
Uses a module-level cache so that ALL DataService instances in the same
process/request share cached results. Within a single analysis run, each
endpoint is called at most once regardless of how many evaluators need it.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level cache shared by all DataService instances.
# Key: "endpoint_name:symbol:args", Value: result dict.
_REQUEST_CACHE: dict[str, Any] = {}


def _clear_cache() -> None:
    """Clear the request cache. Call at the start of each new analysis request."""
    _REQUEST_CACHE.clear()


class DataService:
    """Provides data to evaluators with cross-instance request caching."""

    def _cached_call(self, cache_key: str, factory) -> Any:
        if cache_key not in _REQUEST_CACHE:
            _REQUEST_CACHE[cache_key] = factory()
        return _REQUEST_CACHE[cache_key]

    def get_stock_info(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.stock_info import get_stock_info
        return get_stock_info(symbol)

    def get_sector_list(self, sector_type: str = "industry") -> dict[str, Any]:
        key = f"sector_list:{sector_type}"
        def _fetch():
            from api.v1.endpoints.sectors import get_sector_list
            return get_sector_list(type=sector_type)
        return self._cached_call(key, _fetch)

    def get_valuation_ratios(self, symbol: str) -> dict[str, Any]:
        key = f"valuation:{symbol}"
        def _fetch():
            from api.v1.endpoints.financials import get_valuation_ratios
            return get_valuation_ratios(symbol, with_history=True)
        return self._cached_call(key, _fetch)

    def get_financials(self, symbol: str, periods: int = 4, force: bool = False) -> dict[str, Any]:
        key = f"financials:{symbol}:{periods}"
        def _fetch():
            from api.v1.endpoints.financials import get_financials
            return get_financials(symbol=symbol, periods=periods, force=force)
        return self._cached_call(key, _fetch)

    def get_price_overdraft_signal(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_price_overdraft_signal
        return get_price_overdraft_signal(symbol)

    def get_shareholder_structure(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_shareholder_structure
        return get_shareholder_structure(symbol)

    def get_sentiment(self, symbol: str, days: int = 90) -> dict[str, Any]:
        key = f"sentiment:{symbol}:{days}"
        def _fetch():
            from api.v1.endpoints.financials import get_sentiment
            return get_sentiment(symbol, days=days)
        return self._cached_call(key, _fetch)

    def get_social_sentiment(self, symbol: str, days: int = 90) -> dict[str, Any]:
        key = f"social_sentiment:{symbol}:{days}"
        def _fetch():
            from api.v1.endpoints.financials import get_social_sentiment
            return get_social_sentiment(symbol, days=days)
        return self._cached_call(key, _fetch)

    def get_risk_events(self, symbol: str, days: int = 90) -> dict[str, Any]:
        key = f"risk_events:{symbol}:{days}"
        def _fetch():
            from api.v1.endpoints.financials import get_risk_events
            return get_risk_events(symbol, days=days)
        return self._cached_call(key, _fetch)

    def get_sector_flow_industry(self) -> list[dict[str, Any]]:
        key = "sector_flow_industry"
        def _fetch():
            from api.v1.endpoints.macro import _fetch_sector_flow_industry
            return _fetch_sector_flow_industry()
        return self._cached_call(key, _fetch)

    def get_macro_indicator(self, indicator: str, months: int = 6) -> dict[str, Any]:
        key = f"macro:{indicator}:{months}"
        def _fetch():
            from api.v1.endpoints.macro import get_macro_indicator
            return get_macro_indicator(indicator=indicator, months=months)
        return self._cached_call(key, _fetch)

    def get_market_mainline_report(self) -> dict[str, Any]:
        from src.services.market_theme_service import MarketThemeService
        return MarketThemeService().get_model_report(force=False)

    def get_market_mainline_evidence(self) -> dict[str, Any]:
        from src.services.market_theme_service import MarketThemeService
        return MarketThemeService().get_evidence(force=False)

    def search_news(self, symbol: str, days: int = 90) -> dict[str, Any]:
        key = f"news:{symbol}:{days}"
        def _fetch():
            from api.v1.endpoints.financials import search_news
            return search_news(symbol=symbol, days=days, source="all", force=False)
        return self._cached_call(key, _fetch)

    def get_research_report(self, symbol: str, days: int = 365) -> dict[str, Any]:
        key = f"research:{symbol}:{days}"
        def _fetch():
            from api.v1.endpoints.financials import get_research_report
            return get_research_report(symbol=symbol, days=days, force=False)
        return self._cached_call(key, _fetch)
