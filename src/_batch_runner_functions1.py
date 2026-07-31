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
    _CRITERIA_NUM_LABELS,
 )

__all__ = ['_get_batch_max_concurrent', '_lookup_stock_name', '_normalize_results', '_with_batch_decision_schema', '_criteria_decision_meta', '_format_criteria_detail', '_write_aggregated_report', '_build_template_report_content', '_build_criteria_report_content', '_get_result_items', '_one_line', '_escape_table_cell', '_normalize_decision', '_json_objects_from_text', '_extract_structured_decision', '_get_result_decision', '_is_passed_stock', '_get_stock_decision_summaries', '_send_batch_notification', '_build_batch_notification_content']

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

def _criteria_decision_meta(summary: Dict) -> Dict[str, str]:
    """Map the professional checklist result onto the shared decision shape."""
    final = summary.get("final_decision") or "关键取证未完成，暂停判断"
    decision = (
        "buy"
        if summary.get("gate_pass_complete") is True
        else "unknown" if int(summary.get("insufficient_count") or 0) > 0 else "reject"
    )
    total = summary.get("total", 8)
    passed = summary.get("passed_count", 0)
    failed = summary.get("failed_count", 0)
    insufficient = summary.get("insufficient_count", 0)
    not_evaluated = summary.get("not_evaluated_count", 0)
    reason = (
        f"通过{passed}/{total}；不通过{failed}、取证未完成{insufficient}、"
        f"后续未执行{not_evaluated}。" + _one_line(summary.get("stopped_verdict") or "", limit=90)
    )

    if summary.get("from_cache"):
        reason = f"{reason}（缓存）"

    return {
        "decision": decision,
        "decision_label": final,
        "decision_reason": reason,
        "decision_source": "buy_criteria",
    }

def _format_criteria_detail(stock_code: str, stock_name: str, summary: Dict) -> str:
    """Build the readable professional checklist detail for the batch view."""
    label = f"{stock_name}({stock_code})" if stock_name and stock_name != stock_code else stock_code
    final = summary.get("final_decision") or "关键取证未完成，暂停判断"
    passed = summary.get("passed_count", 0)
    failed = summary.get("failed_count", 0)
    insufficient = summary.get("insufficient_count", 0)
    not_evaluated = summary.get("not_evaluated_count", 0)
    cache_mark = "（缓存复用）" if summary.get("from_cache") else ""

    lines = [
        f"# {label} 八维专业买入分析",
        "",
        f"**最终结论**: {final}{cache_mark}",
        (f"✅ 通过 {passed}/8 / ❌ 不通过 {failed} / " f"? 取证未完成 {insufficient} / 未执行 {not_evaluated}"),
        f"**首个停止项**: {summary.get('stopped_at_name') or '八维全部通过'}",
        "",
        "## 逐项结果",
        "",
    ]
    for c in summary.get("criteria", []):
        idx = c.get("index")
        num = _CRITERIA_NUM_LABELS[idx] if isinstance(idx, int) and 0 <= idx < len(_CRITERIA_NUM_LABELS) else "-"
        mark = {
            "pass": "✅ 通过",
            "fail": "❌ 不通过",
            "insufficient": "? 取证未完成",
        }.get(str(c.get("status") or ""), "? 取证未完成")
        name = c.get("criterion_name") or c.get("criterion_id") or ""
        verdict = (c.get("verdict") or "").strip()
        line = f"{num} {name}  {mark}"
        if verdict:
            line += f" — {verdict}"
        lines.append(line)
    if not_evaluated:
        lines.extend(
            [
                "",
                f"> 首个阻断后，后续 {not_evaluated} 维按布尔状态机未执行。",
            ]
        )

    return "\n".join(lines)

def _write_aggregated_report(
    run_id: str,
    state: BatchRunState,
    template_name: str,
    started_at: datetime,
    analysis_mode: str = "template",
) -> str:
    """Write aggregated MD report and return the file path."""
    BATCH_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = started_at.strftime("%Y%m%d_%H%M%S")
    filename = f"batch_{ts}_{run_id[:8]}.md"
    filepath = BATCH_REPORTS_DIR / filename

    if analysis_mode == "buy_criteria":
        content = _build_criteria_report_content(state, template_name, started_at)
    else:
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

def _build_criteria_report_content(
    state: BatchRunState,
    template_name: str,
    started_at: datetime,
) -> str:
    """Build the buy-criteria-mode aggregated report (8/8 通过 / 卡点).

    Keeps the ``## 筛选通过股票`` heading with a bare-code first column so the
    frontend can extract passed codes and build a watchlist group.
    """
    result_items = _get_result_items(state)
    failed_items = [(code, result) for code, result in result_items if not result.get("success")]
    summary_items = _get_stock_decision_summaries(result_items)
    passed_items = [item for item in summary_items if item["decision"] == "buy"]
    rejected_items = [item for item in summary_items if item["decision"] != "buy"]
    success_rate = (state.success / state.total * 100) if state.total else 0

    lines = [
        "# 买入判断筛选汇总",
        "",
        f"- **触发时间**: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **筛选方式**: 八维布尔买入判断（首个非通过即停止）",
        f"- **股票数量**: {state.total}",
        f"- **完成率**: {state.completed}/{state.total}",
        f"- **分析成功率**: {success_rate:.1f}%",
        f"- **筛选通过(8/8)**: {len(passed_items)}",
        f"- **未通过**: {len(rejected_items)}",
        f"- **分析失败**: {len(failed_items)}",
        "",
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
        f"| 筛选通过(8/8) | {len(passed_items)} |",
        f"| 未通过 | {len(rejected_items)} |",
        f"| 分析成功率 | {success_rate:.1f}% |",
        "",
    ]

    lines.extend(["## 筛选通过股票", ""])
    if passed_items:
        lines.extend(
            [
                "| 股票 | 结论 | 摘要 |",
                "| --- | --- | --- |",
            ]
        )
        for item in passed_items:
            lines.append(f"| {item['code']} | {item['label']} | {item['reason']} |")
    else:
        lines.append("本次筛选没有八维全部通过的股票。")
    lines.append("")

    if rejected_items:
        lines.extend(["## 未通过股票", ""])
        lines.extend(
            [
                "| 股票 | 结论 | 卡点与理由 |",
                "| --- | --- | --- |",
            ]
        )
        for item in rejected_items:
            lines.append(f"| {item['code']} | {item['label']} | {item['reason']} |")
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
    analysis_mode: str = "template",
):
    """Send WeChat notification about completed batch run."""
    try:
        from src.notification import get_notification_service

        content = _build_batch_notification_content(
            run_id,
            state,
            template_name,
            report_path,
            analysis_mode=analysis_mode,
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
    analysis_mode: str = "template",
) -> str:
    """Build batch completion notification markdown content."""
    if analysis_mode == "buy_criteria":
        return _build_criteria_notification_content(state, template_name, report_path)
    return _build_template_notification_content(state, template_name, report_path)
