"""Function group 2 extracted from src/batch_runner.py."""

from __future__ import annotations

from src.batch_runner import (
    json,
    logging,
    os,
    re,
    threading,
    time,
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    wait,
    datetime,
    timezone,
    Path,
    Callable,
    Dict,
    List,
    Optional,
    call_ai_for_stock,
    GeminiAnalyzer,
    get_analyzer,
    BatchRun,
    DatabaseManager,
    persist_llm_usage,
    logger,
    BATCH_REPORTS_DIR,
    DEFAULT_MAX_CONCURRENT,
    MAX_CONCURRENT_LIMIT,
    BATCH_DECISION_SCHEMA_MARKER,
    BATCH_DECISION_SCHEMA_INSTRUCTION,
    BatchRunControl,
    BatchRunState,
    BatchRunner,
    _CRITERIA_NUM_LABELS,
 )

__all__ = ['_build_criteria_notification_content', '_build_template_notification_content', '_save_batch_run_progress', '_save_batch_run_start', '_save_batch_run_resume_start', '_save_batch_run_end']

def _build_criteria_notification_content(
    state: BatchRunState,
    template_name: str,
    report_path: str,
) -> str:
    """Build buy-criteria batch completion notification content."""
    from datetime import datetime

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_name = Path(report_path).name

    result_items = _get_result_items(state)
    failed_items = [(code, result) for code, result in result_items if not result.get("success")]
    summary_items = _get_stock_decision_summaries(result_items)
    passed_items = [item for item in summary_items if item["decision"] == "buy"]
    rejected_items = [item for item in summary_items if item["decision"] != "buy"]
    success_rate = (state.success / state.total * 100) if state.total else 0

    lines = [
        "## 买入判断筛选汇总",
        "",
        f"> 筛选方式: **八维布尔买入判断**",
        f"> 时间: {now}",
        f"> 完成: **{state.completed}/{state.total}**",
        f"> 分析成功: **{state.success}** | 分析失败: **{state.failed}** | 分析成功率: **{success_rate:.1f}%**",
        f"> 筛选通过(8/8): **{len(passed_items)}** | 未通过: **{len(rejected_items)}**",
        f"> 报告: `{report_name}`",
        "",
        "### 筛选通过股票",
        "",
    ]
    if passed_items:
        lines.append("| 股票 | 结论 | 摘要 |")
        lines.append("| --- | --- | --- |")
        for item in passed_items:
            lines.append(f"| {item['code']} | {item['label']} | {item['reason']} |")
    else:
        lines.append("本次筛选没有八维全部通过的股票。")
    lines.append("")

    if failed_items:
        lines.append("### 分析失败")
        lines.append("")
        for code, result in failed_items[:20]:
            lines.append(f"- **{code}**: {_one_line(result.get('text') or '未知错误', limit=80)}")
        if len(failed_items) > 20:
            lines.append(f"- 另有 {len(failed_items) - 20} 项失败，请查看完整报告。")
        lines.append("")

    lines.append(f"*买入判断筛选完成于 {now}*")
    return "\n".join(lines)

def _build_template_notification_content(
    state: BatchRunState,
    template_name: str,
    report_path: str,
) -> str:
    """Build template-mode batch completion notification markdown content."""
    from datetime import datetime

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_name = Path(report_path).name

    result_items = _get_result_items(state)
    failed_items = [(code, result) for code, result in result_items if not result.get("success")]
    summary_items = _get_stock_decision_summaries(result_items)
    passed_items = [item for item in summary_items if item["decision"] == "buy"]
    watch_items = [item for item in summary_items if item["decision"] == "watch"]
    rejected_items = [item for item in summary_items if item["decision"] == "reject"]
    unknown_items = [item for item in summary_items if item["decision"] == "unknown"]
    success_rate = (state.success / state.total * 100) if state.total else 0

    lines = [
        "## 跑批筛选汇总",
        "",
        f"> 模板: **{template_name}**",
        f"> 时间: {now}",
        f"> 完成: **{state.completed}/{state.total}**",
        f"> 分析成功: **{state.success}** | 分析失败: **{state.failed}** | 分析成功率: **{success_rate:.1f}%**",
        f"> 筛选通过: **{len(passed_items)}**",
        f"> 观察: **{len(watch_items)}** | 排除: **{len(rejected_items)}** | 待确认: **{len(unknown_items)}**",
        f"> 报告: `{report_name}`",
        "",
    ]

    lines.append("### 统计概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | ---: |")
    lines.append(f"| 股票数 | {state.total} |")
    lines.append(f"| 已完成 | {state.completed} |")
    lines.append(f"| 分析成功 | {state.success} |")
    lines.append(f"| 分析失败 | {state.failed} |")
    lines.append(f"| 筛选通过 | {len(passed_items)} |")
    lines.append(f"| 观察 | {len(watch_items)} |")
    lines.append(f"| 排除 | {len(rejected_items)} |")
    lines.append(f"| 待确认 | {len(unknown_items)} |")
    lines.append(f"| 分析成功率 | {success_rate:.1f}% |")
    lines.append("")

    lines.append("### 筛选通过股票")
    lines.append("")
    if passed_items:
        lines.append("| 股票 | 结论 | 摘要理由 |")
        lines.append("| --- | --- | --- |")
        for item in passed_items:
            lines.append(f"| {item['code']} | {item['label']} | {item['reason']} |")
    else:
        lines.append("本次跑批没有识别到明确筛选通过的股票。")
    lines.append("")

    if unknown_items:
        lines.append("### 待人工确认")
        lines.append("")
        lines.append("| 股票 | 识别到的结论 | 摘要理由 |")
        lines.append("| --- | --- | --- |")
        for item in unknown_items:
            lines.append(f"| {item['code']} | {item['label']} | {item['reason']} |")
        lines.append("")

    if failed_items:
        lines.append("### 分析失败")
        lines.append("")
        for code, result in failed_items[:20]:
            lines.append(f"- **{code}**: {_one_line(result.get('text') or '未知错误', limit=80)}")
        if len(failed_items) > 20:
            lines.append(f"- 另有 {len(failed_items) - 20} 项失败，请查看完整报告。")
        lines.append("")

    lines.append(f"*批量分析完成于 {now}*")
    return "\n".join(lines)

def _save_batch_run_progress(run_id: str, state: BatchRunState, status: str = "running"):
    """Persist completed stock results during a running batch.

    Batch jobs can be long-running. Persisting each completed stock keeps
    already-paid AI output recoverable if the browser or backend process restarts
    before the final aggregated report is written.
    """
    try:
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            record = session.query(BatchRun).filter_by(run_id=run_id).first()
            if record:
                record.success_count = state.success
                record.fail_count = state.failed
                record.results_json = json.dumps(state.results, ensure_ascii=False)
                # A pause/stop request may arrive from another API worker while
                # an in-flight stock is finishing. Do not let that progress
                # write erase the durable control command.
                next_status = (
                    getattr(record, "status", status)
                    if status == "running" and getattr(record, "status", None) in {"paused", "stopping", "stopped"}
                    else status
                )
                record.status = next_status
                session.commit()
    except Exception:
        logger.exception("Failed to save batch run progress record")

def _save_batch_run_start(
    run_id: str,
    triggered_by: str,
    template_id: str,
    template_name: str,
    stock_codes: List[str],
    analysis_mode: str = "template",
):
    try:
        db = DatabaseManager.get_instance()
        record = BatchRun(
            run_id=run_id,
            triggered_by=triggered_by,
            template_id=template_id,
            template_name=template_name,
            stock_count=len(stock_codes),
            success_count=0,
            fail_count=0,
            started_at=datetime.now(timezone.utc),
            report_path="",
            results_json="[]",
            stock_codes_json=json.dumps(stock_codes, ensure_ascii=False),
            status="running",
            analysis_mode=analysis_mode,
        )
        with db.get_session() as session:
            session.add(record)
            session.commit()
    except Exception:
        logger.exception("Failed to save batch run start record")

def _save_batch_run_resume_start(
    run_id: str,
    stock_codes: List[str],
    existing_results: Dict[str, dict],
):
    try:
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            record = session.query(BatchRun).filter_by(run_id=run_id).first()
            if record:
                record.stock_count = len(stock_codes)
                record.success_count = sum(1 for result in existing_results.values() if result.get("success"))
                record.fail_count = sum(1 for result in existing_results.values() if not result.get("success"))
                record.completed_at = None
                record.report_path = ""
                record.results_json = json.dumps(existing_results, ensure_ascii=False)
                record.stock_codes_json = json.dumps(stock_codes, ensure_ascii=False)
                record.status = "running"
                session.commit()
    except Exception:
        logger.exception("Failed to mark batch run as resumed")

def _save_batch_run_end(
    run_id: str,
    state: BatchRunState,
    started_at: datetime,
    report_path: str = "",
):
    try:
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            record = session.query(BatchRun).filter_by(run_id=run_id).first()
            if record:
                record.success_count = state.success
                record.fail_count = state.failed
                record.completed_at = datetime.now(timezone.utc)
                record.report_path = report_path
                record.results_json = json.dumps(state.results, ensure_ascii=False)
                record.status = "completed"
                session.commit()
    except Exception:
        logger.exception("Failed to save batch run end record")
