# -*- coding: utf-8 -*-
"""Deterministic multi-domain A-share candidate discovery.

This is the runtime-owned bridge between an industry conclusion (for example
``行星滚柱丝杠、减速器、无框力矩电机``) and the synchronized A-share
universe.  It never discovers company identities from generic web results.
Every returned code comes from structured concept-board constituents and is
intersected with local ``stock_meta`` by ``get_theme_stock_candidates``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.get_theme_stock_candidates import (
    _load_local_universe,
    get_theme_stock_candidates,
)


# Product terms do not always have a same-named exchange concept board.  These
# aliases map them to the narrowest structured board currently available.  The
# result remains L1 candidate evidence and is never presented as proof that a
# company already has related orders or revenue.
_DOMAIN_BOARD_ALIASES: dict[str, tuple[str, ...]] = {
    "行星滚柱丝杠": ("机器人执行器",),
    "行星滚珠丝杠": ("机器人执行器",),
    "滚柱丝杠": ("机器人执行器",),
    "滚珠丝杠": ("机器人执行器",),
    "丝杠": ("机器人执行器",),
    "谐波减速器": ("减速器",),
    "行星减速器": ("减速器",),
    "减速器": ("减速器",),
    "无框力矩电机": ("机器人执行器",),
    "力矩电机": ("机器人执行器",),
    "空心杯电机": ("机器人执行器",),
}


def _compact(value: Any) -> str:
    return re.sub(r"[\s·•（）()\-_/]+", "", str(value or "")).lower()


def _normalize_domains(domains: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in domains or []:
        value = str(raw or "").strip(" ，,；;。")
        if not value:
            continue
        key = _compact(value)
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(value[:48])
        if len(normalized) >= 12:
            break
    return normalized


def _lookup_themes(domain: str) -> list[str]:
    compact = _compact(domain)
    for known, aliases in _DOMAIN_BOARD_ALIASES.items():
        known_compact = _compact(known)
        if compact == known_compact or known_compact in compact:
            return list(aliases)
    return [domain]


def get_domain_stock_candidates(
    domains: list[str],
    context_theme: str = "",
    limit_per_domain: int = 300,
) -> dict[str, Any]:
    requested = _normalize_domains(domains)
    if not requested:
        raise ValueError("domains 至少需要一个产业领域")
    bounded_limit = max(20, min(int(limit_per_domain or 300), 500))

    # DatabaseManager initialization is process-global and is not safe to race
    # from several first-use worker threads.  Maintain and read the synchronized
    # security master once, then share this immutable identity map across all
    # domain lookups.  This also avoids repeating full-universe database reads.
    from src.services.data_maintenance import ensure_stock_universe

    maintenance = ensure_stock_universe(trigger="agent_domain_candidates")
    local_universe = _load_local_universe()
    normalized_context = str(context_theme or "").strip()

    lookup_themes: list[str] = []
    for domain in requested:
        for theme in _lookup_themes(domain):
            if theme not in lookup_themes:
                lookup_themes.append(theme)
    use_context_filter = bool(
        normalized_context
        and len(requested) > 1
        and _compact(normalized_context) not in {_compact(value) for value in requested}
        and not any(separator in normalized_context for separator in ("、", ",", "，", ";", "；"))
    )
    if use_context_filter and normalized_context not in lookup_themes:
        lookup_themes.append(normalized_context)

    fetched: dict[str, dict[str, Any]] = {}
    workers = min(4, len(lookup_themes))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                get_theme_stock_candidates,
                theme,
                bounded_limit,
                local_universe=local_universe,
                maintenance_result=maintenance,
            ): theme
            for theme in lookup_themes
        }
        for future in as_completed(futures):
            theme = futures[future]
            try:
                fetched[theme] = future.result()
            except Exception as exc:
                fetched[theme] = {
                    "success": False,
                    "partial": False,
                    "theme": theme,
                    "items": [],
                    "matched_boards": [],
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "warnings": [],
                }

    domain_results: list[dict[str, Any]] = []
    union: dict[str, dict[str, Any]] = {}
    local_universe_count = 0
    all_warnings: list[str] = []
    all_errors: list[str] = []
    context_result = fetched.get(normalized_context) if use_context_filter else None
    context_symbols = {
        str(item.get("symbol") or "")
        for item in ((context_result or {}).get("items") or [])
        if isinstance(item, dict) and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
    }
    context_boards = [
        board for board in ((context_result or {}).get("matched_boards") or [])
        if isinstance(board, dict)
    ]

    for domain in requested:
        themes = _lookup_themes(domain)
        by_symbol: dict[str, dict[str, Any]] = {}
        matched_boards: list[dict[str, Any]] = []
        domain_warnings: list[str] = []
        domain_errors: list[str] = []
        coverage_complete = True
        successful_theme_count = 0

        for theme in themes:
            result = fetched[theme]
            local_universe_count = max(
                local_universe_count,
                int(result.get("local_universe_count") or 0),
            )
            if result.get("success"):
                successful_theme_count += 1
            coverage_complete = coverage_complete and bool(result.get("coverage_complete"))
            matched_boards.extend(
                board for board in result.get("matched_boards") or [] if isinstance(board, dict)
            )
            domain_warnings.extend(str(item) for item in result.get("warnings") or [] if item)
            domain_errors.extend(str(item) for item in result.get("errors") or [] if item)
            for item in result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "")
                if not re.fullmatch(r"\d{6}", symbol):
                    continue
                current = by_symbol.setdefault(symbol, {
                    **item,
                    "matched_domains": [],
                    "lookup_themes": [],
                })
                if domain not in current["matched_domains"]:
                    current["matched_domains"].append(domain)
                if theme not in current["lookup_themes"]:
                    current["lookup_themes"].append(theme)

        pre_context_candidate_count = len(by_symbol)
        context_filter_applied = False
        if context_symbols:
            intersection = {
                symbol: item for symbol, item in by_symbol.items() if symbol in context_symbols
            }
            if intersection:
                by_symbol = intersection
                context_filter_applied = True
            else:
                domain_warnings.append(
                    f"领域板块与上位主题“{normalized_context}”没有成分交集，"
                    "已保留未交集的领域板块候选并明确标记。"
                )
        elif use_context_filter:
            domain_warnings.append(
                f"上位主题“{normalized_context}”未取得可核验成分股，"
                "本领域未执行主题交集筛选。"
            )
        items = list(by_symbol.values())
        domain_result = {
            "domain": domain,
            "lookup_themes": themes,
            "mapping_basis": (
                ("exact_concept_board" if themes == [domain]
                 else "narrowest_structured_board_alias")
                + ("_intersected_with_context_theme" if context_filter_applied else "")
            ),
            "context_theme": normalized_context or None,
            "context_filter_applied": context_filter_applied,
            "pre_context_candidate_count": pre_context_candidate_count,
            "success": bool(items),
            "partial": bool(items) and (not coverage_complete or bool(domain_warnings or domain_errors)),
            "coverage_complete": coverage_complete and successful_theme_count == len(themes),
            "candidate_count": len(items),
            "items": items,
            "matched_boards": matched_boards,
            "context_boards": context_boards if context_filter_applied else [],
            "warnings": list(dict.fromkeys(domain_warnings)),
            "errors": [] if items else list(dict.fromkeys(domain_errors or domain_warnings)),
        }
        domain_results.append(domain_result)
        all_warnings.extend(domain_result["warnings"])
        all_errors.extend(domain_result["errors"])

        for item in items:
            symbol = item["symbol"]
            merged = union.setdefault(symbol, {
                **item,
                "matched_domains": [],
                "lookup_themes": [],
            })
            for key in ("matched_domains", "lookup_themes"):
                for value in item.get(key) or []:
                    if value not in merged[key]:
                        merged[key].append(value)

    union_items = sorted(
        union.values(),
        key=lambda item: (-len(item.get("matched_domains") or []), str(item.get("symbol") or "")),
    )
    success = any(result["success"] for result in domain_results)
    return {
        "success": success,
        "partial": success and any(not result["success"] or result["partial"] for result in domain_results),
        "requested_domains": requested,
        "context_theme": normalized_context or None,
        "local_universe_count": local_universe_count,
        "domain_results": domain_results,
        "items": union_items,
        "candidate_count": len(union_items),
        "returned_count": len(union_items),
        "source_scope": "structured_concept_constituents_intersected_with_local_stock_meta",
        "decision_boundary": (
            "候选仅证明结构化概念板块成员关系与本地证券身份有效；"
            "不等同相关订单、客户验证、收入兑现或投资建议。"
        ),
        "warnings": list(dict.fromkeys(all_warnings)),
        "errors": [] if success else list(dict.fromkeys(all_errors)),
    }


TOOL = ToolSpec(
    name="get_domain_stock_candidates",
    description=(
        "按一个或多个产业领域从结构化概念板块成分股中查找A股候选，并与本地完整证券库核验。"
        "这是领域找股的唯一入口；不得用search_stocks或通用网页搜索替代。"
    ),
    parameters=object_schema({
        "domains": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 12,
            "description": "需要分别找股的产业领域，例如行星滚柱丝杠、减速器、无框力矩电机",
        },
        "context_theme": {"type": "string", "description": "上位产业主题，可留空"},
        "limit_per_domain": {"type": "integer", "minimum": 20, "maximum": 500, "default": 300},
    }, required=("domains",)),
    executor=get_domain_stock_candidates,
    category="research",
)


__all__ = ["TOOL", "get_domain_stock_candidates"]
