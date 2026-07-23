# src/services/buy_criteria/data_service.py
"""Unified data-fetching wrapper for criterion evaluators.

Wraps existing endpoint functions so evaluators don't import endpoints directly.
Uses a module-level cache so that ALL DataService instances in the same
process/request share cached results. Within a single analysis run, each
endpoint is called at most once regardless of how many evaluators need it.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# One cache per analysis worker. All evaluator instances for the same stock run
# share it, while parallel stock evaluations cannot clear or contaminate each
# other's evidence.
_REQUEST_LOCAL = threading.local()


def _request_cache() -> dict[str, Any]:
    cache = getattr(_REQUEST_LOCAL, "cache", None)
    if cache is None:
        cache = {}
        _REQUEST_LOCAL.cache = cache
    return cache


def _clear_cache() -> None:
    """Clear the request cache. Call at the start of each new analysis request."""
    _REQUEST_LOCAL.cache = {}


class DataService:
    """Provides data to evaluators with cross-instance request caching."""

    def _cached_call(self, cache_key: str, factory) -> Any:
        cache = _request_cache()
        if cache_key not in cache:
            cache[cache_key] = factory()
        return cache[cache_key]

    def get_stock_info(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.stock_info import get_stock_info
        return get_stock_info(symbol)

    def get_sector_list(self, sector_type: str = "industry") -> dict[str, Any]:
        key = f"sector_list:{sector_type}"
        def _fetch():
            from api.v1.endpoints.sectors import get_sector_list
            return get_sector_list(type=sector_type)
        return self._cached_call(key, _fetch)

    def get_investment_thesis_candidates(
        self,
        thesis_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Resolve a referenced industry thesis through structured board data.

        This is deliberately candidate/membership evidence only.  It lets the
        evaluators retain the exact industry direction from the conversation
        without treating a concept-board label as proof of orders or revenue.
        """
        from src.agent.result_contracts import InvestmentThesisContext

        try:
            context = InvestmentThesisContext.model_validate(thesis_context or {})
        except Exception as exc:
            return {
                "success": False,
                "requested_domains": [],
                "items": [],
                "warnings": [],
                "errors": [f"产业方向结构无效：{exc}"],
            }
        domain_specs = [domain.model_dump() for domain in context.domains]
        if not domain_specs:
            return {
                "success": False,
                "requested_domains": [],
                "items": [],
                "warnings": ["本轮没有已绑定的产业领域"],
                "errors": [],
            }
        key = "investment_thesis_candidates:" + "|".join(
            f"{item['label']}=>{','.join(item['board_queries'])}" for item in domain_specs
        )

        def _fetch():
            from src.tools.get_domain_stock_candidates import get_domain_stock_candidates

            return get_domain_stock_candidates(domain_specs, limit_per_domain=300)

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

    def get_sector_flow(
        self,
        sector_type: str,
        period: str,
    ) -> dict[str, Any]:
        key = f"sector_flow:{sector_type}:{period}"

        def _fetch():
            from src.tools.get_sector_flow import get_sector_flow

            return get_sector_flow(
                type=sector_type,
                period=period,
                top_n=30,
            )

        return self._cached_call(key, _fetch)

    def get_macro_indicator(self, indicator: str, months: int = 6) -> dict[str, Any]:
        key = f"macro:{indicator}:{months}"
        def _fetch():
            from api.v1.endpoints.macro import get_macro_indicator
            return get_macro_indicator(indicator=indicator, months=months)
        return self._cached_call(key, _fetch)

    def get_market_mainline_report(self) -> dict[str, Any]:
        def _fetch():
            from src.services.market_theme_service import MarketThemeService

            service = MarketThemeService()
            # A strict per-stock worker must never launch the global daily
            # report background job: the worker is intentionally short-lived,
            # so that task would either be abandoned or delay every stock.
            report = service.get_model_report(force=False, trigger_generation=False)
            if (
                not report.get("report_pending")
                and report.get("as_of_date")
                and report.get("current_mainlines")
            ):
                return report

            # Reuse a warm multi-source evidence cache when available, but do
            # not start a second 35-second global aggregation inside every
            # isolated stock worker.  The evaluator separately fetches the
            # uncompressed current concept/industry layer.
            evidence = service.get_cached_evidence() or {}
            current_themes = [
                item for item in (evidence.get("current_themes") or [])
                if isinstance(item, dict) and item.get("name")
            ]
            if not current_themes:
                return report

            future_themes = [
                item for item in (evidence.get("next_themes") or [])
                if isinstance(item, dict) and item.get("name")
            ]
            generated_at = str(evidence.get("generated_at") or evidence.get("data_time") or "")
            as_of_date = str(evidence.get("data_time") or generated_at)[:10]
            names = "、".join(str(item.get("name")) for item in current_themes[:3])
            return {
                "report_pending": False,
                "report_source": "current_evidence_fallback",
                "as_of_date": as_of_date,
                "overview": f"当前多源市场证据识别出的主线包括：{names}。",
                "market_stage": evidence.get("market_stage") or {},
                "current_mainlines": [
                    {
                        "name": item.get("name"),
                        "rank": item.get("rank_label"),
                        "stage": item.get("stage"),
                        "branches": item.get("components") or [],
                        "reason": item.get("thesis") or item.get("stage_reason"),
                        "focus": item.get("expectation_view"),
                        "evidence": item.get("evidence") or [],
                        "triggers": [],
                    }
                    for item in current_themes[:5]
                ],
                "future_mainlines": [
                    {
                        "name": item.get("name"),
                        "stage": item.get("stage_hint") or "候选观察期",
                        "branches": [],
                        "reason": item.get("why_now"),
                        "evidence": [],
                        "triggers": [item.get("trigger")] if item.get("trigger") else [],
                    }
                    for item in future_themes[:5]
                ],
                "source_summary": evidence.get("source_summary") or {},
            }

        return self._cached_call("market_mainline_report", _fetch)

    def get_market_mainline_evidence(self) -> dict[str, Any]:
        from src.services.market_theme_service import MarketThemeService
        return MarketThemeService().get_evidence(force=False)

    def search_news(self, symbol: str, days: int = 90) -> dict[str, Any]:
        key = f"news:{symbol}:{days}"
        def _fetch():
            from api.v1.endpoints.financials import search_news
            return search_news(symbol=symbol, days=days, source="all", force=False)
        return self._cached_call(key, _fetch)

    def search_industry_news(
        self,
        query: str,
        *,
        days: int = 90,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search internal finance feeds for an industry/topic, never as a symbol."""
        normalized = str(query or "").strip()
        key = f"industry_news:{normalized}:{days}:{limit}"

        def _fetch():
            from src.tools.search_financial_news import search_financial_news

            return search_financial_news(
                normalized,
                topic="industry",
                subjects=[normalized],
                days=days,
                limit=limit,
                include_content=False,
                fallback_to_web=True,
            )

        return self._cached_call(key, _fetch)

    def get_research_report(self, symbol: str, days: int = 365) -> dict[str, Any]:
        key = f"research:{symbol}:{days}"
        def _fetch():
            from api.v1.endpoints.financials import get_research_report
            return get_research_report(symbol=symbol, days=days, force=False)
        return self._cached_call(key, _fetch)

    def get_business_segments(self, symbol: str, periods: int = 2) -> dict[str, Any]:
        key = f"business_segments:{symbol}:{periods}"
        def _fetch():
            from src.tools.get_business_segments import get_business_segments
            return get_business_segments(symbol, category="all", periods=periods)
        return self._cached_call(key, _fetch)

    def get_announcements(self, symbol: str, days: int = 365, limit: int = 50) -> dict[str, Any]:
        key = f"announcements:{symbol}:{days}:{limit}"
        def _fetch():
            from src.tools.get_announcements import get_announcements
            return get_announcements(symbol, days=days, type="all", limit=limit)
        return self._cached_call(key, _fetch)

    def get_catalyst_document_passages(
        self,
        symbol: str,
        announcements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Read formal report bodies and return all explicit forward windows."""
        art_codes = [
            str(item.get("url") or "")
            for item in announcements
            if isinstance(item, dict)
        ]
        key = f"catalyst_documents:{symbol}:{hash(tuple(art_codes))}"

        def _fetch():
            from src.services.catalyst_evidence import get_formal_forward_evidence

            return get_formal_forward_evidence(symbol, announcements)

        return self._cached_call(key, _fetch)

    def get_formal_business_evidence(
        self,
        symbol: str,
        announcements: list[dict[str, Any]],
        *,
        thesis: str,
        thesis_context: dict[str, Any] | None,
        research_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read formal report bodies and retrieve thesis-relevant passages."""
        art_codes = tuple(
            str(item.get("url") or "")
            for item in announcements
            if isinstance(item, dict)
        )
        context_key = repr(thesis_context)
        scope_key = repr(research_scope)
        key = (
            f"formal_business:{symbol}:"
            f"{hash((art_codes, thesis, context_key, scope_key))}"
        )

        def _fetch():
            from src.services.catalyst_evidence import get_formal_business_evidence

            return get_formal_business_evidence(
                symbol,
                announcements,
                thesis=thesis,
                thesis_context=thesis_context,
                research_scope=research_scope,
            )

        return self._cached_call(key, _fetch)

    def get_report_schedule(self, symbol: str) -> dict[str, Any]:
        key = f"report_schedule:{symbol}"

        def _fetch():
            from src.services.catalyst_evidence import get_report_schedule

            return get_report_schedule(symbol)

        return self._cached_call(key, _fetch)

    def get_technical_indicators(self, symbol: str, count: int = 120) -> dict[str, Any]:
        key = f"technical:{symbol}:{count}"
        def _fetch():
            from src.tools.get_technical_indicators import get_technical_indicators
            return get_technical_indicators(symbol, count=count)
        return self._cached_call(key, _fetch)

    def get_realtime_quote(self, symbol: str) -> dict[str, Any]:
        key = f"realtime_quote:{symbol}"
        def _fetch():
            from src.tools.get_realtime_quotes import get_realtime_quotes
            result = get_realtime_quotes([symbol])
            items = result.get("items") or []
            return items[0] if items and isinstance(items[0], dict) else {}
        return self._cached_call(key, _fetch)
