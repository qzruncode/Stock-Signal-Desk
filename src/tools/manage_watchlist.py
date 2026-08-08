"""Agent-facing watchlist management with explicit mutation semantics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.services.watchlist_service import manage_watchlist as _manage
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


def _resolve_codes(symbols: str) -> list[str]:
    resolved, unresolved = resolve_securities_csv(symbols)
    if unresolved:
        raise ValueError("无法确认股票代码: " + ", ".join(unresolved))
    codes = [item["symbol"] for item in resolved]
    if not codes:
        raise ValueError("添加或删除自选股时必须提供 symbols")
    return codes


def _result(action: str, codes: list[str]) -> dict[str, Any]:
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


def list_watchlist() -> dict[str, Any]:
    """Read the default watchlist without exposing a multiplexed action field."""
    return _result("list", [])


def add_watchlist_items(symbols: str) -> dict[str, Any]:
    """Add the requested securities after the graph approval boundary."""
    return _result("add", _resolve_codes(symbols))


def remove_watchlist_items(symbols: str) -> dict[str, Any]:
    """Remove the requested securities after the graph approval boundary."""
    return _result("remove", _resolve_codes(symbols))


def manage_watchlist(action: str, symbols: str = "") -> dict[str, Any]:
    """Legacy multiplexed adapter retained for non-Agent callers only."""
    if action == "list":
        return list_watchlist()
    if action == "add":
        return add_watchlist_items(symbols)
    if action == "remove":
        return remove_watchlist_items(symbols)
    raise ValueError("action 必须是 list、add 或 remove")


TOOLS = (
    ToolSpec(
        name="list_watchlist",
        description="读取用户默认自选股列表；不修改任何持久化数据。",
        parameters=object_schema(),
        executor=list_watchlist,
        category="action",
    ),
    ToolSpec(
        name="add_watchlist_items",
        description="将明确指定的股票加入用户默认自选股；这是一次持久化修改，必须经过用户审批。",
        parameters=object_schema(
            {"symbols": {"type": "string", "description": "逗号分隔的股票代码或精确名称"}},
            required=("symbols",),
        ),
        executor=add_watchlist_items,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="remove_watchlist_items",
        description="将明确指定的股票从用户默认自选股移除；这是一次持久化修改，必须经过用户审批。",
        parameters=object_schema(
            {"symbols": {"type": "string", "description": "逗号分隔的股票代码或精确名称"}},
            required=("symbols",),
        ),
        executor=remove_watchlist_items,
        category="action",
        effect="side_effect",
    ),
)


__all__ = [
    "TOOLS",
    "add_watchlist_items",
    "list_watchlist",
    "manage_watchlist",
    "remove_watchlist_items",
]
