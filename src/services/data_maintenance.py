"""Automatic data-readiness checks shared by Agent tools and API endpoints."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from src.services.stock_universe_service import sync_stock_universe
from src.storage import DataMaintenanceJob, DatabaseManager, StockDaily, StockMeta

_universe_lock = threading.Lock()
_RUNNING_JOB_STALE_AFTER = timedelta(seconds=60)


def _public_maintenance_warning(error: str | None) -> str:
    """Keep operational repair details out of Agent-facing tool payloads."""
    detail = str(error or "").strip()
    if not detail:
        return "股票基础库自动更新未完成，当前使用本地缓存"
    if any(token in detail for token in ("安全要求", "覆盖率", "部分股票", "active 状态")):
        return "股票基础库自动更新未完成，当前使用本地缓存；不完整的上游结果未写入数据库"
    return f"股票基础库自动更新未完成，当前使用本地缓存：{detail}"


def _universe_snapshot() -> dict[str, Any]:
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        total, last_sync = (
            session.query(func.count(StockMeta.id), func.min(StockMeta.last_sync_at))
            .filter(StockMeta.status == "active")
            .one()
        )
    stale = not last_sync or last_sync.date() < date.today()
    return {
        "total": int(total or 0),
        "data_time": last_sync.isoformat() if last_sync else None,
        "is_stale": stale,
    }


def _claim_job(dataset: str, target_data_time: str, trigger: str) -> tuple[str, bool]:
    """Claim the singleton maintenance job and fence stale workers.

    A takeover must get a new id.  Reusing the old primary key lets a worker
    from the previous attempt continue writing progress after a stale lease
    has been reclaimed by another process.
    """
    job_id = str(uuid.uuid4())
    now = datetime.now()
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        existing = (
            session.query(DataMaintenanceJob)
            .filter(
                DataMaintenanceJob.dataset == dataset,
                DataMaintenanceJob.scope_key == "all",
                DataMaintenanceJob.target_data_time == target_data_time,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.status == "running":
                heartbeat_at = existing.updated_at or existing.started_at or existing.created_at
                if heartbeat_at and now - heartbeat_at < _RUNNING_JOB_STALE_AFTER:
                    return existing.id, False
            recently_failed = (
                existing.status == "failed"
                and existing.updated_at is not None
                and now - existing.updated_at < timedelta(minutes=10)
            )
            if recently_failed:
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
                    "id": job_id,
                    "status": "running",
                    "trigger": trigger,
                    "progress": 0,
                    "total": 0,
                    "message": "正在接管已中断的股票基础库维护任务",
                    "error": None,
                    "finished_at": None,
                    "started_at": now,
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
                        DataMaintenanceJob.scope_key == "all",
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
                    scope_key="all",
                    target_data_time=target_data_time,
                    trigger=trigger,
                    status="running",
                    started_at=now,
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
                    DataMaintenanceJob.scope_key == "all",
                    DataMaintenanceJob.target_data_time == target_data_time,
                )
                .one()
            )
            return existing.id, False


def _wait_for_job(job_id: str, timeout_seconds: float = 60.0) -> DataMaintenanceJob | None:
    deadline = time.monotonic() + timeout_seconds
    db = DatabaseManager.get_instance()
    while time.monotonic() < deadline:
        with db.get_session() as session:
            job = session.query(DataMaintenanceJob).filter(DataMaintenanceJob.id == job_id).one_or_none()
            if job is None:
                return None
            if job.status in {"success", "partial", "failed"}:
                session.expunge(job)
                return job
        time.sleep(0.25)
    return None


def _get_job(job_id: str) -> DataMaintenanceJob | None:
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        job = session.query(DataMaintenanceJob).filter(DataMaintenanceJob.id == job_id).one_or_none()
        if job is not None:
            session.expunge(job)
        return job


def _update_job(job_id: str, **values: Any) -> None:
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        session.query(DataMaintenanceJob).filter(DataMaintenanceJob.id == job_id).update(values)
        session.commit()


def run_stock_universe_maintenance_job(
    job_id: str,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Execute one already-claimed refresh and persist its terminal state."""

    def progress(processed: int, total: int, message: str) -> None:
        _update_job(job_id, progress=processed, total=total, message=message)
        if on_progress:
            on_progress(processed, total, message)

    try:
        result = sync_stock_universe(progress)
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            error=str(exc),
            message="股票基础库自动更新失败",
            finished_at=datetime.now(),
        )
        raise
    _update_job(
        job_id,
        status="success",
        progress=result["total"],
        total=result["total"],
        message="股票基础库已自动更新",
        error=None,
        finished_at=datetime.now(),
    )
    return result


def _launch_stock_universe_worker(job_id: str) -> None:
    """Detach maintenance from the short-lived isolated tool process."""
    subprocess.Popen(
        [sys.executable, "-m", "src.services.data_maintenance_worker", job_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )


def ensure_stock_universe(
    *,
    trigger: str = "agent",
    force: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Ensure stock data readiness without making Agent reads wait on the network.

    Normal Agent calls return an existing local universe immediately and refresh
    stale data in a detached worker. ``force=True`` remains synchronous for the
    legacy explicit-sync endpoint.
    """
    before = _universe_snapshot()
    if not force and before["total"] > 0 and not before["is_stale"]:
        return {**before, "refreshed": False, "maintenance_status": "ready"}

    with _universe_lock:
        # Another caller may have completed the refresh while this caller waited.
        current = _universe_snapshot()
        if not force and current["total"] > 0 and not current["is_stale"]:
            return {**current, "refreshed": False, "maintenance_status": "reused"}

        # ``target_data_time`` is VARCHAR(32); keep forced attempts unique
        # without overflowing the schema on PostgreSQL.
        target = (
            date.today().isoformat()
            if not force
            else f"{date.today().isoformat()}:{uuid.uuid4().hex[:16]}"
        )
        job_id, claimed = _claim_job("stock_universe", target, trigger)

        if not force:
            job = _get_job(job_id)
            if claimed:
                try:
                    _launch_stock_universe_worker(job_id)
                    job_status = "running"
                except Exception as exc:
                    _update_job(
                        job_id,
                        status="failed",
                        error=str(exc),
                        message="股票基础库后台任务启动失败",
                        finished_at=datetime.now(),
                    )
                    job_status = "failed"
                    job = _get_job(job_id)
            else:
                job_status = job.status if job is not None else "unknown"

            refreshing = job_status == "running"
            if refreshing:
                warning = "股票基础库正在后台更新，当前搜索使用本地缓存"
                maintenance_status = "refreshing"
            else:
                warning = _public_maintenance_warning(job.error if job is not None else None)
                maintenance_status = "stale_fallback"
            return {
                **current,
                "refreshed": False,
                "maintenance_status": maintenance_status,
                "warning": warning,
                "job_id": job_id,
            }

        if not claimed:
            job = _wait_for_job(job_id)
            after_wait = _universe_snapshot()
            if job is not None and job.status == "success":
                return {
                    **after_wait,
                    "refreshed": False,
                    "maintenance_status": "reused",
                    "job_id": job_id,
                }
            if after_wait["total"] > 0:
                return {
                    **after_wait,
                    "refreshed": False,
                    "maintenance_status": "stale_fallback",
                    "warning": _public_maintenance_warning(
                        job.error if job is not None else "股票基础库维护任务等待超时"
                    ),
                    "job_id": job_id,
                }
            raise RuntimeError(job.error if job is not None else "股票基础库维护任务等待超时")

        try:
            result = run_stock_universe_maintenance_job(job_id, on_progress=on_progress)
        except Exception as exc:
            if current["total"] == 0:
                raise
            return {
                **current,
                "refreshed": False,
                "maintenance_status": "stale_fallback",
                "warning": _public_maintenance_warning(str(exc)),
                "job_id": job_id,
            }

        after = _universe_snapshot()
        return {
            **after,
            "refreshed": True,
            "maintenance_status": "success",
            "job_id": job_id,
            "changes": result,
        }


def get_data_health() -> dict[str, Any]:
    """Return stock-universe, K-line and financial coverage plus recent jobs."""
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        universe = _universe_snapshot()
        kline_codes, latest_kline = (
            session.query(func.count(func.distinct(StockDaily.code)), func.max(StockDaily.date))
            .join(StockMeta, StockMeta.code == StockDaily.code)
            .filter(StockMeta.status == "active")
            .one()
        )
        financial_codes, latest_report, latest_financial_fetch = (
            session.query(
                func.count(StockMeta.id),
                func.max(StockMeta.report_date),
                func.max(StockMeta.financial_fetched_at),
            )
            .filter(
                StockMeta.status == "active",
                StockMeta.financial_fetched_at.isnot(None),
            )
            .one()
        )
        jobs = session.query(DataMaintenanceJob).order_by(DataMaintenanceJob.created_at.desc()).limit(10).all()
    total = universe["total"]
    return {
        "stock_universe": universe,
        "kline": {
            "covered_stocks": int(kline_codes or 0),
            "missing_stocks": max(0, total - int(kline_codes or 0)),
            "coverage_ratio": round((int(kline_codes or 0) / total), 4) if total else 0,
            "latest_trade_date": latest_kline.isoformat() if latest_kline else None,
        },
        "financials": {
            "covered_stocks": int(financial_codes or 0),
            "missing_stocks": max(0, total - int(financial_codes or 0)),
            "coverage_ratio": round((int(financial_codes or 0) / total), 4) if total else 0,
            "latest_report_period": latest_report,
            "last_fetched_at": latest_financial_fetch.isoformat() if latest_financial_fetch else None,
        },
        "recent_jobs": [job.to_dict() for job in jobs],
    }


__all__ = [
    "ensure_stock_universe",
    "get_data_health",
    "run_stock_universe_maintenance_job",
]
