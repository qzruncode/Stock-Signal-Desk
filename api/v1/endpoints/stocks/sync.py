# -*- coding: utf-8 -*-
"""A-share stock sync endpoints — trigger sync and monitor status."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_system_config_service
from api.v1.endpoints.stocks import router
from api.v1.schemas.common import ErrorResponse
from data_provider.akshare_fetcher import AkshareFetcher
from src.services.system_config_service import SystemConfigService
from src.storage import DatabaseManager, StockMeta, StockDaily
from sqlalchemy import delete, func

logger = logging.getLogger(__name__)

import chinese_calendar

# Global sync state (in-process, restart resets it)
_sync_lock = threading.Lock()
_sync_state: dict = {
    "status": "idle",  # idle | running | syncing_kline | success | failed
    "progress": 0,
    "total": 0,
    "kline_progress": 0,
    "kline_total": 0,
    "started_at": None,
    "finished_at": None,
    "message": "",
    "error": None,
}


def _get_latest_trading_day(reference: date | None = None) -> date:
    """计算最近一个 A 股交易日，自动跳过周末和中国法定节假日。"""
    d = reference or date.today()
    for _ in range(30):
        if chinese_calendar.is_workday(d):
            return d
        d -= timedelta(days=1)
    return reference or date.today()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_sync_state(**updates) -> None:
    with _sync_lock:
        _sync_state.update(updates)


def _get_sync_state_copy() -> dict:
    with _sync_lock:
        return dict(_sync_state)


def _mark_sync_started() -> bool:
    with _sync_lock:
        if _sync_state["status"] in ("running", "syncing_kline"):
            return False
        _sync_state.update({
            "status": "running",
            "progress": 0,
            "total": 0,
            "kline_progress": 0,
            "kline_total": 0,
            "started_at": _utc_now_iso(),
            "finished_at": None,
            "message": "",
            "error": None,
        })
        return True


def _run_sync():
    """Execute full A-share stock sync in a background thread."""
    try:
        db = DatabaseManager.get_instance()
        today_start = datetime.combine(date.today(), datetime.min.time())

        # Check if stock list was already synced today
        with db.get_session() as session:
            latest_sync = session.query(func.max(StockMeta.last_sync_at)).scalar()
        already_synced_today = latest_sync is not None and latest_sync >= today_start

        delisted = 0
        delisted_codes_count = 0
        added, updated = 0, 0
        skip_phase_one = False

        if already_synced_today:
            skip_phase_one = True
            # Check if ALL active stocks have K-line data
            with db.get_session() as session:
                active_count = session.query(func.count(StockMeta.id)).filter(StockMeta.status == "active").scalar()
                stock_with_data = session.query(func.count(func.distinct(StockDaily.code))).scalar()
            if stock_with_data < active_count:
                logger.info("[StocksSync] K 线数据不完整 (%d/%d)，强制执行 K 线同步", stock_with_data, active_count)
            else:
                logger.info("[StocksSync] 今日已同步，跳过 Phase 1 和 K 线阶段")
                with db.get_session() as session:
                    count = session.query(func.count(StockMeta.id)).filter(StockMeta.status == "active").scalar()
                _set_sync_state(
                    status="success",
                    progress=count,
                    kline_progress=count,
                    kline_total=count,
                    finished_at=_utc_now_iso(),
                    message=f"同步完成: 今日已同步，跳过列表和 K 线更新",
                )
                logger.info("[StocksSync] 同步完成（跳过）")
                return

        if skip_phase_one:
            with db.get_session() as session:
                stocks = [
                    {"code": row.code, "name": row.name}
                    for row in session.query(StockMeta.code, StockMeta.name)
                    .filter(StockMeta.status == "active")
                    .order_by(StockMeta.code)
                    .all()
                ]
        else:
            # Phase 1: Fetch stock list + delisted cleanup
            fetcher = AkshareFetcher()
            stocks_raw = fetcher.get_all_a_stocks()

            if not stocks_raw:
                _set_sync_state(
                    status="failed",
                    message="未能从数据源获取股票列表",
                    finished_at=_utc_now_iso(),
                )
                return

            _set_sync_state(total=len(stocks_raw))
            now = datetime.now()

            with db.get_session() as session:
                all_codes = [s["code"] for s in stocks_raw]
                existing = {
                    row.code: row
                    for row in session.query(StockMeta).filter(StockMeta.code.in_(all_codes)).all()
                }

                for i, item in enumerate(stocks_raw):
                    code = item["code"]
                    meta = existing.get(code)

                    if meta:
                        meta.name = item["name"]
                        meta.market = item["market"]
                        meta.pe_ttm = item.get("pe_ttm")
                        meta.pb = item.get("pb")
                        meta.total_market_cap = item.get("total_market_cap")
                        meta.circulating_market_cap = item.get("circulating_market_cap")
                        meta.last_sync_at = now
                        updated += 1
                    else:
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

                    if (i + 1) % 100 == 0:
                        _set_sync_state(progress=i + 1)

                # Mark delisted stocks and clean up daily data
                current_codes = set(all_codes)
                delisted_codes = [
                    row.code
                    for row in session.query(StockMeta.code)
                    .filter(StockMeta.code.notin_(current_codes), StockMeta.status == "active")
                    .all()
                ]

                if delisted_codes:
                    session.execute(delete(StockDaily).where(StockDaily.code.in_(delisted_codes)))
                    delisted_codes_count = len(delisted_codes)

                delisted = (
                    session.query(StockMeta)
                    .filter(StockMeta.code.notin_(current_codes), StockMeta.status == "active")
                    .update({"status": "delisted", "updated_at": now}, synchronize_session=False)
                )

                session.commit()

            stocks = stocks_raw

        # Phase 2: Incremental K-line sync — batch concurrent for speed
        kline_total = len(stocks)
        kline_incremental = 0
        kline_skipped = kline_total
        kline_failed = 0

        active_codes = [s["code"] for s in stocks]
        today = _get_latest_trading_day()
        _set_sync_state(
            status="syncing_kline",
            kline_progress=0,
            kline_total=kline_total,
        )

        # Batch query latest dates for all stocks at once
        with db.get_session() as session:
            from sqlalchemy import select
            latest_dates = {
                row[0]: row[1]
                for row in session.execute(
                    select(StockDaily.code, func.max(StockDaily.date))
                    .where(StockDaily.code.in_(active_codes))
                    .group_by(StockDaily.code)
                ).all()
            }

        # Categorize in memory (no per-stock DB calls)
        full_fetch_codes: List[str] = []
        incremental_codes: List[str] = []
        skip_count = 0

        for code in active_codes:
            latest_date = latest_dates.get(code)
            if latest_date is None:
                full_fetch_codes.append(code)
            elif latest_date >= today - timedelta(days=1):
                skip_count += 1
            else:
                incremental_codes.append(code)

        kline_skipped = skip_count
        progress_offset = skip_count
        _set_sync_state(kline_progress=progress_offset)

        from data_provider.akshare_fetcher import AkshareFetcher as AF
        batch_fetcher = AF(sleep_min=0.3, sleep_max=0.5)
        inc_fetcher = AF(sleep_min=0.3, sleep_max=0.5)

        if full_fetch_codes:
            import concurrent.futures
            logger.info("[StocksSync] K线全量并发拉取 %d 只股票", len(full_fetch_codes))

            def _fetch_one(code: str) -> tuple:
                df = batch_fetcher.fetch_stock_kline_history(code, days=365)
                return code, df

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(_fetch_one, code): code for code in full_fetch_codes}
                for future in concurrent.futures.as_completed(futures):
                    code, df = future.result()
                    try:
                        if df is not None and not df.empty:
                            db.save_daily_data(df, code, "akshare")
                            kline_incremental += 1
                        else:
                            kline_failed += 1
                    except Exception as e:
                        logger.warning("[StocksSync] K线保存失败 %s: %s", code, str(e)[:120])
                        kline_failed += 1
                    progress_offset += 1
                    _set_sync_state(kline_progress=progress_offset)

        for code in incremental_codes:
            try:
                latest_date = db.get_latest_daily_date(code)
                if latest_date is None or latest_date >= today - timedelta(days=1):
                    kline_skipped += 1
                    continue

                inc_fetcher._enforce_rate_limit()
                df = inc_fetcher.fetch_stock_kline_history(
                    code, days=max(60, (today - latest_date).days + 5),
                )
                if df is not None and not df.empty:
                    db.save_daily_data(df, code, "akshare")
                    kline_incremental += 1
                else:
                    kline_failed += 1
            except Exception as e:
                logger.warning("[StocksSync] K线增量同步失败 %s: %s", code, str(e)[:120])
                kline_failed += 1
            progress_offset += 1
            _set_sync_state(kline_progress=progress_offset)

        msg_parts = []
        if already_synced_today:
            msg_parts.append("今日已同步，跳过列表更新")
        else:
            msg_parts.append(f"新增 {added}, 更新 {updated}, 退市 {delisted}(清理日线 {delisted_codes_count})")
        msg_parts.append(f"K线增量 {kline_incremental}, 跳过 {kline_skipped}, 失败 {kline_failed}")

        _set_sync_state(
            status="success",
            progress=len(stocks),
            kline_progress=kline_total,
            finished_at=_utc_now_iso(),
            message=f"同步完成: {', '.join(msg_parts)}",
        )

        logger.info(
            "[StocksSync] 同步完成: 总数=%d 新增=%d 更新=%d 退市=%d",
            len(stocks), added, updated, delisted,
        )

    except Exception as e:
        _set_sync_state(status="failed", error=str(e), finished_at=_utc_now_iso())
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
    if not _mark_sync_started():
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
    state = _get_sync_state_copy()
    if state["total"] == 0:
        try:
            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                total = session.query(StockMeta).filter(StockMeta.status == "active").count()
            if total > 0:
                return {
                    **state,
                    "status": "success" if state["status"] == "idle" else state["status"],
                    "total": total,
                    "message": state["message"] or "数据已存在（来自数据库）",
                }
        except Exception:
            logger.warning("获取股票状态失败", exc_info=True)
    return state