# -*- coding: utf-8 -*-
"""Service helper for structured multi-domain A-share candidate discovery.

This is used by the standalone buy-criteria service and is not registered as
an Agent tool because it fans out across multiple board lookups. It bridges structured industry-board bindings
and the synchronized A-share universe.  It never discovers company identities
from generic web results.
Every returned code comes from structured concept-board constituents and is
intersected with local ``stock_meta`` by ``get_theme_stock_candidates``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.services.buy_criteria.contracts import DomainBoardQuerySpec
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
        if len(normalized) >= 512:
            break
    return normalized


def _live_board_catalog() -> dict[str, Any]:
    try:
        from src.services.domain_board_catalog import get_domain_board_catalog

        catalog = get_domain_board_catalog()
        identities = [
            (
                str(item.get("sector_code") or "").strip(),
                str(item.get("name") or "").strip(),
            )
            for item in catalog.get("boards") or []
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
            and str(item.get("sector_code") or "").strip()
        ]
        return {
            "catalog_snapshot_id": str(catalog.get("catalog_snapshot_id") or "").strip(),
            "by_name": {board_name: board_id for board_id, board_name in identities},
            "by_id": {board_id: board_name for board_id, board_name in identities},
        }
    except Exception:
        return {
            "catalog_snapshot_id": "",
            "by_name": {},
            "by_id": {},
        }


def _live_board_codes() -> dict[str, str]:
    """Compatibility view for persisted pre-V2 domain bindings."""
    return dict(_live_board_catalog()["by_name"])


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
    has_v2_identity = any(spec.board_id for spec in domain_specs)
    live_catalog = _live_board_catalog() if has_v2_identity else None
    live_snapshot_id = str(live_catalog.get("catalog_snapshot_id") or "") if live_catalog is not None else ""
    board_codes = dict(live_catalog.get("by_name") or {}) if live_catalog is not None else _live_board_codes()
    board_names_by_id = dict(live_catalog.get("by_id") or {}) if live_catalog is not None else {}
    resolved_themes: dict[int, list[str]] = {}
    identity_errors: dict[int, list[str]] = {}
    lookup_board_codes: dict[str, str | None] = {}
    for index, spec in enumerate(domain_specs):
        errors: list[str] = []
        themes: list[str] = []
        if spec.board_id:
            if spec.catalog_snapshot_id and spec.catalog_snapshot_id != live_snapshot_id:
                errors.append("板块目录快照已变化，拒绝使用历史 board_id 绑定。")
            live_name = board_names_by_id.get(spec.board_id)
            if not live_name:
                errors.append(f"实时目录不存在板块 ID {spec.board_id}。")
            elif live_name != spec.board_name:
                errors.append(
                    f"板块 ID {spec.board_id} 当前名称为“{live_name}”，" f"与绑定名称“{spec.board_name}”不一致。"
                )
            elif spec.board_queries != [spec.board_name]:
                errors.append("强类型板块绑定必须只查询其 board_name。")
            elif not errors:
                themes.append(spec.board_name)
                lookup_board_codes[spec.board_name] = spec.board_id
        else:
            for theme in spec.board_queries:
                if theme not in themes:
                    themes.append(theme)
                lookup_board_codes.setdefault(theme, board_codes.get(theme))
        resolved_themes[index] = themes
        identity_errors[index] = errors

    lookup_themes = list(lookup_board_codes)
    fetched: dict[str, dict[str, Any]] = {}
    workers = min(4, len(lookup_themes))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                get_theme_stock_candidates,
                theme,
                board_code=lookup_board_codes.get(theme),
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
    for domain_index, domain_spec in enumerate(domain_specs):
        domain = domain_spec.label
        themes = list(resolved_themes[domain_index])
        by_symbol: dict[str, dict[str, Any]] = {}
        matched_boards: list[dict[str, Any]] = []
        rejected_boards: list[dict[str, Any]] = []
        domain_warnings: list[str] = []
        domain_errors: list[str] = list(identity_errors[domain_index])
        coverage_complete = (
            domain_spec.mapping_type != "unresolved" and not domain_spec.unresolved_parts and not domain_errors
        )
        successful_theme_count = 0

        if domain_spec.unresolved_parts:
            domain_warnings.append("未解析子领域：" + "、".join(domain_spec.unresolved_parts))
        if not themes:
            coverage_complete = False
            domain_errors.append("当前完整板块目录中没有可执行的结构化召回路径。")

        for theme in themes:
            result = fetched[theme]
            local_universe_count = max(
                local_universe_count,
                int(result.get("local_universe_count") or 0),
            )
            exact_theme_complete = bool(result.get("success") and result.get("coverage_complete"))
            if exact_theme_complete:
                successful_theme_count += 1
            coverage_complete = coverage_complete and exact_theme_complete
            domain_warnings.extend(str(item) for item in result.get("warnings") or [] if item)
            domain_errors.extend(str(item) for item in result.get("errors") or [] if item)
            if not exact_theme_complete:
                rejected_boards.extend(board for board in result.get("matched_boards") or [] if isinstance(board, dict))
                domain_errors.append(f"查询板块“{theme}”未取得完整精确板块成分，" "已拒绝近似或跨行业板块候选。")
                continue
            matched_boards.extend(board for board in result.get("matched_boards") or [] if isinstance(board, dict))
            for item in result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "")
                if len(symbol) != 6 or not symbol.isdigit():
                    continue
                merged = by_symbol.setdefault(
                    symbol,
                    {
                        **item,
                        "matched_domains": [domain],
                        "lookup_themes": [],
                        "boards": [],
                        "sources": [],
                        "evidence_level": "L1",
                        "company_evidence_required": True,
                    },
                )
                if theme not in merged["lookup_themes"]:
                    merged["lookup_themes"].append(theme)
                for board in item.get("boards") or []:
                    if board not in merged["boards"]:
                        merged["boards"].append(board)
                for source in item.get("sources") or []:
                    if isinstance(source, dict) and source not in merged["sources"]:
                        merged["sources"].append(source)

        items = sorted(by_symbol.values(), key=lambda item: str(item.get("symbol") or ""))
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
            "membership_is_business_proof": False,
            "success": bool(items),
            "partial": bool(items) and (not coverage_complete or bool(domain_warnings or domain_errors)),
            "coverage_complete": (coverage_complete and bool(themes) and successful_theme_count == len(themes)),
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
            merged = union.setdefault(
                symbol,
                {
                    **item,
                    "matched_domains": [],
                    "lookup_themes": [],
                    "boards": [],
                    "sources": [],
                },
            )
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
            "候选来自实时结构化板块成分与本地完整证券库；"
            "板块成员关系不等同目标子领域主营、订单、客户验证、收入兑现或投资建议。"
        ),
        "warnings": list(dict.fromkeys(all_warnings)),
        "errors": [] if success else list(dict.fromkeys(all_errors)),
    }


__all__ = ["get_domain_stock_candidates"]
