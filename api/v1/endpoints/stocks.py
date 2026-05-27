# -*- coding: utf-8 -*-
"""A-share stock list sync and query endpoints."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_system_config_service
from api.v1.schemas.common import ErrorResponse
from data_provider.akshare_fetcher import AkshareFetcher
from src.services.system_config_service import SystemConfigService
from src.storage import DatabaseManager, StockMeta

logger = logging.getLogger(__name__)

router = APIRouter()

# Global sync state (in-process, restart resets it)
_sync_lock = threading.Lock()
_sync_state: dict = {
    "status": "idle",  # idle | running | success | failed
    "progress": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
    "message": "",
    "error": None,
}


def _classify_a_stock_market(code: str) -> str:
    code = code.strip()
    if code.startswith("688"):
        return "kcb"
    if code.startswith(("300", "301")):
        return "cyb"
    if code.startswith(("8", "9")) and len(code) == 6:
        return "bj"
    if code.startswith(("600", "601", "603", "605")):
        return "sh"
    if code.startswith(("000", "001", "002", "003")):
        return "sz"
    return "other"


def _run_sync():
    """Execute full A-share stock sync in a background thread."""
    global _sync_state
    try:
        _sync_state["status"] = "running"
        _sync_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _sync_state["progress"] = 0
        _sync_state["total"] = 0
        _sync_state["error"] = None

        fetcher = AkshareFetcher()
        stocks = fetcher.get_all_a_stocks()

        if not stocks:
            _sync_state["status"] = "failed"
            _sync_state["message"] = "未能从数据源获取股票列表"
            _sync_state["finished_at"] = datetime.now(timezone.utc).isoformat()
            return

        _sync_state["total"] = len(stocks)
        db = DatabaseManager.get_instance()
        now = datetime.now()

        added, updated = 0, 0
        with db.get_session() as session:
            # Batch lookup existing codes
            all_codes = [s["code"] for s in stocks]
            existing = {
                row.code: row
                for row in session.query(StockMeta).filter(StockMeta.code.in_(all_codes)).all()
            }

            for i, item in enumerate(stocks):
                code = item["code"]
                meta = existing.get(code)

                if meta:
                    # Update existing
                    meta.name = item["name"]
                    meta.market = item["market"]
                    meta.pe_ttm = item.get("pe_ttm")
                    meta.pb = item.get("pb")
                    meta.total_market_cap = item.get("total_market_cap")
                    meta.circulating_market_cap = item.get("circulating_market_cap")
                    meta.last_sync_at = now
                    updated += 1
                else:
                    # Insert new
                    meta = StockMeta(
                        code=code,
                        name=item["name"],
                        market=item["market"],
                        status="active",
                        pe_ttm=item.get("pe_ttm"),
                        pb=item.get("pb"),
                        total_market_cap=item.get("total_market_cap"),
                        circulating_market_cap=item.get("circulating_market_cap"),
                        last_sync_at=now,
                    )
                    session.add(meta)
                    added += 1

                # Update progress every 100 records
                if (i + 1) % 100 == 0:
                    _sync_state["progress"] = i + 1

            # Mark delisted stocks (not in current list)
            current_codes = set(all_codes)
            delisted = (
                session.query(StockMeta)
                .filter(StockMeta.code.notin_(current_codes), StockMeta.status == "active")
                .update({"status": "delisted", "updated_at": now}, synchronize_session=False)
            )

            session.commit()

        _sync_state["status"] = "success"
        _sync_state["progress"] = len(stocks)
        _sync_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _sync_state["message"] = f"同步完成: 新增 {added}, 更新 {updated}, 退市标记 {delisted}"

        logger.info(
            "[StocksSync] 同步完成: 总数=%d 新增=%d 更新=%d 退市=%d",
            len(stocks), added, updated, delisted,
        )

    except Exception as e:
        _sync_state["status"] = "failed"
        _sync_state["error"] = str(e)
        _sync_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        logger.error("[StocksSync] 同步失败: %s", e, exc_info=True)


@router.post(
    "/sync",
    summary="Sync all A-share stocks",
    responses={409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def sync_stocks(
    service: SystemConfigService = Depends(get_system_config_service),
):
    """Trigger a full sync of all A-share stock metadata from East Money via akshare."""
    global _sync_state

    if _sync_state["status"] == "running":
        raise HTTPException(
            status_code=409,
            detail={"error": "sync_in_progress", "message": "同步正在进行中，请稍后再试"},
        )

    # Fire-and-forget in background thread
    with _sync_lock:
        if _sync_state["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail={"error": "sync_in_progress", "message": "同步正在进行中，请稍后再试"},
            )
        thread = threading.Thread(target=_run_sync, daemon=True)
        thread.start()

    return {
        "success": True,
        "message": "同步已启动",
        "status": "running",
    }


@router.get(
    "/sync/status",
    summary="Get sync status",
)
def get_sync_status():
    """Return current sync progress.

    When the in-memory state is idle/empty (e.g. after a restart), fall back
    to the database count so the frontend doesn't show "not synced yet".
    """
    if _sync_state["total"] == 0:
        try:
            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                total = session.query(StockMeta).filter(StockMeta.status == "active").count()
            if total > 0:
                return {
                    **{k: v for k, v in _sync_state.items()},
                    "status": "success" if _sync_state["status"] == "idle" else _sync_state["status"],
                    "total": total,
                    "message": _sync_state["message"] or "数据已存在（来自数据库）",
                }
        except Exception:
            pass
    return _sync_state


@router.get(
    "",
    summary="List all A-share stocks",
)
def list_stocks(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=10, le=500, description="每页数量"),
    search: Optional[str] = Query(default=None, description="搜索代码或名称"),
    market: Optional[str] = Query(default=None, description="市场筛选 (sh/sz/cyb/kcb/bj)"),
):
    """Paginate and search the synced A-share stock list."""
    db = DatabaseManager.get_instance()

    with db.get_session() as session:
        query = session.query(StockMeta).filter(StockMeta.status == "active")

        if market:
            query = query.filter(StockMeta.market == market)

        if search:
            search_term = f"%{search.strip()}%"
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    StockMeta.code.like(search_term),
                    StockMeta.name.like(search_term),
                )
            )

        total = query.count()
        items = (
            query.order_by(StockMeta.code)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
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
