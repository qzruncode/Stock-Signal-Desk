"""Search the automatically maintained local A-share security universe."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_

from src.services.data_maintenance import ensure_stock_universe
from src.storage import DatabaseManager, StockMeta
from src.tools.base import ToolSpec, object_schema


_EXCHANGE_MARKETS: dict[str, tuple[str, ...]] = {
    "all": ("sh", "sz", "cyb", "kcb", "bj"),
    "sh": ("sh", "kcb"),
    "sz": ("sz", "cyb"),
    "bj": ("bj",),
}
_BOARD_MARKETS: dict[str, tuple[str, ...]] = {
    "all": _EXCHANGE_MARKETS["all"],
    "main": ("sh", "sz"),
    "cyb": ("cyb",),
    "kcb": ("kcb",),
    "bj": ("bj",),
}
_LEGACY_MARKETS = frozenset(_EXCHANGE_MARKETS["all"])
_SECURITY_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def _markets_for_scope(
    *,
    market: str = "all",
    exchange: str = "all",
    board: str = "all",
) -> tuple[str, ...]:
    """Resolve declarative A-share exchange and board constraints once."""
    normalized_market = str(market or "all").strip().lower()
    normalized_exchange = str(exchange or "all").strip().lower()
    normalized_board = str(board or "all").strip().lower()
    if normalized_market not in {"all", *_LEGACY_MARKETS}:
        raise ValueError(f"unsupported A-share board code: {normalized_market}")
    if normalized_exchange not in _EXCHANGE_MARKETS:
        raise ValueError(f"unsupported A-share exchange: {normalized_exchange}")
    if normalized_board not in _BOARD_MARKETS:
        raise ValueError(f"unsupported A-share board: {normalized_board}")

    accepted = set(_EXCHANGE_MARKETS[normalized_exchange])
    accepted.intersection_update(_BOARD_MARKETS[normalized_board])
    if normalized_market != "all":
        accepted.intersection_update({normalized_market})
    return tuple(market_code for market_code in _EXCHANGE_MARKETS["all"] if market_code in accepted)


def _security_codes_in_query(query: str) -> tuple[str, ...]:
    """Extract explicit A-share identifiers from a free-form lookup query."""
    return tuple(dict.fromkeys(_SECURITY_CODE_PATTERN.findall(str(query or ""))))


def search_stocks(
    query: str = "",
    market: str = "all",
    exchange: str = "all",
    board: str = "all",
    sector: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    maintenance = ensure_stock_universe(trigger="agent_search_stocks")
    bounded_limit = max(1, min(int(limit or 20), 200))
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        statement = session.query(StockMeta).filter(StockMeta.status == "active")
        normalized_query = str(query or "").strip()
        normalized_sector = str(sector or "").strip()
        if normalized_query:
            code_hints = _security_codes_in_query(normalized_query)
            if code_hints:
                # A six-digit A-share code is an explicit identifier. Treat it
                # as authoritative even when the user or planner also includes
                # the company name, punctuation, or explanatory words.
                statement = statement.filter(StockMeta.code.in_(code_hints))
            else:
                pattern = f"%{normalized_query}%"
                statement = statement.filter(or_(StockMeta.code.like(pattern), StockMeta.name.like(pattern)))
        market_codes = _markets_for_scope(
            market=market,
            exchange=exchange,
            board=board,
        )
        if not market_codes:
            items = []
        else:
            statement = statement.filter(StockMeta.market.in_(market_codes))
            if normalized_sector:
                statement = statement.filter(StockMeta.sector.like(f"%{normalized_sector}%"))
            items = statement.order_by(StockMeta.code).limit(bounded_limit + 1).all()
    has_more = len(items) > bounded_limit
    returned = [item.to_dict() for item in items[:bounded_limit]]
    warning = maintenance.get("warning")
    warnings = [warning] if warning else []
    if has_more:
        warnings.append("结果超过当前返回上限，请缩小搜索条件")
    data_time = maintenance.get("data_time")
    return {
        "success": True,
        "partial": bool(has_more or warning),
        "query": normalized_query,
        "code_hints": list(_security_codes_in_query(normalized_query)),
        "market": str(market or "all").strip().lower(),
        "exchange": str(exchange or "all").strip().lower(),
        "board": str(board or "all").strip().lower(),
        "sector": normalized_sector or None,
        "items": returned,
        "returned_count": len(returned),
        "has_more": has_more,
        "maintenance": maintenance,
        "data_time": data_time,
        "is_stale": bool(maintenance.get("is_stale")) if data_time else None,
        "freshness_unknown": data_time is None,
        "errors": [],
        "warnings": warnings,
    }


TOOL = ToolSpec(
    name="search_stocks",
    description=(
        "搜索自动维护的A股证券库，可按代码、名称、市场和行业查询。"
        "当股票基础库为空或过期时会自动刷新，不需要用户手动同步。"
    ),
    parameters=object_schema(
        {
            "query": {"type": "string", "description": "股票代码、名称或两者组合；显式六位代码优先用于身份定位。"},
            "market": {
                "type": "string",
                "enum": ["all", "sh", "sz", "cyb", "kcb", "bj"],
                "default": "all",
                "description": "兼容旧调用的精确板块代码；新调用请使用 exchange 和 board。",
            },
            "exchange": {
                "type": "string",
                "enum": ["all", "sh", "sz", "bj"],
                "default": "all",
                "description": "交易所范围；深圳包含主板与创业板，上海包含主板与科创板。",
            },
            "board": {
                "type": "string",
                "enum": ["all", "main", "cyb", "kcb", "bj"],
                "default": "all",
                "description": "A股板块范围；仅在用户明确指定板块时使用。",
            },
            "sector": {"type": "string", "description": "行业关键词，可留空"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        }
    ),
    executor=search_stocks,
    category="data",
)


__all__ = ["TOOL", "search_stocks"]
