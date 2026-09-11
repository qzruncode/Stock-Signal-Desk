# -*- coding: utf-8 -*-
"""Batch run helper functions — extracted from batch.py for module decomposition."""

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.batch_runner import BATCH_REPORTS_DIR, BatchRunControl, BatchRunState, _write_aggregated_report
from src.config import get_config
from src.storage import DataMaintenanceJob, DatabaseManager

logger = logging.getLogger(__name__)

_BATCH_LEASE_DATASET = "batch_analysis"
_BATCH_LEASE_SCOPE = "all"
_BATCH_LEASE_TARGET = "active"
_BATCH_LEASE_STALE_AFTER = timedelta(seconds=90)

# In-memory progress tracker (shared with run.py)
_running_batch: Optional[dict] = None
_running_lock = threading.Lock()


def _regenerate_batch_report_for_run(run: dict) -> tuple[str, BatchRunState]:
    results = _parse_results_json(run.get("results_json"))
    if not results:
        raise HTTPException(status_code=400, detail="该跑批没有可用于汇总的单股结果")
    if run.get("analysis_mode") == "buy_criteria":
        raise HTTPException(
            status_code=410,
            detail="旧的买入判断跑批报告已停止重新生成",
        )

    run_id = run.get("run_id") or ""
    state = BatchRunState(
        run_id,
        total=int(run.get("stock_count") or len(results)),
        existing_results=results,
    )
    started_at = _parse_started_at(run.get("started_at")) or datetime.now()
    report_path = _write_aggregated_report(
        run_id,
        state,
        run.get("template_name") or "-",
        started_at,
    )
    return report_path, state


def _build_partial_report_from_run(run: dict) -> str:
    try:
        results = json.loads(run.get("results_json") or "{}")
    except Exception:
        return ""
    if not isinstance(results, dict) or not results:
        return ""

    lines = [
        "# 批量分析报告（部分结果）",
        "",
        f"- **触发时间**: {run.get('started_at') or '-'}",
        f"- **分析模板**: {run.get('template_name') or '-'}",
        f"- **股票数量**: {run.get('stock_count') or 0}",
        f"- **成功**: {run.get('success_count') or 0} / **失败**: {run.get('fail_count') or 0}",
        "",
        "---",
        "",
    ]

    for code, result in results.items():
        if code == "__all__" or not isinstance(result, dict):
            continue
        lines.append(f"## {code}")
        lines.append("")
        if result.get("success"):
            lines.append(f"> 模型: {result.get('model') or '-'}")
            lines.append("")
            lines.append(result.get("text") or "")
        else:
            lines.append(f"> 分析失败: {result.get('text') or '未知错误'}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def resume_incomplete_batches_on_startup() -> bool:
    """Resume the latest interrupted batch that has a persisted stock list."""
    if _is_batch_running():
        return False

    db = DatabaseManager.get_instance()
    runs = db.get_incomplete_batch_runs(limit=5)
    for run in runs:
        stock_codes = _resolve_auto_resume_stock_codes(run)
        if not stock_codes:
            continue
        existing_results = _filter_results_for_stock_codes(
            _parse_results_json(run.get("results_json")),
            stock_codes,
        )
        if len(existing_results) == 0 or len(existing_results) >= len(stock_codes):
            continue

        if run.get("analysis_mode") == "buy_criteria":
            logger.warning("Skip auto-resume for removed buy-criteria batch %s", run.get("run_id"))
            continue

        from src.prompt_templates import get_prompt_template_store

        store = get_prompt_template_store()
        template = store.get(run.get("template_id") or "")
        if template is None:
            logger.warning("Cannot auto-resume batch %s: template missing", run.get("run_id"))
            continue
        template_name = template["name"]
        system_prompt = template["content"]

        from src.batch_runner import BatchRunner

        runner = BatchRunner()
        control = BatchRunControl()
        run_id = run["run_id"]
        lease_id, claimed = _claim_batch_execution(
            trigger="startup",
            total=len(stock_codes),
        )
        if not claimed:
            continue
        try:
            _start_batch_thread(
                lambda on_progress, run_id=run_id, stock_codes=stock_codes, existing_results=existing_results, system_prompt=system_prompt, template_name=template_name, run=run: runner.resume(
                    run_id=run_id,
                    stock_codes=stock_codes,
                    system_prompt=system_prompt,
                    template_name=template_name,
                    started_at=_parse_started_at(run.get("started_at")),
                    existing_results=existing_results,
                    control=control,
                    on_progress=on_progress,
                ),
                control=control,
                lease_id=lease_id,
            )
        except Exception as exc:
            _persist_batch_lease(lease_id, "failed", message="启动自动续跑失败", error=str(exc))
            logger.exception("Failed to auto-resume batch run: %s", run_id)
            continue
        logger.info("Auto-resumed interrupted batch run: run_id=%s", run_id)
        return True
    return False


def _is_batch_running() -> bool:
    global _running_batch
    if _running_batch and _running_batch.get("running"):
        return True
    try:
        return _get_active_batch_lease() is not None
    except Exception:
        logger.exception("Failed to inspect persisted batch lease")
        return False


def _get_active_batch_lease() -> Optional[dict]:
    """Return the live cross-process batch lease, if one exists."""
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        lease = (
            session.query(DataMaintenanceJob)
            .filter(
                DataMaintenanceJob.dataset == _BATCH_LEASE_DATASET,
                DataMaintenanceJob.scope_key == _BATCH_LEASE_SCOPE,
                DataMaintenanceJob.target_data_time == _BATCH_LEASE_TARGET,
            )
            .one_or_none()
        )
        if lease is None or lease.status not in {"running", "queued"}:
            return None
        heartbeat_at = lease.updated_at or lease.started_at or lease.created_at
        if heartbeat_at and datetime.now() - heartbeat_at >= _BATCH_LEASE_STALE_AFTER:
            return None
        return {
            "id": lease.id,
            "status": lease.status,
            "updated_at": heartbeat_at.isoformat() if heartbeat_at else None,
        }


def _batch_lease_is_current(lease_id: Optional[str]) -> bool:
    """Check that a worker still owns the durable batch lease."""
    if not lease_id:
        return True
    try:
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            lease = session.query(DataMaintenanceJob).filter(DataMaintenanceJob.id == lease_id).one_or_none()
            return lease is not None and lease.status in {"running", "queued"}
    except Exception:
        # A transient database outage must not make an otherwise live worker
        # self-cancel; the normal lease heartbeat will report the outage.
        logger.warning("Failed to verify batch lease ownership: %s", lease_id, exc_info=True)
        return True


def _claim_batch_execution(*, trigger: str, total: int = 0) -> tuple[str, bool]:
    """Atomically claim the singleton batch execution slot across processes."""
    lease_id = uuid.uuid4().hex
    now = datetime.now()
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        existing = (
            session.query(DataMaintenanceJob)
            .filter(
                DataMaintenanceJob.dataset == _BATCH_LEASE_DATASET,
                DataMaintenanceJob.scope_key == _BATCH_LEASE_SCOPE,
                DataMaintenanceJob.target_data_time == _BATCH_LEASE_TARGET,
            )
            .one_or_none()
        )
        if existing is not None:
            heartbeat_at = existing.updated_at or existing.started_at or existing.created_at
            if (
                existing.status in {"running", "queued"}
                and heartbeat_at
                and now - heartbeat_at < _BATCH_LEASE_STALE_AFTER
            ):
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
                    # Rotate the lease id on stale takeover so an old worker
                    # cannot keep heartbeating the newly claimed execution.
                    "id": lease_id,
                    "status": "running",
                    "trigger": trigger,
                    "progress": 0,
                    "total": max(0, int(total)),
                    "message": "跑批任务已启动",
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
                        DataMaintenanceJob.dataset == _BATCH_LEASE_DATASET,
                        DataMaintenanceJob.scope_key == _BATCH_LEASE_SCOPE,
                        DataMaintenanceJob.target_data_time == _BATCH_LEASE_TARGET,
                    )
                    .one_or_none()
                )
                return (current.id, False) if current is not None else (lease_id, False)
            session.commit()
            return lease_id, True
        try:
            session.add(
                DataMaintenanceJob(
                    id=lease_id,
                    dataset=_BATCH_LEASE_DATASET,
                    scope_key=_BATCH_LEASE_SCOPE,
                    target_data_time=_BATCH_LEASE_TARGET,
                    trigger=trigger,
                    status="running",
                    total=max(0, int(total)),
                    message="跑批任务已启动",
                    started_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            return lease_id, True
        except IntegrityError:
            session.rollback()
            existing = (
                session.query(DataMaintenanceJob)
                .filter(
                    DataMaintenanceJob.dataset == _BATCH_LEASE_DATASET,
                    DataMaintenanceJob.scope_key == _BATCH_LEASE_SCOPE,
                    DataMaintenanceJob.target_data_time == _BATCH_LEASE_TARGET,
                )
                .one_or_none()
            )
            return (existing.id, False) if existing is not None else (lease_id, False)


def _persist_batch_lease(
    lease_id: Optional[str],
    status: str,
    *,
    message: str = "",
    error: Optional[str] = None,
    progress: Optional[int] = None,
    total: Optional[int] = None,
) -> None:
    if not lease_id:
        return
    terminal = status in {"success", "partial", "failed"}
    try:
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            values = {
                "status": status,
                "message": message,
                "error": error,
                "updated_at": datetime.now(),
            }
            if progress is not None:
                values["progress"] = max(0, int(progress))
            if total is not None:
                values["total"] = max(0, int(total))
            if terminal:
                values["finished_at"] = datetime.now()
            session.query(DataMaintenanceJob).filter(DataMaintenanceJob.id == lease_id).update(values)
            session.commit()
    except Exception:
        logger.warning("Failed to persist batch lease state: %s", lease_id, exc_info=True)


def _get_running_batch_snapshot() -> Optional[dict]:
    """Return a detached snapshot of the process-local batch state."""
    with _running_lock:
        if _running_batch is None:
            return None
        snapshot = dict(_running_batch)
        state = snapshot.get("state")
        if isinstance(state, dict):
            snapshot["state"] = dict(state)
        return snapshot


def _build_batch_state_from_record(run: dict) -> dict:
    """Reconstruct the public progress shape from a persisted batch record."""
    results = _parse_results_json(run.get("results_json"))
    total = max(int(run.get("stock_count") or 0), len(results))
    success = max(int(run.get("success_count") or 0), sum(1 for item in results.values() if item.get("success")))
    failed = max(int(run.get("fail_count") or 0), sum(1 for item in results.values() if not item.get("success")))
    completed = min(total, success + failed) if total else success + failed
    status = run.get("status") or ("completed" if run.get("completed_at") else "running")
    messages = {
        "running": "后台跑批中（已恢复持久化进度）",
        "paused": "跑批已暂停（持久化状态）",
        "stopping": "跑批正在终止（持久化状态）",
        "stopped": "跑批已终止",
        "completed": "跑批已完成",
    }
    return {
        "run_id": run.get("run_id"),
        "total": total,
        "completed": completed,
        "success": min(success, total) if total else success,
        "failed": min(failed, total) if total else failed,
        "current_stock": None,
        "current_message": messages.get(status, f"跑批状态：{status}"),
        "status": status,
        "paused": status == "paused",
        "stopping": status == "stopping",
        "active_stocks": [],
        "results": results,
    }


def _is_persisted_batch_active(run: dict) -> bool:
    return not run.get("completed_at") and run.get("status") in {"running", "paused", "stopping"}


def _mark_batch_stopped():
    global _running_batch
    with _running_lock:
        if _running_batch is not None:
            _running_batch["running"] = False


def _get_running_control() -> Optional[BatchRunControl]:
    if not _running_batch or not _running_batch.get("running"):
        return None
    control = _running_batch.get("control")
    if isinstance(control, BatchRunControl):
        return control
    return None


def _set_running_status(status: str, message: str):
    if not _running_batch:
        return
    state = _running_batch.get("state")
    if isinstance(state, dict):
        state["status"] = status
        state["paused"] = status == "paused"
        state["stopping"] = status == "stopping"
        state["current_message"] = message


def _persist_current_status(status: str):
    if not _running_batch:
        return
    state = _running_batch.get("state") or {}
    run_id = state.get("run_id")
    if not run_id:
        return
    try:
        DatabaseManager.get_instance().update_batch_run_status(run_id, status)
        lease_id = _running_batch.get("lease_id")
        if lease_id:
            _persist_batch_lease(
                lease_id,
                "running",
                message=state.get("current_message") or status,
                progress=state.get("completed"),
                total=state.get("total"),
            )
    except Exception:
        logger.exception("Failed to persist batch status: %s", status)


def _start_batch_thread(
    run_factory,
    control: Optional[BatchRunControl] = None,
    lease_id: Optional[str] = None,
):
    global _running_batch
    with _running_lock:
        if _running_batch and _running_batch.get("running"):
            raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")
        _running_batch = {
            "running": True,
            "state": None,
            "control": control,
            "lease_id": lease_id,
        }

    def on_progress(state):
        if lease_id and control is not None and not _batch_lease_is_current(lease_id):
            control.stop()
        if _running_batch is not None:
            _running_batch["state"] = state.to_dict()
        if lease_id:
            now_monotonic = time.monotonic()
            last_heartbeat = getattr(on_progress, "_last_heartbeat", 0.0)
            if now_monotonic - last_heartbeat >= 10.0 or state.status in {"paused", "stopping"}:
                _persist_batch_lease(
                    lease_id,
                    "running",
                    message=state.current_message,
                    progress=state.completed,
                    total=state.total,
                )
                on_progress._last_heartbeat = now_monotonic

    def _run():
        global _running_batch
        try:
            if lease_id and not _batch_lease_is_current(lease_id):
                raise RuntimeError("批处理租约已被其他进程接管")
            state = run_factory(on_progress)
            if _running_batch is not None:
                _running_batch["state"] = state.to_dict()
            final_status = getattr(state, "status", "completed")
            lease_status = "success" if final_status == "completed" else "partial"
            _persist_batch_lease(
                lease_id,
                lease_status,
                message=(state.current_message if hasattr(state, "current_message") else "跑批结束"),
            )
        except Exception as exc:
            logger.exception("Batch worker failed")
            _persist_batch_lease(lease_id, "failed", message="跑批执行异常", error=str(exc))
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1.0)
            _mark_batch_stopped()

    heartbeat_stop = threading.Event()

    def _sync_control_from_persisted_state(snapshot: dict) -> None:
        control_state = snapshot.get("control")
        state = snapshot.get("state") or {}
        run_id = state.get("run_id")
        if not isinstance(control_state, BatchRunControl) or not run_id:
            return
        try:
            persisted = DatabaseManager.get_instance().get_batch_run(run_id)
            status = persisted.get("status") if persisted else None
            if status == "paused" and not control_state.paused:
                control_state.pause()
            elif status == "running" and control_state.paused:
                control_state.resume()
            elif status in {"stopping", "stopped"} and not control_state.stopping:
                control_state.stop()
        except Exception:
            logger.debug("Failed to reconcile persisted batch control", exc_info=True)

    def _heartbeat() -> None:
        last_lease_persist = 0.0
        while not heartbeat_stop.wait(timeout=2.0):
            if lease_id and control is not None and not _batch_lease_is_current(lease_id):
                control.stop()
            snapshot = _get_running_batch_snapshot() or {}
            _sync_control_from_persisted_state(snapshot)
            state = snapshot.get("state") or {}
            now_monotonic = time.monotonic()
            if now_monotonic - last_lease_persist >= 20.0:
                _persist_batch_lease(
                    lease_id,
                    "running",
                    message=state.get("current_message") or "跑批执行中",
                    progress=state.get("completed"),
                    total=state.get("total"),
                )
                last_lease_persist = now_monotonic

    heartbeat_thread = None
    if lease_id:
        heartbeat_thread = threading.Thread(
            target=_heartbeat,
            name="batch-lease-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

    thread = threading.Thread(target=_run, daemon=True)
    try:
        thread.start()
    except Exception as exc:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
        _mark_batch_stopped()
        _persist_batch_lease(lease_id, "failed", message="跑批线程启动失败", error=str(exc))
        raise


def _parse_results_json(raw: Optional[str]) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(code): result for code, result in parsed.items() if code != "__all__" and isinstance(result, dict)}


def _parse_stock_codes_json(raw: Optional[str]) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(code).strip() for code in parsed if str(code).strip()]


def _resolve_resume_stock_codes(run: dict, fallback_stock_codes: list[str]) -> list[str]:
    stored_codes = _parse_stock_codes_json(run.get("stock_codes_json"))
    if stored_codes:
        return stored_codes
    return [code.strip() for code in fallback_stock_codes if code.strip()]


def _resolve_auto_resume_stock_codes(run: dict) -> list[str]:
    existing_result_count = len(_parse_results_json(run.get("results_json")))
    stock_count = int(run.get("stock_count") or 0)
    if existing_result_count <= 0 or existing_result_count >= stock_count:
        return []

    stored_codes = _parse_stock_codes_json(run.get("stock_codes_json"))
    if stored_codes:
        return stored_codes

    config_codes = [code.strip() for code in get_config().stock_list if code.strip()]
    if config_codes and len(config_codes) == stock_count:
        return config_codes
    return []


def _filter_results_for_stock_codes(results: dict, stock_codes: list[str]) -> dict:
    stock_code_set = set(stock_codes)
    return {code: result for code, result in results.items() if code in stock_code_set}


def _parse_started_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _delete_batch_report_file(report_path: Optional[str]):
    if not report_path:
        return
    candidates = [Path(report_path), BATCH_REPORTS_DIR / Path(report_path).name]
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            logger.warning("Failed to delete batch report file: %s", path, exc_info=True)
