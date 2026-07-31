"""Build evidence-search queries from structured task context.

The query builder does not classify industries and contains no stock-, theme-
or wording-specific rules.  It preserves the semantic domains already resolved
by the planner and only falls back to the stock's declared industry when the
turn has no structured thesis.
"""

from __future__ import annotations

import re
from typing import Any


def structured_thesis_queries(
    stock_info: dict[str, Any],
    *,
    limit: int = 6,
) -> list[str]:
    context = stock_info.get("_investment_thesis_context")
    context = context if isinstance(context, dict) else {}
    values: list[Any] = []
    for domain in context.get("domains") or []:
        if not isinstance(domain, dict):
            continue
        values.append(domain.get("label"))
        values.extend(domain.get("board_queries") or [])
    values.append(context.get("summary"))
    values.append(stock_info.get("_investment_thesis"))
    research_scope = stock_info.get("_derived_research_scope")
    research_scope = research_scope if isinstance(research_scope, dict) else {}
    values.extend(research_scope.get("primary_labels") or [])
    values.append(stock_info.get("industry"))

    queries: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = re.sub(r"\s+", " ", str(value or "")).strip(" ，,、;；")
        normalized = query.lower()
        if len(query) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        queries.append(query[:100])
        if len(queries) >= max(1, limit):
            break
    return queries


__all__ = ["structured_thesis_queries"]
