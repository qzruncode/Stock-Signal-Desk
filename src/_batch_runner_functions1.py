"""Function group 1 extracted from src/batch_runner.py."""

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
 )

__all__ = ['_get_batch_max_concurrent', '_lookup_stock_name', '_normalize_results', '_with_batch_decision_schema', '_write_aggregated_report', '_build_template_report_content', '_get_result_items', '_one_line', '_escape_table_cell', '_normalize_decision', '_json_objects_from_text', '_extract_structured_decision', '_get_result_decision', '_is_passed_stock', '_get_stock_decision_summaries', '_send_batch_notification', '_build_batch_notification_content']

def _get_batch_max_concurrent() -> int:
    raw = os.getenv("BATCH_MAX_CONCURRENT", "").strip()
    if not raw:
        return DEFAULT_MAX_CONCURRENT
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid BATCH_MAX_CONCURRENT=%r, using default %d", raw, DEFAULT_MAX_CONCURRENT)
        return DEFAULT_MAX_CONCURRENT
    if value < 1:
        logger.warning("BATCH_MAX_CONCURRENT=%d is too small, using 1", value)
        return 1
    if value > MAX_CONCURRENT_LIMIT:
        logger.warning(
            "BATCH_MAX_CONCURRENT=%d exceeds limit %d, using %d",
            value,
            MAX_CONCURRENT_LIMIT,
            MAX_CONCURRENT_LIMIT,
        )
        return MAX_CONCURRENT_LIMIT
    return value

def _lookup_stock_name(code: str) -> str:
    try:
        from src.data.stock_mapping import STOCK_NAME_MAP

        return STOCK_NAME_MAP.get(code, code)
    except Exception:
        return code

def _normalize_results(value: Optional[Dict[str, dict]]) -> Dict[str, dict]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, dict] = {}
    for code, result in value.items():
        if code == "__all__" or not isinstance(result, dict):
            continue
        normalized[str(code)] = result
    return normalized

def _with_batch_decision_schema(system_prompt: str) -> str:
    base = (system_prompt or "").strip()
    if BATCH_DECISION_SCHEMA_MARKER in base:
        return base
    return f"{base}\n\n{BATCH_DECISION_SCHEMA_INSTRUCTION}".strip()

def _write_aggregated_report(
    run_id: str,
    state: BatchRunState,
    template_name: str,
    started_at: datetime,
) -> str:
    """Write aggregated MD report and return the file path."""
    BATCH_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = started_at.strftime("%Y%m%d_%H%M%S")
    filename = f"batch_{ts}_{run_id[:8]}.md"
    filepath = BATCH_REPORTS_DIR / filename

    content = _build_template_report_content(state, template_name, started_at)

    filepath.write_text(content, encoding="utf-8")
    logger.info("Batch report saved: %s", filepath)
    return str(filepath)

def _build_template_report_content(
    state: BatchRunState,
    template_name: str,
    started_at: datetime,
) -> str:
    """Build the template-mode aggregated report (buy/watch/reject/unknown)."""
    result_items = _get_result_items(state)
    failed_items = [(code, result) for code, result in result_items if not result.get("success")]
    summary_items = _get_stock_decision_summaries(result_items)
    passed_items = [item for item in summary_items if item["decision"] == "buy"]
    watch_items = [item for item in summary_items if item["decision"] == "watch"]
    rejected_items = [item for item in summary_items if item["decision"] == "reject"]
    unknown_items = [item for item in summary_items if item["decision"] == "unknown"]
    success_rate = (state.success / state.total * 100) if state.total else 0

    lines = [
        "# 跑批筛选汇总",
        f"",
        f"- **触发时间**: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **分析模板**: {template_name}",
        f"- **股票数量**: {state.total}",
        f"- **完成率**: {state.completed}/{state.total}",
        f"- **分析成功率**: {success_rate:.1f}%",
        f"- **筛选通过**: {len(passed_items)}",
        f"- **观察**: {len(watch_items)}",
        f"- **排除**: {len(rejected_items)}",
        f"- **待确认**: {len(unknown_items)}",
        f"",
        "---",
        "",
        "## 统计概览",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 总股票数 | {state.total} |",
        f"| 已完成 | {state.completed} |",
        f"| 分析成功 | {state.success} |",
        f"| 分析失败 | {state.failed} |",
        f"| 筛选通过 | {len(passed_items)} |",
        f"| 观察 | {len(watch_items)} |",
        f"| 排除 | {len(rejected_items)} |",
        f"| 待确认 | {len(unknown_items)} |",
        f"| 分析成功率 | {success_rate:.1f}% |",
        "",
    ]

    lines.extend(["## 筛选通过股票", ""])
    if passed_items:
        lines.extend(
            [
                "| 股票 | 结论 | 摘要理由 | 模型 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in passed_items:
            lines.append(f"| {item['code']} | {item['label']} | {item['reason']} | `{item['model']}` |")
    else:
        lines.append("本次跑批没有识别到明确筛选通过的股票。")
    lines.append("")

    if unknown_items:
        lines.extend(["## 待人工确认", ""])
        lines.extend(
            [
                "| 股票 | 识别到的结论 | 摘要理由 | 模型 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in unknown_items:
            lines.append(f"| {item['code']} | {item['label']} | {item['reason']} | `{item['model']}` |")
        lines.append("")

    if failed_items:
        lines.extend(["## 分析失败", ""])
        for code, result in failed_items:
            reason = _one_line(result.get("text") or "未知错误", limit=100)
            lines.append(f"- **{code}**: {reason}")
        lines.append("")

    return "\n".join(lines)

def _get_result_items(state: BatchRunState) -> List[tuple[str, dict]]:
    return [(code, result) for code, result in state.results.items() if code != "__all__" and isinstance(result, dict)]

def _one_line(text: str, limit: int = 80) -> str:
    compact = _escape_table_cell(" ".join(str(text).split()))
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."

def _escape_table_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()

def _normalize_decision(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"buy", "watch", "reject", "unknown"}:
        return normalized
    return "unknown"

def _json_objects_from_text(text: str) -> List[dict]:
    decoder = json.JSONDecoder()
    objects: List[dict] = []
    raw = str(text or "")
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[index:])
        except Exception:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects

def _extract_structured_decision(text: str) -> Optional[Dict[str, str]]:
    marker_index = str(text or "").rfind(BATCH_DECISION_SCHEMA_MARKER)
    candidates = _json_objects_from_text(str(text or "")[marker_index:] if marker_index >= 0 else str(text or ""))
    for obj in reversed(candidates):
        decision_value = obj.get("decision") or obj.get("batch_decision")
        if isinstance(decision_value, dict):
            obj = decision_value
            decision_value = obj.get("decision")
        if not decision_value:
            continue
        decision = _normalize_decision(str(decision_value))
        label = str(obj.get("decision_label") or obj.get("label") or obj.get("conclusion") or decision).strip()
        reason = str(obj.get("reason") or obj.get("summary") or obj.get("decision_reason") or "").strip()
        return {
            "decision": decision,
            "decision_label": label or decision,
            "decision_reason": reason or "模型未给出摘要理由",
            "decision_source": "structured",
        }
    return None

def _get_result_decision(result: dict) -> Dict[str, str]:
    if result.get("decision"):
        return {
            "decision": _normalize_decision(result.get("decision") or ""),
            "decision_label": str(result.get("decision_label") or result.get("decision") or "unknown"),
            "decision_reason": str(result.get("decision_reason") or result.get("reason") or "模型未给出摘要理由"),
            "decision_source": str(result.get("decision_source") or "stored"),
        }
    structured = _extract_structured_decision(result.get("text") or "")
    if structured:
        return structured
    return {
        "decision": "unknown",
        "decision_label": "未提供结构化结论",
        "decision_reason": "模型输出缺少批量决策结构，程序未从自然语言中猜测结论",
        "decision_source": "missing_structured_decision",
    }

def _is_passed_stock(text: str) -> bool:
    structured = _extract_structured_decision(text)
    return bool(structured and structured["decision"] == "buy")

def _get_stock_decision_summaries(result_items: List[tuple[str, dict]]) -> List[dict]:
    summaries = []
    for code, result in result_items:
        if not result.get("success"):
            continue
        decision_meta = _get_result_decision(result)
        summaries.append(
            {
                "code": _escape_table_cell(code),
                "decision": decision_meta["decision"],
                "label": _one_line(decision_meta["decision_label"], limit=28),
                "reason": _one_line(decision_meta["decision_reason"], limit=90),
                "source": _escape_table_cell(decision_meta["decision_source"]),
                "model": _escape_table_cell(result.get("model") or "-"),
            }
        )
    return summaries

def _send_batch_notification(
    run_id: str,
    state: BatchRunState,
    template_name: str,
    report_path: str,
):
    """Send WeChat notification about completed batch run."""
    try:
        from src.notification import get_notification_service

        content = _build_batch_notification_content(
            run_id,
            state,
            template_name,
            report_path,
        )
        service = get_notification_service()
        service.send(content)
        logger.info("Batch notification sent: run_id=%s", run_id)
    except Exception:
        logger.exception("Failed to send batch notification")

def _build_batch_notification_content(
    run_id: str,
    state: BatchRunState,
    template_name: str,
    report_path: str,
) -> str:
    """Build batch completion notification markdown content."""
    return _build_template_notification_content(state, template_name, report_path)
