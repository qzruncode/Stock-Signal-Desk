"""Shared financial-statement loader used by statement-specific tools."""

from __future__ import annotations

from src.tools._financial_data import get_financial_section


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
