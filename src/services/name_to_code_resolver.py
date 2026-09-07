# -*- coding: utf-8 -*-
"""
===================================
Name-to-Code Resolution Engine
===================================

Resolve stock name to code from exact maintained security identities.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Set, Tuple

from src.data.stock_mapping import STOCK_NAME_MAP
from src.services.stock_code_utils import is_code_like, normalize_code

logger = logging.getLogger(__name__)

_database_cache: Optional[tuple[float, Dict[str, str], Dict[str, str]]] = None
_DATABASE_CACHE_TTL = 60


def _contains_cjk(text: str) -> bool:
    """Return True when text contains CJK characters."""
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def _is_code_like(s: str) -> bool:
    """Backward-compatible wrapper of shared code-like check."""
    return is_code_like(s)


def _normalize_code(raw: str) -> Optional[str]:
    """Backward-compatible wrapper of shared code normalization."""
    return normalize_code(raw)


def _build_reverse_map_no_duplicates(
    code_to_name: Dict[str, str],
) -> Dict[str, str]:
    """
    Build name -> code map. If a name maps to multiple codes (ambiguous), exclude it.
    """
    name_to_codes: Dict[str, Set[str]] = {}
    for code, name in code_to_name.items():
        if not name or not code:
            continue
        name = name.strip()
        if name not in name_to_codes:
            name_to_codes[name] = set()
        name_to_codes[name].add(code)
    # Only include names with exactly one code
    return {
        name: next(iter(codes))
        for name, codes in name_to_codes.items()
        if len(codes) == 1
    }


def _build_local_name_indexes(
    code_to_name: Dict[str, str],
) -> Tuple[Dict[str, str], Set[str]]:
    """
    Build cached local lookup structures:
    - unique name -> code
    - ambiguous names that should fail fast
    """
    name_to_codes: Dict[str, Set[str]] = {}
    for code, name in code_to_name.items():
        if not name or not code:
            continue
        normalized_name = name.strip()
        if not normalized_name:
            continue
        name_to_codes.setdefault(normalized_name, set()).add(code)

    unique_names = {
        name: next(iter(codes))
        for name, codes in name_to_codes.items()
        if len(codes) == 1
    }
    ambiguous_names = {name for name, codes in name_to_codes.items() if len(codes) > 1}
    return unique_names, ambiguous_names


_LOCAL_REVERSE_MAP, _LOCAL_AMBIGUOUS_NAMES = _build_local_name_indexes(STOCK_NAME_MAP)


def get_database_stock_indexes() -> tuple[Dict[str, str], Dict[str, str]]:
    """Read identity indexes from the authoritative data service."""
    from src.services.market_data_client import get_market_data_client

    global _database_cache
    now = time.time()
    if _database_cache is not None and now - _database_cache[0] < _DATABASE_CACHE_TTL:
        return _database_cache[1], _database_cache[2]
    rows = get_market_data_client().securities(page_size=10000)["items"]
    code_to_name = {
        row["code"]: "".join(row["name"].split())
        for row in rows
        if row.get("code") and row.get("name")
    }
    name_to_code = _build_reverse_map_no_duplicates(code_to_name)
    _database_cache = now, name_to_code, code_to_name
    return name_to_code, code_to_name


def resolve_local_name_to_code(name: str) -> Optional[str]:
    """Resolve exact codes/aliases and the data service's maintained identities.

    This is the identity-validation boundary for model-authored atomic tool
    calls. It never contacts a provider: if the maintained security master does
    not know a name, the graph can explicitly plan ``search_stocks`` or ask
    for clarification instead of hiding a second data source inside the
    requested source tool.
    """
    if not name or not isinstance(name, str):
        return None
    s = name.strip()
    if not s:
        return None

    # 1. Input looks like code
    if _is_code_like(s):
        return _normalize_code(s)

    # 2. Local reverse map (no duplicates)
    local_reverse = _LOCAL_REVERSE_MAP
    if s in local_reverse:
        return local_reverse[s]
    if s in _LOCAL_AMBIGUOUS_NAMES:
        logger.debug(f"[NameResolver] 命中本地歧义名称，快速返回 None: {s}")
        return None

    if not _contains_cjk(s):
        return None

    # 3. Full A-share universe, maintained independently by the data service.
    database_reverse, _ = get_database_stock_indexes()
    if s in database_reverse:
        return database_reverse[s]

    return None


def resolve_name_to_code(name: str) -> Optional[str]:
    """Use the same strict, maintained identity contract for all callers."""
    return resolve_local_name_to_code(name)
