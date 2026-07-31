"""Filter one user-owned watchlist by resolved concept-board membership.

The tool receives typed semantic-resource bindings.  It does not split,
translate or infer themes from natural-language text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.agent.result_contracts import DomainBoardQuerySpec
from src.tools.base import ToolSpec, object_schema
from src.tools.get_domain_stock_candidates import get_domain_stock_candidates
from src.tools.manage_watchlist_groups import manage_watchlist_groups


def _select_group(groups: list[dict[str, Any]], group: str) -> dict[str, Any]:
    identifier = str(group or "").strip()
    if not identifier:
        return next(
            (item for item in groups if item.get("is_default")),
            groups[0] if groups else {},
        )
    for item in groups:
        if str(item.get("id")) == identifier or str(item.get("name") or "") == identifier:
            return item
    raise ValueError(f"未找到自选分组: {identifier}")


def filter_watchlist_by_theme(domains: list[Any], group: str = "") -> dict[str, Any]:
    """Return the intersection of a watchlist and validated domain bindings."""
    domain_specs = [DomainBoardQuerySpec.model_validate(item) for item in domains or []]
    if not domain_specs:
        raise ValueError("domains 至少需要一个已绑定领域")
    requested_themes = [item.label for item in domain_specs]
    group_payload = manage_watchlist_groups("list")
    selected_group = _select_group(list(group_payload.get("groups") or []), group)
    raw_codes = [str(code or "").strip() for code in selected_group.get("codes") or []]
    valid_codes = list(dict.fromkeys(code for code in raw_codes if len(code) == 6 and code.isdigit()))
    invalid_entries = [code for code in raw_codes if code and not (len(code) == 6 and code.isdigit())]

    matches_by_code: dict[str, dict[str, Any]] = {}
    source_coverage: list[dict[str, Any]] = []
    warnings = list(group_payload.get("warnings") or [])
    errors: list[str] = []
    partial = bool(group_payload.get("partial"))

    try:
        result = get_domain_stock_candidates(
            [item.model_dump() for item in domain_specs],
        )
    except Exception as exc:
        result = {
            "success": False,
            "partial": False,
            "domain_results": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
            "warnings": [],
        }
    partial = partial or bool(result.get("partial"))
    errors.extend(str(error) for error in result.get("errors") or [])
    warnings.extend(str(warning) for warning in result.get("warnings") or [])
    for domain_result in result.get("domain_results") or []:
        if not isinstance(domain_result, dict):
            continue
        requested_theme = str(domain_result.get("domain") or "")
        source_coverage.append(
            {
                "theme": requested_theme,
                "candidate_count": int(domain_result.get("candidate_count") or 0),
                "coverage_complete": bool(domain_result.get("coverage_complete")),
                "mapping_type": domain_result.get("mapping_type"),
                "mapping_rationale": domain_result.get("mapping_rationale"),
                "sources": [
                    {
                        "name": board.get("source"),
                        "board": board.get("name"),
                        "coverage": board.get("coverage"),
                        "url": board.get("url"),
                    }
                    for board in domain_result.get("matched_boards") or []
                    if isinstance(board, dict)
                ],
            }
        )
        for item in domain_result.get("items") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            if symbol not in valid_codes:
                continue
            match = matches_by_code.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": str(item.get("name") or ""),
                    "matched_themes": [],
                    "boards": [],
                    "evidence_level": "L1",
                    "evidence_boundary": "主题板块成员关系，不等同已形成相关订单或收入",
                    "sources": [],
                },
            )
            if requested_theme not in match["matched_themes"]:
                match["matched_themes"].append(requested_theme)
            for board in item.get("boards") or []:
                if board not in match["boards"]:
                    match["boards"].append(board)
            source = item.get("source")
            if isinstance(source, dict) and source not in match["sources"]:
                match["sources"].append(source)

    items = [matches_by_code[code] for code in valid_codes if code in matches_by_code]
    if invalid_entries:
        warnings.append("已忽略无法识别为六位证券代码的自选条目: " + "、".join(invalid_entries[:10]))
    data_time = datetime.now().astimezone().isoformat()
    success = bool(group_payload.get("success", True)) and bool(source_coverage)
    return {
        "success": success,
        "partial": partial if success else False,
        "group": {
            "id": selected_group.get("id"),
            "name": selected_group.get("name") or "我的自选股",
            "count": len(raw_codes),
            "valid_security_count": len(valid_codes),
        },
        "requested_themes": requested_themes,
        "domain_specs": [item.model_dump() for item in domain_specs],
        "matched_count": len(items),
        "items": items,
        "source_coverage": source_coverage,
        "invalid_entries": invalid_entries,
        "decision_boundary": (
            "结果严格限定为该自选集合与公开主题板块的交集；L1 只证明板块成员关系。"
            "如需判断真实业务关联，应由用户继续指定公司后再查公告、财报或主营证据。"
        ),
        "data_time": data_time,
        "is_stale": False,
        "freshness_unknown": False,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
    }


TOOL = ToolSpec(
    name="filter_watchlist_by_theme",
    description=(
        "在用户自己的默认自选股或指定自选分组内，按一个或多个行业/概念主题筛选成员。"
        "工具内部完成集合交集，只返回自选中的匹配项，不返回全市场候选，也不查询无关行情。"
        "适用于‘我的自选里哪些与AI/机器人有关’；概念成员仅标为L1，不代表订单或收入。"
    ),
    parameters=object_schema(
        {
            "domains": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string"},
                        "board_queries": {"type": "array", "items": {"type": "string"}},
                        "mapping_type": {
                            "type": "string",
                            "enum": ["catalog_binding", "unresolved"],
                        },
                        "rationale": {"type": "string"},
                        "unresolved_parts": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "label",
                        "board_queries",
                        "mapping_type",
                        "rationale",
                        "unresolved_parts",
                    ],
                },
                "description": "由语义资源绑定器对照实时板块目录生成的领域对象",
            },
            "group": {"type": "string", "description": "可选的自选分组名称或ID；为空时使用默认自选股"},
        },
        required=("domains",),
    ),
    executor=filter_watchlist_by_theme,
    category="research",
)


__all__ = ["TOOL", "filter_watchlist_by_theme"]
