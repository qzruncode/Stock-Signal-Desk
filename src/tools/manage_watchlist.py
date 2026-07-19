"""Agent-facing watchlist management with explicit mutation semantics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.services.watchlist_service import manage_watchlist as _manage
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


def manage_watchlist(action: str, symbols: str = "") -> dict[str, Any]:
    if action == "list":
        codes: list[str] = []
    else:
        resolved, unresolved = resolve_securities_csv(symbols)
        if unresolved:
            raise ValueError("无法确认股票代码: " + ", ".join(unresolved))
        codes = [item["symbol"] for item in resolved]
        if action in {"add", "remove"} and not codes:
            raise ValueError("添加或删除自选股时必须提供 symbols")
    result = _manage(action, codes)
    now = datetime.now().astimezone().isoformat()
    return {
        "success": True,
        "partial": False,
        "action": action,
        **result,
        "data_time": now,
        "is_stale": False,
        "freshness_unknown": False,
        "errors": [],
        "warnings": [],
    }


TOOL = ToolSpec(
    name="manage_watchlist",
    description=(
        "查看、添加或删除用户自选股。add/remove 只允许在用户明确要求修改自选股时调用；"
        "不能因为分析或推荐结果而擅自修改。"
    ),
    parameters=object_schema({
        "action": {"type": "string", "enum": ["list", "add", "remove"]},
        "symbols": {"type": "string", "description": "逗号分隔的股票代码或精确名称；list 时留空"},
    }, required=("action",)),
    executor=manage_watchlist,
    category="action",
)


__all__ = ["TOOL", "manage_watchlist"]
