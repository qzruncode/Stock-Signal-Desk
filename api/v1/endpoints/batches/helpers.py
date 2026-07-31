# -*- coding: utf-8 -*-
"""Batch run helper functions — extracted from batch.py for module decomposition."""

import json
import logging
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import HTTPException

from src.batch_runner import BATCH_REPORTS_DIR, BatchRunControl, BatchRunState, _write_aggregated_report
from src.config import get_config
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# In-memory progress tracker (shared with run.py)
_running_batch: Optional[dict] = None
_running_lock = threading.Lock()


def _regenerate_batch_report_for_run(run: dict) -> tuple[str, BatchRunState]:
    results = _parse_results_json(run.get("results_json"))
    if not results:
        raise HTTPException(status_code=400, detail="该跑批没有可用于汇总的单股结果")

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
        analysis_mode=run.get("analysis_mode") or "template",
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

        analysis_mode = run.get("analysis_mode") or "template"
        if analysis_mode == "buy_criteria":
            template_name = run.get("template_name") or "买入判断筛选"
            system_prompt = ""
        else:
            from src.prompt_templates import get_prompt_template_store

            store = get_prompt_template_store()
            template = store.get(run.get("template_id") or "")
            if template is None:
                logger.warning("Cannot auto-resume batch %s: template missing", run.get("run_id"))
                continue
            template_name = template["name"]
            system_prompt = template["content"]

        from src.batch_runner import BatchRunner

        runner = BatchRunner(max_concurrent=1 if analysis_mode == "buy_criteria" else None)
        control = BatchRunControl()
        run_id = run["run_id"]
        _start_batch_thread(
            lambda on_progress, run_id=run_id, stock_codes=stock_codes, existing_results=existing_results, system_prompt=system_prompt, template_name=template_name, analysis_mode=analysis_mode, run=run: runner.resume(
                run_id=run_id,
                stock_codes=stock_codes,
                system_prompt=system_prompt,
                template_name=template_name,
                analysis_mode=analysis_mode,
                started_at=_parse_started_at(run.get("started_at")),
                existing_results=existing_results,
                control=control,
                on_progress=on_progress,
            ),
            control=control,
        )
        logger.info("Auto-resumed interrupted batch run: run_id=%s", run_id)
        return True
    return False


def _is_batch_running() -> bool:
    global _running_batch
    return bool(_running_batch and _running_batch.get("running"))


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
    except Exception:
        logger.exception("Failed to persist batch status: %s", status)


def _start_batch_thread(run_factory, control: Optional[BatchRunControl] = None):
    global _running_batch
    with _running_lock:
        if _running_batch and _running_batch.get("running"):
            raise HTTPException(status_code=409, detail="已有跑批正在执行，请等待完成")
        _running_batch = {"running": True, "state": None, "control": control}

    def on_progress(state):
        if _running_batch is not None:
            _running_batch["state"] = state.to_dict()

    def _run():
        global _running_batch
        try:
            state = run_factory(on_progress)
            if _running_batch is not None:
                _running_batch["state"] = state.to_dict()
        finally:
            _mark_batch_stopped()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


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
