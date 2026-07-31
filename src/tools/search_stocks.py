"""Search the automatically maintained local A-share security universe."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_

from src.services.data_maintenance import ensure_stock_universe
from src.storage import DatabaseManager, StockMeta
from src.tools.base import ToolSpec, object_schema


def search_stocks(
    query: str = "",
    market: str = "all",
    sector: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    maintenance = ensure_stock_universe(trigger="agent_search_stocks")
    bounded_limit = max(1, min(int(limit or 20), 200))
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        statement = session.query(StockMeta).filter(StockMeta.status == "active")
        normalized_query = str(query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            statement = statement.filter(or_(StockMeta.code.like(pattern), StockMeta.name.like(pattern)))
        if market and market != "all":
            statement = statement.filter(StockMeta.market == market)
        normalized_sector = str(sector or "").strip()
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
        "market": market,
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
            "query": {"type": "string", "description": "股票代码或名称；留空表示浏览"},
            "market": {"type": "string", "enum": ["all", "sh", "sz", "cyb", "kcb", "bj"], "default": "all"},
            "sector": {"type": "string", "description": "行业关键词，可留空"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        }
    ),
    executor=search_stocks,
    category="data",
)


__all__ = ["TOOL", "search_stocks"]
