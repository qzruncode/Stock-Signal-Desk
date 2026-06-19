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

    def get_sector_flow_industry(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.macro import _fetch_sector_flow_industry
        return _fetch_sector_flow_industry(symbol)

    def get_industry_cycle_report(self, symbol: str) -> dict[str, Any]:
        from src.services.industry_cycle_service import IndustryCycleService
        return IndustryCycleService().get_report(symbol=symbol)

    def get_catalyst_data(self, symbol: str) -> dict[str, Any]:
        """Get catalyst data — wraps the catalyst analysis endpoint."""
        try:
            from api.v1.endpoints.catalyst import get_catalyst_analysis
            return get_catalyst_analysis(symbol)
        except (ImportError, AttributeError):
            logger.warning("[DataService] catalyst endpoint not available")
            return {}
