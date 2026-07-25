# -*- coding: utf-8 -*-
"""Validated multi-domain A-share candidate discovery.

This is the runtime-owned bridge between an industry conclusion (for example
``行星滚柱丝杠、减速器、无框力矩电机``) and the synchronized A-share
universe.  It never discovers company identities from generic web results.
Every returned code comes from structured concept-board constituents and is
intersected with local ``stock_meta`` by ``get_theme_stock_candidates``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.agent.result_contracts import DomainBoardQuerySpec
from src.tools.base import ToolSpec, object_schema
from src.tools.get_theme_stock_candidates import (
    _load_local_universe,
    get_theme_stock_candidates,
)


def _compact(value: Any) -> str:
    return "".join(str(value or "").split()).casefold()


def _normalize_domain_specs(domains: list[Any]) -> list[DomainBoardQuerySpec]:
    """Validate planner-owned resolution objects."""
    normalized: list[DomainBoardQuerySpec] = []
    seen: set[str] = set()
    for raw in domains or []:
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
) -> dict[str, Any]:
    domain_specs = _normalize_domain_specs(domains)
    if not domain_specs:
        raise ValueError("domains 至少需要一个产业领域")
    requested = [spec.label for spec in domain_specs]
    # DatabaseManager initialization is process-global and is not safe to race
    # from several first-use worker threads.  Maintain and read the synchronized
    # security master once, then share this immutable identity map across all
    # domain lookups.  This also avoids repeating full-universe database reads.
    from src.services.data_maintenance import ensure_stock_universe

    maintenance = ensure_stock_universe(trigger="agent_domain_candidates")
    local_universe = _load_local_universe()
    lookup_themes: list[str] = []
    for spec in domain_specs:
        for theme in spec.board_queries:
            if theme not in lookup_themes:
                lookup_themes.append(theme)

    fetched: dict[str, dict[str, Any]] = {}
    workers = min(4, len(lookup_themes))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                get_theme_stock_candidates,
                theme,
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
                if len(symbol) != 6 or not symbol.isdigit():
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

        items = list(by_symbol.values())
        domain_result = {
            "domain": domain,
            "lookup_themes": themes,
            "mapping_type": domain_spec.mapping_type,
            "mapping_rationale": domain_spec.rationale,
            "unresolved_parts": list(domain_spec.unresolved_parts),
            "mapping_basis": {
                "catalog_binding": "live_catalog_binding",
                "unresolved": "catalog_unresolved",
            }[domain_spec.mapping_type],
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
                        "enum": ["catalog_binding", "unresolved"],
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
    }, required=("domains",)),
    executor=get_domain_stock_candidates,
    category="research",
)


__all__ = ["TOOL", "get_domain_stock_candidates"]
