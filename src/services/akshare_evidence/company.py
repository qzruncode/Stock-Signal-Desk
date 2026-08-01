"""Company evidence facade shared by Agent tools and decision evaluators."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Iterable

from src.tools._akshare import bare_symbol

from .company_events import get_corporate_event_evidence
from .company_financial import get_financial_event_evidence
from .company_ownership import get_ownership_evidence
from .company_trading import get_trading_evidence

_SECTION_FETCHERS: dict[str, Callable[..., dict[str, Any]]] = {
    "ownership": get_ownership_evidence,
    "financial_events": get_financial_event_evidence,
    "corporate_events": get_corporate_event_evidence,
    "trading_evidence": get_trading_evidence,
}


def get_company_evidence(
    symbol: str,
    *,
    sections: Iterable[str] | None = None,
    days: int = 730,
    report_period_count: int = 4,
) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if len(code) != 6 or not code.isdecimal():
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    selected = list(dict.fromkeys(sections or _SECTION_FETCHERS))
    unknown = [name for name in selected if name not in _SECTION_FETCHERS]
    if unknown:
        raise ValueError("不支持的证据域: " + ", ".join(unknown))

    kwargs_by_section = {
        "ownership": {"days": days},
        "financial_events": {"report_period_count": report_period_count},
        "corporate_events": {"days": days},
        "trading_evidence": {"days": min(days, 365)},
    }
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(selected)))) as pool:
        futures = {
            pool.submit(_SECTION_FETCHERS[name], code, **kwargs_by_section[name]): name
            for name in selected
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = {
                    "section": name,
                    "symbol": code,
                    "datasets": {},
                    "available_dataset_count": 0,
                    "required_dataset_count": 0,
                    "coverage_complete": False,
                    "success": False,
                    "partial": False,
                    "errors": [f"{type(exc).__name__}: {str(exc)[:400]}"],
                    "warnings": [],
                    "data_time": None,
                    "is_stale": None,
                    "freshness_unknown": True,
                }
    ordered = {name: results[name] for name in selected}
    errors = [f"{name}: {error}" for name, result in ordered.items() for error in result.get("errors") or []]
    warnings = [str(warning) for result in ordered.values() for warning in result.get("warnings") or []]
    data_times = [str(result["data_time"]) for result in ordered.values() if result.get("data_time")]
    successful = [result for result in ordered.values() if result.get("success")]
    return {
        "symbol": code,
        "sections": ordered,
        "requested_sections": selected,
        "available_section_count": len(successful),
        "required_section_count": len(selected),
        "coverage_complete": all(result.get("coverage_complete") for result in ordered.values()),
        "retrieval_only": True,
        "semantic_status": "model_required",
        "source": "AKShare structured company evidence",
        "source_repository": "https://github.com/akfamily/akshare",
        "success": bool(successful),
        "partial": bool(successful) and (bool(errors) or len(successful) != len(selected)),
        "errors": errors,
        "warnings": list(dict.fromkeys(warnings)),
        "data_time": max(data_times) if data_times else None,
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "is_stale": None,
        "freshness_unknown": not bool(data_times),
    }


__all__ = ["get_company_evidence"]
