from __future__ import annotations
from typing import Any, Iterable


def get_company_evidence(
    symbol: str,
    *,
    sections: Iterable[str] | None = None,
    days: int = 730,
    report_period_count: int = 4,
) -> dict[str, Any]:
    from src.services.market_data_client import read_source

    arguments = {
        "symbol": symbol,
        "sections": sections,
        "days": days,
        "report_period_count": report_period_count,
    }
    if "sections" in arguments and arguments["sections"] is not None:
        arguments["sections"] = list(arguments["sections"])
    return read_source("company.evidence", arguments)
