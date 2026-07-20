"""Filter one user-owned watchlist by concept-board membership."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.tools.base import ToolSpec, object_schema
from src.tools.get_theme_stock_candidates import get_theme_stock_candidates
from src.tools.manage_watchlist_groups import manage_watchlist_groups


_THEME_ALIASES = {
    "ai": "人工智能",
    "人工智能": "人工智能",
    "机器人": "机器人",
    "人形机器人": "人形机器人",
    "具身智能": "具身智能",
}


def _themes(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("theme 不能为空")
    lowered = text.lower()
    found: list[str] = []
    if "人工智能" in text:
        found.append("人工智能")
    # A precise robotics topic must not silently widen to the generic robot board.
    if "人形机器人" in text:
        found.append("人形机器人")
    elif "具身智能" in text:
        found.append("具身智能")
    elif "机器人" in text:
        found.append("机器人")
    if re.search(r"(?<![a-z])ai(?![a-z])", lowered) and "人工智能" not in found:
        found.insert(0, "人工智能")
    if not found:
        pieces = [part.strip() for part in re.split(r"[、,，/与和及+]", text) if part.strip()]
        found = [_THEME_ALIASES.get(part.lower(), part) for part in pieces]
    return list(dict.fromkeys(found))[:5]


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


def filter_watchlist_by_theme(theme: str, group: str = "") -> dict[str, Any]:
    """Return only the intersection of a watchlist and requested themes."""
    requested_themes = _themes(theme)
    group_payload = manage_watchlist_groups("list")
    selected_group = _select_group(list(group_payload.get("groups") or []), group)
    raw_codes = [str(code or "").strip() for code in selected_group.get("codes") or []]
    valid_codes = list(dict.fromkeys(code for code in raw_codes if re.fullmatch(r"\d{6}", code)))
    invalid_entries = [code for code in raw_codes if code and not re.fullmatch(r"\d{6}", code)]

    matches_by_code: dict[str, dict[str, Any]] = {}
    source_coverage: list[dict[str, Any]] = []
    warnings = list(group_payload.get("warnings") or [])
    errors: list[str] = []
    partial = bool(group_payload.get("partial"))

    for requested_theme in requested_themes:
        try:
            result = get_theme_stock_candidates(requested_theme, limit=1000)
        except Exception as exc:
            partial = True
            errors.append(f"{requested_theme}: {type(exc).__name__}: {exc}")
            continue
        partial = partial or bool(result.get("partial"))
        errors.extend(str(error) for error in result.get("errors") or [])
        warnings.extend(str(warning) for warning in result.get("warnings") or [])
        source_coverage.append({
            "theme": requested_theme,
            "candidate_count": int(result.get("candidate_count") or 0),
            "coverage_complete": bool(result.get("coverage_complete")),
            "data_time": result.get("data_time"),
            "sources": [
                {
                    "name": board.get("source"),
                    "board": board.get("name"),
                    "coverage": board.get("coverage"),
                    "url": board.get("url"),
                }
                for board in result.get("matched_boards") or []
                if isinstance(board, dict)
            ],
        })
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            if symbol not in valid_codes:
                continue
            match = matches_by_code.setdefault(symbol, {
                "symbol": symbol,
                "name": str(item.get("name") or ""),
                "matched_themes": [],
                "boards": [],
                "evidence_level": "L1",
                "evidence_boundary": "主题板块成员关系，不等同已形成相关订单或收入",
                "sources": [],
            })
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
    parameters=object_schema({
        "theme": {"type": "string", "description": "一个或多个主题，例如 AI和机器人"},
        "group": {"type": "string", "description": "可选的自选分组名称或ID；为空时使用默认自选股"},
    }, required=("theme",)),
    executor=filter_watchlist_by_theme,
    category="research",
)


__all__ = ["TOOL", "filter_watchlist_by_theme"]
