# -*- coding: utf-8 -*-
"""A-share stock list and K-line sync endpoints."""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import chinese_calendar
from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

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
        "job_id": None,
        "status": "idle",
        "progress": 0,
        "total": 0,
        "kline_progress": 0,
        "kline_total": 0,
        "started_at": None,
        "finished_at": None,
        "message": "",
        "error": None,
        "updated_count": 0,
        "no_data_count": 0,
        "failed_count": 0,
        "incomplete_count": 0,
        "unmatched_count": 0,
        "report_period": None,
        "periods_checked": 0,
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


def _financial_state_with_db_fallback(state: dict) -> dict:
    """进程重启后从维护任务审计表恢复最近一次财报终态。"""
    # A detached worker owns the durable progress after the API returns.  If
    # the local state carries a job id, always reconcile it with the database;
    # otherwise an API process would keep returning its initial "running"
    # snapshot after the worker has already finished.
    if state["status"] != "idle" and not state.get("job_id"):
        return state
    if state["status"] == "idle" and state["total"] != 0:
        return state
    db = DatabaseManager.get_instance()
    try:
        with db.get_session() as session:
            query = session.query(DataMaintenanceJob).filter(DataMaintenanceJob.dataset == "financial_reports")
            if state.get("job_id"):
                job = query.filter(DataMaintenanceJob.id == state["job_id"]).first()
                if job is None:
                    # The singleton job id can rotate when another process
                    # takes over a stale run; surface that newer run instead
                    # of keeping an orphaned local snapshot forever.
                    job = query.order_by(DataMaintenanceJob.created_at.desc()).first()
            else:
                job = query.order_by(DataMaintenanceJob.created_at.desc()).first()
            if job is None:
                return state
            message = job.message or "已恢复最近一次财报同步结果"
            status = job.status
            error = job.error
            if status in {"running", "queued"}:
                heartbeat_at = job.updated_at or job.started_at or job.created_at
                if heartbeat_at and datetime.now() - heartbeat_at > timedelta(seconds=STATUS_RUNNING_STALE_AFTER_SECONDS):
                    status = "failed"
                    error = error or "财报同步任务已中断，请重新同步"
                    message = "财报同步任务已中断"
            updated_match = re.search(r"已更新\s+(\d+)\s*/", message)
            no_data_match = re.search(r"无可用财报\s+(\d+)", message)
            failed_match = re.search(r"失败\s+(\d+)", message)
            incomplete_match = re.search(r"(\d+)\s+只股票没有拿到完整核心字段", message)
            return {
                **state,
                "job_id": job.id,
                "status": status,
                "progress": job.progress or 0,
                "total": job.total or 0,
                "updated_count": int(updated_match.group(1)) if updated_match else (job.progress or 0),
                "no_data_count": int(no_data_match.group(1)) if no_data_match else 0,
                "failed_count": int(failed_match.group(1)) if failed_match else 0,
                "incomplete_count": int(incomplete_match.group(1)) if incomplete_match else 0,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "message": message,
                "error": error,
                "report_period": job.target_data_time,
            }
    except Exception:
        logger.warning("读取财报同步终态失败", exc_info=True)
        return state


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


_sync_job_last_persist: dict[str, tuple[float, int]] = {}


def _claim_persisted_sync_job(
    *,
    dataset: str,
    scope_key: str,
    target_data_time: str,
    total: int,
    trigger: str,
) -> tuple[str, bool]:
    """Claim a durable sync job so a second API process cannot duplicate it."""
    # DataMaintenanceJob.id is VARCHAR(36); keep the UUID unprefixed so this
    # works on PostgreSQL as well as the local SQLite database.
    job_id = str(uuid.uuid4())
    now = datetime.now()
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        existing = (
            session.query(DataMaintenanceJob)
            .filter(
                DataMaintenanceJob.dataset == dataset,
                DataMaintenanceJob.scope_key == scope_key,
                DataMaintenanceJob.target_data_time == target_data_time,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.status in {"running", "queued"}:
                heartbeat_at = existing.updated_at or existing.started_at or existing.created_at
                if heartbeat_at and now - heartbeat_at < timedelta(seconds=STATUS_RUNNING_STALE_AFTER_SECONDS):
                    return existing.id, False
            old_id = existing.id
            claim_query = session.query(DataMaintenanceJob).filter(
                DataMaintenanceJob.id == old_id,
                DataMaintenanceJob.status == existing.status,
            )
            if existing.updated_at is not None:
                claim_query = claim_query.filter(DataMaintenanceJob.updated_at == existing.updated_at)
            claimed_rows = claim_query.update(
                {
                    # Rotate the primary key on takeover so a stale worker
                    # holding the old job id can no longer write progress.
                    "id": job_id,
                    "status": "running",
                    "trigger": trigger,
                    "progress": 0,
                    "total": total,
                    "message": "同步任务已启动",
                    "error": None,
                    "started_at": now,
                    "finished_at": None,
                    "updated_at": now,
                },
                synchronize_session=False,
            )
            if claimed_rows != 1:
                session.rollback()
                current = (
                    session.query(DataMaintenanceJob)
                    .filter(
                        DataMaintenanceJob.dataset == dataset,
                        DataMaintenanceJob.scope_key == scope_key,
                        DataMaintenanceJob.target_data_time == target_data_time,
                    )
                    .one_or_none()
                )
                return (current.id, False) if current is not None else (job_id, False)
            session.commit()
            return job_id, True
        try:
            session.add(
                DataMaintenanceJob(
                    id=job_id,
                    dataset=dataset,
                    scope_key=scope_key,
                    target_data_time=target_data_time,
                    trigger=trigger,
                    status="running",
                    total=total,
                    started_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            return job_id, True
        except IntegrityError:
            session.rollback()
            existing = (
                session.query(DataMaintenanceJob)
                .filter(
                    DataMaintenanceJob.dataset == dataset,
                    DataMaintenanceJob.scope_key == scope_key,
                    DataMaintenanceJob.target_data_time == target_data_time,
                )
                .one_or_none()
            )
            return (existing.id, False) if existing is not None else (job_id, False)


def _persist_sync_job_state(dataset: str, state: dict[str, Any]) -> None:
    job_id = state.get("job_id")
    if not job_id:
        return
    progress = int(state.get("kline_progress") or state.get("progress") or 0)
    status = state.get("status")
    now_monotonic = time.monotonic()
    last_time, last_progress = _sync_job_last_persist.get(str(job_id), (0.0, -1))
    terminal = status in {"success", "failed", "partial"}
    if not terminal and progress == last_progress and now_monotonic - last_time < 1.0:
        return
    db = DatabaseManager.get_instance()
    try:
        with db.get_session() as session:
            values: dict[str, Any] = {
                # A detached worker attaches its job id before entering the
                # sync routine. Keep that short initialization window
                # claimable as running instead of exposing an ``idle`` job.
                "status": "running" if status in {"idle", "syncing_kline"} else status,
                "progress": progress,
                "total": int(state.get("kline_total") or state.get("total") or 0),
                "message": state.get("message") or "",
                "error": state.get("error"),
                "updated_at": datetime.now(),
            }
            if terminal:
                values["finished_at"] = datetime.now()
            session.query(DataMaintenanceJob).filter(DataMaintenanceJob.id == job_id).update(values)
            session.commit()
        _sync_job_last_persist[str(job_id)] = (now_monotonic, progress)
    except Exception:
        logger.warning("持久化同步任务状态失败: %s", job_id, exc_info=True)


def _release_sync_job_claim(job_id: str, message: str) -> None:
    """Release a claim that lost the process-local state race."""
    try:
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            session.query(DataMaintenanceJob).filter(DataMaintenanceJob.id == job_id).update(
                {
                    "status": "failed",
                    "message": message,
                    "error": message,
                    "finished_at": datetime.now(),
                    "updated_at": datetime.now(),
                }
            )
            session.commit()
    except Exception:
        logger.warning("释放同步任务租约失败: %s", job_id, exc_info=True)


def _latest_persisted_sync_state(dataset: str) -> dict | None:
    db = DatabaseManager.get_instance()
    try:
        with db.get_session() as session:
            job = (
                session.query(DataMaintenanceJob)
                .filter(DataMaintenanceJob.dataset == dataset)
                .order_by(DataMaintenanceJob.created_at.desc())
                .first()
            )
            if job is None:
                return None
            status = job.status
            message = job.message or ""
            error = job.error
            if status in {"running", "queued"}:
                heartbeat_at = job.updated_at or job.started_at or job.created_at
                if heartbeat_at and datetime.now() - heartbeat_at > timedelta(seconds=STATUS_RUNNING_STALE_AFTER_SECONDS):
                    status = "failed"
                    error = error or "同步任务已中断，请重新同步"
                    message = "同步任务已中断"
                else:
                    status = "syncing_kline" if dataset.startswith("kline") else "running"
            return {
                **_initial_state(),
                "job_id": job.id,
                "status": status,
                "progress": job.progress or 0,
                "total": job.total or 0,
                "kline_progress": job.progress or 0,
                "kline_total": job.total or 0,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "message": message,
                "error": error,
            }
    except Exception:
        logger.warning("读取持久化同步任务状态失败: %s", dataset, exc_info=True)
        return None


def _state_with_persisted_sync_fallback(state: dict, dataset: str) -> dict:
    persisted = _latest_persisted_sync_state(dataset)
    if state.get("job_id") and persisted and persisted.get("job_id") == state.get("job_id"):
        return {**state, **persisted}
    if state.get("job_id") and persisted:
        local_started = state.get("started_at")
        persisted_started = persisted.get("started_at")
        if local_started and persisted_started:
            try:
                local_value = datetime.fromisoformat(str(local_started).replace("Z", "+00:00"))
                persisted_value = datetime.fromisoformat(str(persisted_started).replace("Z", "+00:00"))
                if local_value.tzinfo is not None:
                    local_value = local_value.replace(tzinfo=None)
                if persisted_value.tzinfo is not None:
                    persisted_value = persisted_value.replace(tzinfo=None)
                if persisted_value >= local_value:
                    # Another API process may have taken over the singleton
                    # dataset after this process's local state was created.
                    return persisted
            except ValueError:
                pass
    if state["status"] != "idle" or state["total"] != 0:
        return state
    return persisted or state


def _launch_detached_worker(module: str, *args: str) -> None:
    subprocess.Popen(
        [sys.executable, "-m", module, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        cwd=str(Path(__file__).resolve().parents[4]),
    )


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
    _persist_sync_job_state("stock_universe", _get_state_copy(_list_sync_state, _list_sync_lock))


def _get_list_state_copy() -> dict:
    return _get_state_copy(_list_sync_state, _list_sync_lock)


def _mark_list_sync_started() -> bool:
    return _mark_started(_list_sync_state, _list_sync_lock)


def _set_kline_state(**updates) -> None:
    _set_state(_kline_sync_state, _kline_sync_lock, **updates)
    _persist_sync_job_state("kline", _get_state_copy(_kline_sync_state, _kline_sync_lock))


def _get_kline_state_copy() -> dict:
    return _state_with_persisted_sync_fallback(
        _get_state_copy(_kline_sync_state, _kline_sync_lock),
        "kline",
    )


def _mark_kline_sync_started() -> bool:
    started = _mark_started(_kline_sync_state, _kline_sync_lock, status="syncing_kline")
    if started:
        _set_kline_state(message="正在同步全市场 K 线")
    return started


def _set_missing_kline_state(**updates) -> None:
    _set_state(_missing_kline_sync_state, _missing_kline_sync_lock, **updates)
    _persist_sync_job_state("kline_missing", _get_state_copy(_missing_kline_sync_state, _missing_kline_sync_lock))


def _get_missing_kline_state_copy() -> dict:
    return _state_with_persisted_sync_fallback(
        _get_state_copy(_missing_kline_sync_state, _missing_kline_sync_lock),
        "kline_missing",
    )


def _mark_missing_kline_sync_started() -> bool:
    started = _mark_started(_missing_kline_sync_state, _missing_kline_sync_lock, status="syncing_kline")
    if started:
        _set_missing_kline_state(message="正在补齐缺失 K 线")
    return started


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
                "job_id": job.id,
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


def _persisted_status_belongs_to_current_run(state: dict, persisted: dict) -> bool:
    if state.get("job_id") and persisted.get("job_id") == state.get("job_id"):
        return True
    if state["status"] == "idle" or state["total"] == 0:
        return True
    local_started = state.get("started_at")
    persisted_started = persisted.get("started_at")
    if not local_started or not persisted_started:
        return False
    try:
        local_value = datetime.fromisoformat(str(local_started).replace("Z", "+00:00"))
        persisted_value = datetime.fromisoformat(str(persisted_started).replace("Z", "+00:00"))
        if local_value.tzinfo is not None:
            local_value = local_value.replace(tzinfo=None)
        if persisted_value.tzinfo is not None:
            persisted_value = persisted_value.replace(tzinfo=None)
        return persisted_value >= local_value
    except ValueError:
        return False


def _status_with_db_fallback(state: dict) -> dict:
    persisted_status = _latest_stock_universe_status()
    if persisted_status is not None and _persisted_status_belongs_to_current_run(state, persisted_status):
        return persisted_status

    if state["status"] != "idle" or state["total"] != 0:
        return state

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
    job_id, claimed = _claim_persisted_sync_job(
        dataset="stock_universe",
        scope_key="all",
        target_data_time=date.today().isoformat(),
        total=0,
        trigger="settings",
    )
    if not claimed:
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "股票列表同步正在进行中，请稍后再试"}
        )
    if not _mark_list_sync_started():
        _release_sync_job_claim(job_id, "本进程已有股票列表同步任务")
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "股票列表同步正在进行中，请稍后再试"}
        )
    _set_list_state(job_id=job_id, message="准备同步股票列表")
    try:
        _launch_detached_worker("src.services.stock_list_sync_worker", job_id)
    except Exception as exc:
        _release_sync_job_claim(job_id, "股票列表后台任务启动失败")
        _set_list_state(
            status="failed",
            error=str(exc)[:300],
            message="股票列表后台任务启动失败",
            finished_at=_utc_now_iso(),
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "sync_worker_start_failed", "message": "股票列表后台任务启动失败"},
        ) from exc
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
    active_codes = _get_active_stock_codes()
    if not active_codes:
        raise HTTPException(status_code=400, detail={"error": "stock_list_required", "message": "请先同步股票列表"})
    target_data_time = _get_latest_trading_day().isoformat()
    job_id, claimed = _claim_persisted_sync_job(
        dataset="kline",
        scope_key="all",
        target_data_time=target_data_time,
        total=len(active_codes),
        trigger="settings",
    )
    if not claimed:
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "K线同步正在进行中，请稍后再试"}
        )
    if not _mark_kline_sync_started():
        _release_sync_job_claim(job_id, "本进程已有 K 线同步任务")
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "K线同步正在进行中，请稍后再试"}
        )
    _set_kline_state(job_id=job_id, message="准备同步全市场 K 线")
    try:
        _launch_detached_worker("src.services.kline_sync_worker", job_id, "all")
    except Exception as exc:
        _release_sync_job_claim(job_id, "K线后台任务启动失败")
        _set_kline_state(
            status="failed",
            error=str(exc)[:300],
            message="K线后台任务启动失败",
            finished_at=_utc_now_iso(),
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "sync_worker_start_failed", "message": "K线后台任务启动失败"},
        ) from exc
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
    scope_key = f"codes:{hashlib.sha256(','.join(sorted(set(clean_codes))).encode()).hexdigest()[:48]}"
    job_id, claimed = _claim_persisted_sync_job(
        dataset="kline_missing",
        scope_key=scope_key,
        target_data_time=_get_latest_trading_day().isoformat(),
        total=len(clean_codes),
        trigger="settings",
    )
    if not claimed:
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "缺失K线同步正在进行中，请稍后再试"}
        )
    if not _mark_missing_kline_sync_started():
        _release_sync_job_claim(job_id, "本进程已有缺失 K 线同步任务")
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "缺失K线同步正在进行中，请稍后再试"}
        )
    _set_missing_kline_state(job_id=job_id, kline_total=len(clean_codes), total=len(clean_codes))
    try:
        _launch_detached_worker(
            "src.services.kline_sync_worker",
            job_id,
            "missing",
            ",".join(clean_codes),
        )
    except Exception as exc:
        _release_sync_job_claim(job_id, "缺失 K 线后台任务启动失败")
        _set_missing_kline_state(
            status="failed",
            error=str(exc)[:300],
            message="缺失K线后台任务启动失败",
            finished_at=_utc_now_iso(),
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "sync_worker_start_failed", "message": "缺失K线后台任务启动失败"},
        ) from exc
    return _get_missing_kline_state_copy()


@router.get("/kline/sync-missing/status", summary="Get missing K-line sync status")
def get_missing_kline_sync_status():
    return _get_missing_kline_state_copy()


@router.post(
    "/sync/financial",
    summary="为每只 active 股票同步最近可用财务摘要",
    responses={409: {"model": ErrorResponse}, 400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def sync_stock_financial(
    service: SystemConfigService = Depends(get_system_config_service),
):
    """按活跃股票覆盖同步最近可用财务报告，不以单一报告期返回行数作为总量。"""
    active_codes = _get_active_stock_codes()
    if not active_codes:
        raise HTTPException(status_code=400, detail={"error": "stock_list_required", "message": "请先同步股票列表"})
    persisted_status = _latest_persisted_sync_state("financial_reports")
    if persisted_status and persisted_status["status"] == "running":
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "财报同步正在进行中，请稍后再试"}
        )
    period = _financials_sync.latest_report_period()
    job_id, claimed = _claim_persisted_sync_job(
        dataset="financial_reports",
        scope_key="all",
        target_data_time=f"{period[:4]}-{period[4:6]}-{period[6:8]}",
        total=len(active_codes),
        trigger="settings",
    )
    if not claimed:
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "财报同步正在进行中，请稍后再试"}
        )
    if not _mark_financial_sync_started():
        _release_sync_job_claim(job_id, "本进程已有财报同步任务")
        raise HTTPException(
            status_code=409, detail={"error": "sync_in_progress", "message": "财报同步正在进行中，请稍后再试"}
    )
    _set_financial_state(
        job_id=job_id,
        total=len(active_codes),
        message=f"已启动：将为 {len(active_codes)} 只活跃股票寻找最近可用财报 (首选报告期 {period})",
        report_period=f"{period[:4]}-{period[4:6]}-{period[6:8]}",
    )
    try:
        _launch_detached_worker(
            "src.services.financial_sync_worker",
            job_id,
            period,
            ",".join(active_codes),
        )
    except Exception as exc:
        _release_sync_job_claim(job_id, "财报后台任务启动失败")
        _set_financial_state(
            status="failed",
            error=str(exc)[:300],
            message="财报后台任务启动失败",
            finished_at=_utc_now_iso(),
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "sync_worker_start_failed", "message": "财报后台任务启动失败"},
        ) from exc
    return {
        "success": True,
        "message": f"最新财报同步已启动，将覆盖 {len(active_codes)} 只活跃股票",
        "status": "running",
        "period": period,
        "total": len(active_codes),
    }


@router.get("/sync/financial/status", summary="Get financial sync status")
def get_stock_financial_sync_status():
    return _financial_state_with_db_fallback(_get_financial_state_copy())


# 注入状态对象给 _financials_sync（必须在 _utc_now_iso 等所有 helper 定义后）
from api.v1.endpoints.stocks import _financials_sync  # noqa: E402

_financials_sync.attach_state(
    state=_financial_sync_state,
    lock=_financial_sync_lock,
    set_state=_set_financial_state,
    initial_state=_initial_state,
    utc_now_iso=_utc_now_iso,
)
