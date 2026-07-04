# -*- coding: utf-8 -*-
"""A-share stock listing and K-line status endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Query
from sqlalchemy import or_

from api.v1.endpoints.stocks import router
from src.storage import DatabaseManager, StockMeta, StockDaily
from sqlalchemy import func, select

logger = logging.getLogger(__name__)


@router.get(
    "",
    summary="List all A-share stocks",
)
def list_stocks(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=10, le=500, description="每页数量"),
    search: Optional[str] = Query(default=None, description="搜索代码或名称"),
    market: Optional[str] = Query(default=None, description="市场筛选 (sh/sz/cyb/kcb/bj)"),
    count: bool = Query(default=True, description="是否返回精确总数；加载更多可设为 false"),
):
    """Paginate and search the synced A-share stock list."""
    db = DatabaseManager.get_instance()

    with db.get_session() as session:
        query = session.query(StockMeta).filter(StockMeta.status == "active")

        if market:
            query = query.filter(StockMeta.market == market)

        if search:
            search_term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    StockMeta.code.like(search_term),
                    StockMeta.name.like(search_term),
                )
            )

        if count:
            total = query.count()
            items = (
                query.order_by(StockMeta.code)
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            has_more = page < max(1, (total + page_size - 1) // page_size)
            total_pages = max(1, (total + page_size - 1) // page_size)
        else:
            rows = (
                query.order_by(StockMeta.code)
                .offset((page - 1) * page_size)
                .limit(page_size + 1)
                .all()
            )
            items = rows[:page_size]
            has_more = len(rows) > page_size
            total = 0
            total_pages = page + 1 if has_more else page

        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_more": has_more,
        }


@router.get(
    "/count",
    summary="Get total stock count",
)
def get_stock_count():
    """Return total count of active A-share stocks in database."""
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        total = session.query(StockMeta).filter(StockMeta.status == "active").count()
    return {"total": total}


@router.get(
    "/kline-status",
    summary="验证 K 线数据完整性",
)
def get_kline_status():
    """返回股票总数、已有 K 线数据的股票数、缺失数、最近交易日。"""
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        total_stocks = session.query(func.count(StockMeta.id)).filter(StockMeta.status == "active").scalar()
        active_codes = select(StockMeta.code).where(StockMeta.status == "active")
        stocks_with_kline = (
            session.query(func.count(func.distinct(StockDaily.code)))
            .filter(StockDaily.code.in_(active_codes))
            .scalar()
        )
        latest_trading_day = session.execute(select(func.max(StockDaily.date))).scalar()
        codes_with_kline = select(StockDaily.code).distinct()
        missing_codes = [
            row.code
            for row in session.query(StockMeta.code)
            .filter(StockMeta.status == "active", StockMeta.code.notin_(codes_with_kline))
            .order_by(StockMeta.code)
            .all()
        ]

    return {
        "total_stocks": total_stocks or 0,
        "stocks_with_kline": stocks_with_kline or 0,
        "missing": (total_stocks or 0) - (stocks_with_kline or 0),
        "missing_codes": missing_codes,
        "latest_trading_day": str(latest_trading_day) if latest_trading_day else None,
    }


@router.post(
    "/kline/batch",
    summary="Get batch kline data for specified stock codes",
)
def get_kline_batch(body: dict):
    """Return daily OHLCV data from local DB for specified stock codes.

    Request body: { "codes": ["000001", "000002", ...], "count": 250 }
    Returns: { "results": { "000001": [{date, open, high, low, close, ...}, ...], ... } }
    Only returns data for codes that exist in DB; missing codes get empty arrays.
    """
    import time
    from sqlalchemy import select

    codes: list[str] = body.get("codes", [])
    count: int = body.get("count", 250)

    if not codes:
        return {"results": {}}

    codes = codes[:100]

    t0 = time.time()
    db = DatabaseManager.get_instance()

    with db.get_session() as session:
        rows = session.execute(
            select(
                StockDaily.code,
                StockDaily.date,
                StockDaily.open,
                StockDaily.high,
                StockDaily.low,
                StockDaily.close,
                StockDaily.volume,
                StockDaily.amount,
                StockDaily.pct_chg,
                StockDaily.ma5,
                StockDaily.ma10,
                StockDaily.ma20,
                StockDaily.volume_ratio,
                StockDaily.data_source,
            )
            .where(StockDaily.code.in_(codes))
            .order_by(StockDaily.code, StockDaily.date)
        ).all()

    klines: dict[str, list] = {c: [] for c in codes}
    for row in rows:
        date_str = row[1].isoformat() if hasattr(row[1], 'isoformat') else str(row[1])[:10]
        klines[row[0]].append({
            "date": date_str,
            "open": row[2],
            "high": row[3],
            "low": row[4],
            "close": row[5],
            "volume": row[6],
            "amount": row[7],
            "pct_chg": row[8],
            "ma5": row[9],
            "ma10": row[10],
            "ma20": row[11],
            "volume_ratio": row[12],
            "data_source": row[13],
        })

    results = {}
    for code in codes:
        data = klines[code]
        if len(data) > count:
            data = data[-count:]
        results[code] = data

    elapsed = int((time.time() - t0) * 1000)
    logger.info(f"[kline/batch] {len(codes)} codes, {elapsed}ms")

    return {"results": results}
