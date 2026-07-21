# -*- coding: utf-8 -*-
"""Validated multi-domain A-share candidate discovery.

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

from src.agent.result_contracts import DomainBoardQuerySpec
from src.tools.base import ToolSpec, object_schema
from src.tools.get_theme_stock_candidates import (
    _load_local_universe,
    get_theme_stock_candidates,
)


def _compact(value: Any) -> str:
    return re.sub(r"[\s·•（）()\-_/]+", "", str(value or "")).lower()


def _common_substring_length(left: str, right: str) -> int:
    a, b = _compact(left), _compact(right)
    previous = [0] * (len(b) + 1)
    best = 0
    for char_a in a:
        current = [0]
        for index, char_b in enumerate(b, 1):
            value = previous[index - 1] + 1 if char_a == char_b else 0
            current.append(value)
            best = max(best, value)
        previous = current
    return best


def _normalize_domain_specs(domains: list[Any]) -> list[DomainBoardQuerySpec]:
    """Validate planner-owned resolution objects.

    Plain strings remain accepted for internal callers, but they are treated
    only as exact board names.  This compatibility path deliberately performs
    no semantic aliasing: free-form product interpretation belongs to the
    planner supplied with the live board catalog.
    """
    normalized: list[DomainBoardQuerySpec] = []
    seen: set[str] = set()
    for raw in domains or []:
        if isinstance(raw, str):
            value = raw.strip(" ，,；;。")
            if not value:
                continue
            spec = DomainBoardQuerySpec(
                label=value[:64],
                board_queries=[value[:64]],
                mapping_type="exact_board",
                rationale="内部兼容调用：仅按同名结构化板块查询，未执行语义别名扩展。",
            )
        else:
            spec = DomainBoardQuerySpec.model_validate(raw)
        key = _compact(spec.label)
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(spec)
        if len(normalized) >= 12:
            break
    return normalized


def get_domain_stock_candidates(
    domains: list[Any],
    context_theme: str = "",
    limit_per_domain: int = 300,
) -> dict[str, Any]:
    domain_specs = _normalize_domain_specs(domains)
    if not domain_specs:
        raise ValueError("domains 至少需要一个产业领域")
    requested = [spec.label for spec in domain_specs]
    bounded_limit = max(20, min(int(limit_per_domain or 300), 500))
    # ``limit_per_domain`` is an output cap, not a retrieval cap.  Intersecting
    # two independently truncated top-N lists produces false zero-overlap and
    # previously triggered an unsafe fallback to unrelated domain boards.
    discovery_limit = 1000

    # DatabaseManager initialization is process-global and is not safe to race
    # from several first-use worker threads.  Maintain and read the synchronized
    # security master once, then share this immutable identity map across all
    # domain lookups.  This also avoids repeating full-universe database reads.
    from src.services.data_maintenance import ensure_stock_universe

    maintenance = ensure_stock_universe(trigger="agent_domain_candidates")
    local_universe = _load_local_universe()
    normalized_context = str(context_theme or "").strip()
    guarded_specs: list[DomainBoardQuerySpec] = []
    for spec in domain_specs:
        if spec.mapping_type != "proxy_board" or not normalized_context:
            guarded_specs.append(spec)
            continue
        accepted = [
            board for board in spec.board_queries
            if max(
                _common_substring_length(board, spec.label),
                _common_substring_length(board, normalized_context),
            ) >= 3
        ]
        rejected = [board for board in spec.board_queries if board not in accepted]
        if not rejected:
            guarded_specs.append(spec)
            continue
        guarded_specs.append(spec.model_copy(update={
            "board_queries": accepted,
            "mapping_type": "proxy_board" if accepted else "unresolved",
            "rationale": (
                spec.rationale + " " if spec.rationale else ""
            ) + "已拒绝缺乏领域/上位主题语义邻接的跨场景代理板块：" + "、".join(rejected),
            "unresolved_parts": list(dict.fromkeys([
                *spec.unresolved_parts,
                *(f"跨场景板块 {board}" for board in rejected),
            ])),
        }))
    domain_specs = guarded_specs
    inferred_context_themes = [normalized_context] if normalized_context else []

    lookup_themes: list[str] = []
    for spec in domain_specs:
        for theme in spec.board_queries:
            if theme not in lookup_themes:
                lookup_themes.append(theme)
    use_context_filter = bool(
        normalized_context
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
                discovery_limit,
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
    context_coverage_complete = bool(
        context_result
        and context_result.get("success")
        and context_result.get("coverage_complete")
    )
    context_symbols = {
        str(item.get("symbol") or "")
        for item in ((context_result or {}).get("items") or [])
        if isinstance(item, dict) and re.fullmatch(r"\d{6}", str(item.get("symbol") or ""))
    }
    context_boards = [
        board for board in ((context_result or {}).get("matched_boards") or [])
        if isinstance(board, dict)
    ]

    for domain_spec in domain_specs:
        domain = domain_spec.label
        themes = list(domain_spec.board_queries)
        by_symbol: dict[str, dict[str, Any]] = {}
        matched_boards: list[dict[str, Any]] = []
        rejected_boards: list[dict[str, Any]] = []
        domain_warnings: list[str] = []
        domain_errors: list[str] = []
        coverage_complete = (
            domain_spec.mapping_type != "unresolved"
            and not domain_spec.unresolved_parts
        )
        successful_theme_count = 0

        if domain_spec.unresolved_parts:
            domain_warnings.append(
                "未解析子领域：" + "、".join(domain_spec.unresolved_parts)
            )
        if not themes:
            coverage_complete = False
            domain_errors.append(
                "当前完整板块目录中没有可验证的窄板块映射，未执行近似板块取数。"
            )

        for theme in themes:
            result = fetched[theme]
            local_universe_count = max(
                local_universe_count,
                int(result.get("local_universe_count") or 0),
            )
            exact_theme_complete = bool(
                result.get("success") and result.get("coverage_complete")
            )
            if exact_theme_complete:
                successful_theme_count += 1
            coverage_complete = coverage_complete and exact_theme_complete
            domain_warnings.extend(str(item) for item in result.get("warnings") or [] if item)
            domain_errors.extend(str(item) for item in result.get("errors") or [] if item)
            if not exact_theme_complete:
                rejected_boards.extend(
                    board for board in result.get("matched_boards") or [] if isinstance(board, dict)
                )
                domain_errors.append(
                    f"查询板块“{theme}”未取得完整精确板块成分，"
                    "已拒绝近似或跨行业板块候选。"
                )
                continue
            matched_boards.extend(
                board for board in result.get("matched_boards") or [] if isinstance(board, dict)
            )
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
        context_filter_applied = use_context_filter
        if use_context_filter and context_coverage_complete:
            had_domain_candidates = bool(by_symbol)
            intersection = {
                symbol: item for symbol, item in by_symbol.items() if symbol in context_symbols
            }
            by_symbol = intersection
            if had_domain_candidates and not intersection:
                domain_errors.append(
                    f"领域板块与上位主题“{normalized_context}”没有成分交集，"
                    "按主题约束返回 0 只，未回退到未交集候选。"
                )
        elif use_context_filter:
            by_symbol = {}
            coverage_complete = False
            context_errors = [
                str(item) for item in ((context_result or {}).get("errors") or []) if item
            ]
            domain_errors.extend(context_errors)
            domain_errors.append(
                f"上位主题“{normalized_context}”未取得完整精确板块成分，"
                "已停止本领域候选输出。"
            )
        post_context_candidate_count = len(by_symbol)
        items = list(by_symbol.values())[:bounded_limit]
        domain_result = {
            "domain": domain,
            "lookup_themes": themes,
            "mapping_type": domain_spec.mapping_type,
            "mapping_rationale": domain_spec.rationale,
            "unresolved_parts": list(domain_spec.unresolved_parts),
            "mapping_basis": ({
                "exact_board": "catalog_exact_board",
                "proxy_board": "catalog_proxy_board",
                "unresolved": "catalog_unresolved",
            }[domain_spec.mapping_type]
                + ("_intersected_with_context_theme" if context_filter_applied else "")),
            "context_theme": normalized_context or None,
            "context_filter_applied": context_filter_applied,
            "pre_context_candidate_count": pre_context_candidate_count,
            "post_context_candidate_count": post_context_candidate_count,
            "success": bool(items),
            "partial": bool(items) and (not coverage_complete or bool(domain_warnings or domain_errors)),
            "coverage_complete": (
                coverage_complete
                and bool(themes)
                and successful_theme_count == len(themes)
            ),
            "candidate_count": len(items),
            "items": items,
            "matched_boards": matched_boards,
            "rejected_boards": rejected_boards,
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
                "boards": [],
                "sources": [],
            })
            for key in ("matched_domains", "lookup_themes", "boards"):
                for value in item.get(key) or []:
                    if value not in merged[key]:
                        merged[key].append(value)
            for source in item.get("sources") or []:
                if isinstance(source, dict) and source not in merged["sources"]:
                    merged["sources"].append(source)

    union_items = sorted(
        union.values(),
        key=lambda item: (-len(item.get("matched_domains") or []), str(item.get("symbol") or "")),
    )
    success = any(result["success"] for result in domain_results)
    return {
        "success": success,
        "partial": success and any(not result["success"] or result["partial"] for result in domain_results),
        "requested_domains": requested,
        "domain_specs": [spec.model_dump() for spec in domain_specs],
        "context_theme": normalized_context or None,
        "inferred_context_themes": inferred_context_themes,
        "local_universe_count": local_universe_count,
        "domain_results": domain_results,
        "items": union_items,
        "candidate_count": len(union_items),
        "returned_count": len(union_items),
        "source_scope": (
            "structured_concept_constituents_intersected_with_context_theme_and_local_stock_meta"
            if use_context_filter
            else "structured_concept_constituents_intersected_with_local_stock_meta"
        ),
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
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": 64},
                    "board_queries": {
                        "type": "array", "items": {"type": "string"}, "maxItems": 4,
                    },
                    "mapping_type": {
                        "type": "string",
                        "enum": ["exact_board", "proxy_board", "unresolved"],
                    },
                    "rationale": {"type": "string", "maxLength": 240},
                    "unresolved_parts": {
                        "type": "array", "items": {"type": "string"}, "maxItems": 8,
                    },
                },
                "required": ["label", "board_queries", "mapping_type", "rationale", "unresolved_parts"],
            },
            "minItems": 1,
            "maxItems": 12,
            "description": "已由任务规划器对照当前完整板块目录解析的产业领域对象",
        },
        "context_theme": {"type": "string", "description": "上位产业主题，可留空"},
        "limit_per_domain": {"type": "integer", "minimum": 20, "maximum": 500, "default": 300},
    }, required=("domains",)),
    executor=get_domain_stock_candidates,
    category="research",
)


__all__ = ["TOOL", "get_domain_stock_candidates"]
