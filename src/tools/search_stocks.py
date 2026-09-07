"""Search the local A-share security master without triggering maintenance."""

from __future__ import annotations

import re
from typing import Any

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
    return tuple(
        market_code
        for market_code in _EXCHANGE_MARKETS["all"]
        if market_code in accepted
    )


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
    from src.services.market_data_client import get_market_data_client

    markets = _markets_for_scope(market=market, exchange=exchange, board=board)
    hints = _security_codes_in_query(query)
    limit = max(1, min(int(limit), 200))
    result = get_market_data_client().securities(
        search="" if hints else query.strip(),
        codes=",".join(hints),
        market=",".join(markets) if markets else "none",
        sector=sector.strip(),
        page_size=limit,
    )
    items = result["items"]
    has_more = result["total"] > len(items)
    return {
        "success": True,
        "partial": has_more,
        "query": query.strip(),
        "code_hints": list(hints),
        "market": market,
        "exchange": exchange,
        "board": board,
        "sector": sector or None,
        "items": items,
        "returned_count": len(items),
        "has_more": has_more,
        "data_source": "market-data-service",
        "source_scope": "security_master_identity_lookup",
        "data_time": None,
        "data_time_applicable": False,
        "data_time_provenance": "unavailable",
        "data_time_note": "证券身份信息由独立服务自动维护，检查时间不冒充上游发布时间。",
        "is_stale": False,
        "freshness_unknown": False,
        "errors": [],
        "warnings": ["结果超过返回上限，请缩小搜索条件"] if has_more else [],
    }


TOOL = ToolSpec(
    name="search_stocks",
    description=(
        "通过独立数据服务搜索A股证券主数据，可按代码、名称、市场和行业查询。"
        "仅用于实体定位；证券信息由数据服务自动维护，业务库不采集或写入市场数据。"
    ),
    parameters=object_schema(
        {
            "query": {
                "type": "string",
                "description": "股票代码、名称或两者组合；显式六位代码优先用于身份定位。",
            },
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
