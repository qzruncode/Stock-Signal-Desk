"""Shared financial-statement loader used by statement-specific tools."""

from __future__ import annotations

from typing import Any

from src.tools._financial_data import get_financial_bundle, get_financial_section


def get_financial_statements(symbol: str, periods: int, *, use_cache: bool = True) -> dict[str, Any]:
    return get_financial_bundle(symbol, periods, use_cache=use_cache)


def statement_view(result: dict[str, Any], section: str) -> dict[str, Any]:
    return {
        "symbol": result.get("symbol"),
        "requested_periods": result.get("requested_periods"),
        "periods": len(result.get(section) or []),
        section: result.get(section) or [],
        "amount_unit": result.get("amount_unit"),
        "ratio_unit": result.get("ratio_unit"),
        "currency": result.get("currency"),
        "basis": result.get(
            {
                "balance_sheet": "balance_sheet_basis",
                "income_statement": "income_statement_basis",
                "cashflow": "cashflow_basis",
            }[section]
        ),
        "source": result.get("source"),
        "source_url": result.get("source_url"),
        "success": bool(result.get(section)),
        "partial": result.get("partial"),
        "errors": result.get("errors") or [],
        "data_time": result.get("data_time"),
        "is_stale": result.get("is_stale"),
        "fallback_used": result.get("fallback_used"),
        "_cached": result.get("_cached"),
        "_fetched_at": result.get("_fetched_at"),
    }


def get_statement(
    symbol: str,
    section: str,
    periods: int,
    *,
    use_cache: bool = True,
    local_identity: bool = False,
) -> dict[str, Any]:
    """Return one statement without requesting the other two statements."""
    return get_financial_section(
        symbol,
        section,
        periods,
        use_cache=use_cache,
        local_identity=local_identity,
    )
