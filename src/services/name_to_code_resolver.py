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

# AkShare result cache: (timestamp, name_to_code_dict)
_akshare_cache: Optional[tuple[float, Dict[str, str]]] = None
_AKSHARE_CACHE_TTL = 1800  # 30 MIN
_database_cache: Optional[tuple[float, Dict[str, str], Dict[str, str]]] = None
_DATABASE_CACHE_TTL = 300


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
    return {name: next(iter(codes)) for name, codes in name_to_codes.items() if len(codes) == 1}


def _build_local_name_indexes(code_to_name: Dict[str, str]) -> Tuple[Dict[str, str], Set[str]]:
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

    unique_names = {name: next(iter(codes)) for name, codes in name_to_codes.items() if len(codes) == 1}
    ambiguous_names = {name for name, codes in name_to_codes.items() if len(codes) > 1}
    return unique_names, ambiguous_names


_LOCAL_REVERSE_MAP, _LOCAL_AMBIGUOUS_NAMES = _build_local_name_indexes(STOCK_NAME_MAP)


def get_database_stock_indexes() -> tuple[Dict[str, str], Dict[str, str]]:
    """Return authoritative active A-share name/code indexes from ``stock_meta``.

    ``STOCK_NAME_MAP`` is intentionally small and cannot resolve most current
    A-share names.  The Stocks page already maintains a full local universe,
    so Agent tools must use that same source before the exact-name network
    refresh. A short cache keeps per-tool resolution inexpensive.
    """
    global _database_cache
    now = time.time()
    if _database_cache is not None and now - _database_cache[0] < _DATABASE_CACHE_TTL:
        return _database_cache[1], _database_cache[2]

    try:
        from src.storage import DatabaseManager, StockMeta

        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            rows = session.query(StockMeta.code, StockMeta.name).filter(StockMeta.status == "active").all()
        code_to_name = {
            str(code).strip(): "".join(str(name).split())
            for code, name in rows
            if str(code or "").strip() and str(name or "").strip()
        }
        name_to_code = _build_reverse_map_no_duplicates(code_to_name)
        _database_cache = (now, name_to_code, code_to_name)
        return name_to_code, code_to_name
    except Exception as exc:
        logger.warning("[NameResolver] local stock_meta lookup failed: %s", exc)
        return {}, {}


def _get_akshare_name_to_code() -> Optional[Dict[str, str]]:
    """Fetch A-share name->code from AkShare, with cache."""
    global _akshare_cache
    now = time.time()
    if _akshare_cache is not None and (now - _akshare_cache[0]) < _AKSHARE_CACHE_TTL:
        return _akshare_cache[1]
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            return None
        code_to_name = {}
        for _, row in df.iterrows():
            code = row.get("code")
            name = row.get("name")
            if code is None or name is None:
                continue
            code_str = str(code).strip()
            # Strip .SH/.SZ suffix
            if "." in code_str:
                base, suffix = code_str.rsplit(".", 1)
                if suffix.upper() in ("SH", "SZ", "SS") and base.isdigit():
                    code_str = base
            code_to_name[code_str] = str(name).strip()
        result = _build_reverse_map_no_duplicates(code_to_name)
        _akshare_cache = (now, result)
        logger.info(f"[NameResolver] AkShare cache loaded: {len(result)} name->code mappings")
        return result
    except Exception as e:
        logger.warning(f"[NameResolver] AkShare fallback failed: {e}")
        return None


def resolve_local_name_to_code(name: str) -> Optional[str]:
    """Resolve one security identity from code and maintained local indexes only.

    This is the identity-validation boundary for model-authored atomic tool
    calls.  It must remain free of provider requests: if the local master does
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

    # 2b. Full local A-share universe maintained by the Stocks page.
    database_reverse, _ = get_database_stock_indexes()
    if s in database_reverse:
        return database_reverse[s]

    return None


def resolve_name_to_code(name: str) -> Optional[str]:
    """Resolve one security code, using a legacy online fallback when needed.

    Non-Agent compatibility callers retain the historical exact AkShare
    fallback.  LangGraph model tool dispatch uses ``resolve_local_name_to_code``
    instead so a direct source action never performs a hidden identity lookup.
    """
    if not name or not isinstance(name, str):
        return None
    s = name.strip()
    if not s:
        return None

    local = resolve_local_name_to_code(s)
    if local:
        return local

    # Preserve the historical ambiguity rule.  An ambiguous static alias must
    # never be "resolved" by a provider-side name lookup.
    if s in _LOCAL_AMBIGUOUS_NAMES:
        return None

    # Skip the A-share network identity refresh for non-CJK free text.
    if not _contains_cjk(s):
        logger.debug(
            "[NameResolver] skip CJK-only identity refresh for non-CJK input: %s",
            s,
        )
        return None

    # 3. AkShare exact-name fallback
    akshare_map = _get_akshare_name_to_code()
    if akshare_map and s in akshare_map:
        logger.debug(f"[NameResolver] 命中 AkShare 映射: {s} -> {akshare_map[s]}")
        return akshare_map[s]

    logger.debug("[NameResolver] exact identity resolution failed: %s", s)
    return None
