# -*- coding: utf-8 -*-
"""A-share stock list and K-line sync endpoints."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import chinese_calendar
from fastapi import Depends, HTTPException
from sqlalchemy import func, select

from api.deps import get_system_config_service
from src.tools._kline import fetch_and_persist_kline
from api.v1.endpoints.stocks import router
from api.v1.schemas.common import ErrorResponse
from src.services.data_maintenance import ensure_stock_universe
from src.services.system_config_service import SystemConfigService
from src.storage import DataMaintenanceJob, DatabaseManager, StockDaily, StockMeta

logger = logging.getLogger(__name__)

KLINE_SYNC_MAX_WORKERS = 5
KLINE_SYNC_ATTEMPTS = 2
KLINE_SYNC_RETRY_DELAY_SECONDS = 1.5
STATUS_DB_FALLBACK_TTL_SECONDS = 30.0
STATUS_RUNNING_STALE_AFTER_SECONDS = 60.0
_status_db_fallback_cache = {"expires_at": 0.0, "total": 0}


def _initial_state() -> dict:
    return {
        "status": "idle",
        "progress": 0,
        "total": 0,
        "kline_progress": 0,
        "kline_total": 0,
        "started_at": None,
        "finished_at": None,
        "message": "",
        "error": None,
    }


_list_sync_lock = threading.Lock()
_list_sync_state = _initial_state()
_kline_sync_lock = threading.Lock()
_kline_sync_state = _initial_state()
_missing_kline_sync_lock = threading.Lock()
_missing_kline_sync_state = _initial_state()
_financial_sync_lock = threading.Lock()
_financial_sync_state = _initial_state()


def _set_financial_state(**updates) -> None:
    _set_state(_financial_sync_state, _financial_sync_lock, **updates)


def _get_financial_state_copy() -> dict:
    return _get_state_copy(_financial_sync_state, _financial_sync_lock)


def _mark_financial_sync_started() -> bool:
    """财务同步允许与 list/kline 并发；不检查 running 状态。"""
    with _financial_sync_lock:
        if _financial_sync_state["status"] == "running":
            return False
        _financial_sync_state.update(_initial_state())
        _financial_sync_state.update(
            {
                "status": "running",
                "started_at": _utc_now_iso(),
            }
        )
        return True


def _get_latest_trading_day(reference: date | None = None) -> date:
    """Return the latest A-share trading day before or on reference."""
    d = reference or date.today()
    for _ in range(30):
        if chinese_calendar.is_workday(d):
            return d
        d -= timedelta(days=1)
    return reference or date.today()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_state(state: dict, lock: threading.Lock, **updates) -> None:
    with lock:
        state.update(updates)


def _get_state_copy(state: dict, lock: threading.Lock) -> dict:
    with lock:
        return dict(state)


def _mark_started(state: dict, lock: threading.Lock, *, status: str = "running") -> bool:
    with lock:
        if state["status"] in ("running", "syncing_kline"):
            return False
        state.update(_initial_state())
        state.update(
            {
                "status": status,
                "started_at": _utc_now_iso(),
            }
        )
        return True


def _set_list_state(**updates) -> None:
    _set_state(_list_sync_state, _list_sync_lock, **updates)


def _get_list_state_copy() -> dict:
    return _get_state_copy(_list_sync_state, _list_sync_lock)


def _mark_list_sync_started() -> bool:
    return _mark_started(_list_sync_state, _list_sync_lock)


def _set_kline_state(**updates) -> None:
    _set_state(_kline_sync_state, _kline_sync_lock, **updates)


def _get_kline_state_copy() -> dict:
    return _get_state_copy(_kline_sync_state, _kline_sync_lock)


def _mark_kline_sync_started() -> bool:
    return _mark_started(_kline_sync_state, _kline_sync_lock, status="syncing_kline")


def _set_missing_kline_state(**updates) -> None:
    _set_state(_missing_kline_sync_state, _missing_kline_sync_lock, **updates)


def _get_missing_kline_state_copy() -> dict:
    return _get_state_copy(_missing_kline_sync_state, _missing_kline_sync_lock)


def _mark_missing_kline_sync_started() -> bool:
    return _mark_started(_missing_kline_sync_state, _missing_kline_sync_lock, status="syncing_kline")


def _get_active_stock_codes() -> list[str]:
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        return [
            row.code
            for row in session.query(StockMeta.code).filter(StockMeta.status == "active").order_by(StockMeta.code).all()
        ]


def _run_list_sync() -> None:
    def on_progress(processed: int, total: int, message: str) -> None:
        _set_list_state(progress=processed, total=total, message=message)

    try:
        maintenance = ensure_stock_universe(
            trigger="legacy_stocks_api",
            force=True,
            on_progress=on_progress,
        )
        result = maintenance.get("changes")
        if maintenance.get("maintenance_status") != "success" or not result:
            raise RuntimeError(
                maintenance.get("warning")
                or "股票列表同步未完成，当前仍使用本地缓存"
            )
        _set_list_state(
            status="success",
            progress=result["total"],
            total=result["total"],
            finished_at=_utc_now_iso(),
            message=(
                f"同步列表完成(基础资料): 新增 {result['added']}, 更新 {result['updated']}, "
                f"标记退市 {result['delisted']}"
            ),
        )
    except Exception as e:
        _set_list_state(
            status="failed",
            error=str(e),
            message="股票列表同步失败",
            finished_at=_utc_now_iso(),
        )
        logger.error("[StocksSync] 列表同步失败: %s", e, exc_info=True)


def _sync_one_kline(code: str, today: date, latest_dates: dict[str, date | None]) -> tuple[str, str]:
    latest_date = latest_dates.get(code)
    if latest_date is not None and latest_date >= today:
        return code, "skipped"

    start_date = latest_date + timedelta(days=1) if latest_date else None
    if start_date and start_date > today:
        return code, "skipped"

    last_error: Exception | None = None
    for attempt in range(1, KLINE_SYNC_ATTEMPTS + 1):
        try:
            if start_date:
                records, _source = fetch_and_persist_kline(
                    code,
                    start_date=start_date,
                    end_date=today,
                    use_cache=False,
                )
            else:
                records, _source = fetch_and_persist_kline(code, count=500, use_cache=False)
            if records:
                return code, "updated"
        except Exception as e:
            last_error = e

        if attempt < KLINE_SYNC_ATTEMPTS:
            time.sleep(KLINE_SYNC_RETRY_DELAY_SECONDS * attempt)

    if last_error:
        logger.warning("[StocksSync] K线同步重试失败 %s: %s", code, str(last_error)[:120])
    return code, "failed"


def _run_kline_sync_for_codes(
    codes: list[str],
    set_status: Callable[..., None],
    latest_dates: dict[str, date | None] | None = None,
) -> None:
    today = _get_latest_trading_day()
    db = DatabaseManager.get_instance()
    if latest_dates is None:
        with db.get_session() as session:
            latest_dates = {
                row[0]: row[1]
                for row in session.execute(
                    select(StockDaily.code, func.max(StockDaily.date))
                    .where(StockDaily.code.in_(codes))
                    .group_by(StockDaily.code)
                ).all()
            }

    total = len(codes)
    updated = skipped = failed = 0
    set_status(status="syncing_kline", kline_progress=0, kline_total=total, total=total)

    def _fetch(code: str) -> tuple[str, str]:
        try:
            return _sync_one_kline(code, today, latest_dates or {})
        except Exception as e:
            logger.warning("[StocksSync] K线同步失败 %s: %s", code, str(e)[:120])
            return code, "failed"

    progress = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=KLINE_SYNC_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch, code): code for code in codes}
        for future in concurrent.futures.as_completed(futures):
            _code, result = future.result()
            if result == "updated":
                updated += 1
            elif result == "skipped":
                skipped += 1
            else:
                failed += 1
            progress += 1
            set_status(kline_progress=progress)

    set_status(
        status="success" if failed == 0 else "failed",
        progress=total,
        kline_progress=total,
        finished_at=_utc_now_iso(),
        message=f"K线同步完成: 更新 {updated}, 跳过 {skipped}, 失败 {failed}",
        error=None if failed == 0 else f"{failed} 只股票同步失败",
    )


def _run_kline_sync() -> None:
    try:
        codes = _get_active_stock_codes()
        if not codes:
            _set_kline_state(
                status="failed", error="请先同步股票列表", message="请先同步股票列表", finished_at=_utc_now_iso()
            )
            return
        _run_kline_sync_for_codes(codes, _set_kline_state)
    except Exception as e:
        _set_kline_state(status="failed", error=str(e), finished_at=_utc_now_iso())
        logger.error("[StocksSync] K线同步失败: %s", e, exc_info=True)


def _run_missing_kline_sync(codes: list[str]) -> None:
    try:
        active = set(_get_active_stock_codes())
        target_codes = [code for code in codes if code in active]
        if not target_codes:
            _set_missing_kline_state(
                status="failed",
                error="未找到可同步的 active 股票",
                message="未找到可同步的 active 股票",
                finished_at=_utc_now_iso(),
            )
            return
        _run_kline_sync_for_codes(target_codes, _set_missing_kline_state, latest_dates={})
    except Exception as e:
        _set_missing_kline_state(status="failed", error=str(e), finished_at=_utc_now_iso())
        logger.error("[StocksSync] 缺失K线同步失败: %s", e, exc_info=True)


def _latest_stock_universe_status() -> dict | None:
    db = DatabaseManager.get_instance()
    try:
        with db.get_session() as session:
            job = (
                session.query(DataMaintenanceJob)
                .filter(DataMaintenanceJob.dataset == "stock_universe")
                .order_by(DataMaintenanceJob.created_at.desc())
                .first()
            )
            if not isinstance(job, DataMaintenanceJob):
                return None
            status = job.status
            error = job.error
            message = job.message or ""
            if status == "queued":
                status = "running"
            if status == "partial":
                status = "failed"
            if status == "running":
                heartbeat_at = job.updated_at or job.started_at or job.created_at
                if heartbeat_at and (datetime.now() - heartbeat_at).total_seconds() > STATUS_RUNNING_STALE_AFTER_SECONDS:
                    status = "failed"
                    error = error or "股票列表同步任务已中断，请重新同步"
                    message = "股票列表同步任务已中断"
            return {
                **_initial_state(),
                "status": status,
                "progress": job.progress or 0,
                "total": job.total or 0,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "message": message,
                "error": error,
            }
    except Exception:
        logger.warning("读取股票列表持久化同步状态失败", exc_info=True)
        return None


def _status_with_db_fallback(state: dict) -> dict:
    if state["status"] != "idle" or state["total"] != 0:
        return state

    persisted_status = _latest_stock_universe_status()
    if persisted_status is not None:
        return persisted_status

    try:
        now = time.monotonic()
        if now < _status_db_fallback_cache["expires_at"]:
            total = _status_db_fallback_cache["total"]
        else:
            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                total = session.query(StockMeta).filter(StockMeta.status == "active").count()
            _status_db_fallback_cache.update(
                {
                    "expires_at": now + STATUS_DB_FALLBACK_TTL_SECONDS,
                    "total": total,
                }
            )
        if total > 0:
            if state["status"] != "idle":
                return {
                    **state,
                    "total": state["total"] or total,
                }
            return {
                **state,
                "status": "success",
                "total": total,
                "message": state["message"] or "数据已存在（来自数据库）",
            }
    except Exception:
        logger.warning("获取股票列表状态失败", exc_info=True)
    return state


@router.post(
    "/sync/list",
    summary="Sync A-share stock list",
    responses={409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def sync_stock_list(
    service: SystemConfigService = Depends(get_system_config_service),
):
    """Trigger stock metadata sync only."""
    current_state = _get_list_state_copy()
    persisted_status = _latest_stock_universe_status()
    if current_state["status"] == "running" or (persisted_status and persisted_status["status"] == "running"):
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "股票列表同步正在进行中，请稍后再试"}
        )
    if not _mark_list_sync_started():
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "股票列表同步正在进行中，请稍后再试"}
        )
    _set_list_state(message="准备同步股票列表")
    thread = threading.Thread(target=_run_list_sync, daemon=True)
    thread.start()
    return {"success": True, "message": "同步列表已启动", "status": "running"}


@router.get("/sync/list/status", summary="Get stock list sync status")
def get_stock_list_sync_status():
    return _status_with_db_fallback(_get_list_state_copy())


@router.post(
    "/sync/kline",
    summary="Sync K-line for all active stocks",
    responses={409: {"model": ErrorResponse}, 400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def sync_stock_kline(
    service: SystemConfigService = Depends(get_system_config_service),
):
    """Trigger K-line sync for active StockMeta only."""
    if not _get_active_stock_codes():
        raise HTTPException(status_code=400, detail={"error": "stock_list_required", "message": "请先同步股票列表"})
    if not _mark_kline_sync_started():
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "K线同步正在进行中，请稍后再试"}
        )
    thread = threading.Thread(target=_run_kline_sync, daemon=True)
    thread.start()
    return {"success": True, "message": "同步K线已启动", "status": "syncing_kline"}


@router.get("/sync/kline/status", summary="Get K-line sync status")
def get_stock_kline_sync_status():
    return _get_kline_state_copy()


@router.post(
    "/kline/sync-missing",
    summary="Sync K-line for missing stock codes",
    responses={409: {"model": ErrorResponse}, 400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def sync_missing_kline(body: dict):
    """Trigger K-line sync for a specified missing-code list."""
    codes = body.get("codes") or []
    if not isinstance(codes, list) or not codes:
        raise HTTPException(
            status_code=400, detail={"error": "missing_codes_required", "message": "请提供缺失股票代码列表"}
        )
    clean_codes = [str(code).strip() for code in codes if str(code).strip()]
    if not clean_codes:
        raise HTTPException(
            status_code=400, detail={"error": "missing_codes_required", "message": "请提供缺失股票代码列表"}
        )
    if not _mark_missing_kline_sync_started():
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "缺失K线同步正在进行中，请稍后再试"}
        )
    _set_missing_kline_state(kline_total=len(clean_codes), total=len(clean_codes))
    thread = threading.Thread(target=_run_missing_kline_sync, args=(clean_codes,), daemon=True)
    thread.start()
    return _get_missing_kline_state_copy()


@router.get("/kline/sync-missing/status", summary="Get missing K-line sync status")
def get_missing_kline_sync_status():
    return _get_missing_kline_state_copy()


@router.post(
    "/sync/financial",
    summary="按报告期拉全市场业绩快报写入 stock_meta",
    responses={409: {"model": ErrorResponse}, 400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def sync_stock_financial(
    service: SystemConfigService = Depends(get_system_config_service),
):
    """按最近一个已披露的报告期（季度/年报）一次拉全市场业绩快报写入 stock_meta。"""
    if not _get_active_stock_codes():
        raise HTTPException(status_code=400, detail={"error": "stock_list_required", "message": "请先同步股票列表"})
    if not _mark_financial_sync_started():
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "财报同步正在进行中，请稍后再试"}
        )
    period = _financials_sync.latest_report_period()
    _set_financial_state(message=f"已启动 (报告期 {period})")
    thread = threading.Thread(target=_financials_sync.run_financial_sync, args=(period,), daemon=True)
    thread.start()
    return {"success": True, "message": f"同步财报已启动 (报告期 {period})", "status": "running", "period": period}


@router.get("/sync/financial/status", summary="Get financial sync status")
def get_stock_financial_sync_status():
    return _get_financial_state_copy()


# 注入状态对象给 _financials_sync（必须在 _utc_now_iso 等所有 helper 定义后）
from api.v1.endpoints.stocks import _financials_sync  # noqa: E402

_financials_sync.attach_state(
    state=_financial_sync_state,
    lock=_financial_sync_lock,
    set_state=_set_financial_state,
    initial_state=_initial_state,
    utc_now_iso=_utc_now_iso,
)
