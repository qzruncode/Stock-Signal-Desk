# -*- coding: utf-8 -*-
"""Shared stock-symbol normalization for registered tools."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def resolve_symbol(value: str) -> str:
    """Resolve a stock name/code into a normalized stock code when possible."""
    raw = (value or "").strip()
    if not raw:
        return raw
    try:
        from src.services.name_to_code_resolver import resolve_name_to_code

        # Tool execution must never guess a security from a similar-looking
        # company name.  Fuzzy correction remains available to interactive
        # search surfaces, but financial/valuation calls require an exact
        # code, exact name or exact alias.
        return resolve_name_to_code(raw) or raw
    except Exception as exc:
        logger.debug("symbol resolve failed for %s: %s", raw, exc)
        return raw


def resolve_symbols_csv(value: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in (value or "").replace("，", ",").split(","):
        if not part.strip():
            continue
        symbol = resolve_symbol(part).strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result


def resolve_securities_csv(value: str) -> tuple[list[dict[str, str]], list[str]]:
    """Resolve a comma-separated security list with verified display names.

    Unlike ``resolve_symbols_csv``, unresolved names are not silently passed
    through as if they were codes.  The caller receives an explicit unresolved
    list and can produce a partial, auditable result.
    """
    from src.services.name_to_code_resolver import get_database_stock_indexes

    _, code_to_name = get_database_stock_indexes()
    resolved: list[dict[str, str]] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,，、;；\n]+", value or ""):
        raw = part.strip()
        if not raw:
            continue
        code = resolve_symbol(raw).strip()
        if not re.fullmatch(r"\d{6}", code):
            unresolved.append(raw)
            continue
        if code in seen:
            continue
        seen.add(code)
        resolved.append({"input": raw, "symbol": code, "name": code_to_name.get(code, raw)})
    return resolved, unresolved


def find_securities_in_text(text: str, *, limit: int = 20) -> list[dict[str, str]]:
    """Extract locally verified A-share entities from arbitrary conversation text."""
    from src.services.name_to_code_resolver import get_database_stock_indexes

    name_to_code, code_to_name = get_database_stock_indexes()
    source = str(text or "")
    matches: list[tuple[int, str, str]] = []
    for name, code in name_to_code.items():
        # Two-character names create many accidental matches in prose.  Direct
        # user/tool arguments can still resolve them; automatic history mining
        # deliberately requires a stronger name signal.
        if len(name) < 3:
            continue
        position = source.find(name)
        if position >= 0:
            matches.append((position, name, code))
    for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", source):
        code = match.group(1)
        if code in code_to_name:
            matches.append((match.start(), code_to_name[code], code))

    matches.sort(key=lambda item: item[0])
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, name, code in matches:
        if code in seen:
            continue
        seen.add(code)
        results.append({"name": name, "symbol": code})
        if len(results) >= max(1, limit):
            break
    return results


def find_securities_in_markdown_table_first_column(
    text: str,
    *,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Extract securities explicitly listed as Markdown table row subjects.

    Referential follow-ups such as ``这些公司`` should inherit the companies
    the previous answer actually listed, not every stock name mentioned in
    caveats, examples, or source prose.  Research answers in this project put
    the row subject in the first table column, which gives us a deterministic
    and much less noisy scope boundary.
    """
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or re.fullmatch(r"[-: ]+", cells[0] or ""):
            continue
        for entity in find_securities_in_text(cells[0], limit=4):
            if entity["symbol"] in seen:
                continue
            seen.add(entity["symbol"])
            results.append(entity)
            if len(results) >= max(1, limit):
                return results
    return results


def normalize_tool_security_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Normalize common ``symbol``/``symbols`` arguments before tool execution."""
    normalized = dict(arguments)
    unresolved: list[str] = []
    if isinstance(normalized.get("symbol"), str):
        raw = normalized["symbol"].strip()
        code = resolve_symbol(raw).strip()
        if re.fullmatch(r"\d{6}", code):
            normalized["symbol"] = code
        elif raw:
            unresolved.append(raw)
    if isinstance(normalized.get("symbols"), str):
        resolved, missing = resolve_securities_csv(normalized["symbols"])
        normalized["symbols"] = ",".join(item["symbol"] for item in resolved)
        unresolved.extend(missing)
    return normalized, unresolved


__all__ = [
    "find_securities_in_markdown_table_first_column",
    "find_securities_in_text",
    "normalize_tool_security_arguments",
    "resolve_securities_csv",
    "resolve_symbol",
    "resolve_symbols_csv",
]
