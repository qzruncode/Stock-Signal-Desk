# -*- coding: utf-8 -*-
"""
===================================
批量跑批模块
===================================

职责：
1. 并发控制：默认 5 路并发，可通过 BATCH_MAX_CONCURRENT 调整
2. 每只股票调用一次 AI（ai_caller）
3. 结果汇总为单个 MD 文件，存入 reports/ 目录
4. 跑批完成后自动推送通知
5. 跑批记录存入数据库
"""

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.ai_caller import call_ai_for_stock
from src.analyzer import GeminiAnalyzer, get_analyzer
from src.storage import BatchRun, DatabaseManager, persist_llm_usage

logger = logging.getLogger(__name__)

BATCH_REPORTS_DIR = Path(__file__).parent.parent / "reports" / "batch"
DEFAULT_MAX_CONCURRENT = 5
MAX_CONCURRENT_LIMIT = 10
BATCH_DECISION_SCHEMA_MARKER = "BATCH_DECISION_JSON"
BATCH_DECISION_SCHEMA_INSTRUCTION = f"""

【跑批结构化判定要求】
在完整分析正文末尾必须追加一个独立的结构化判定区，格式必须严格如下：

{BATCH_DECISION_SCHEMA_MARKER}
```json
{{
  "decision": "buy",
  "decision_label": "建议买入",
  "reason": "一句话说明最核心原因"
}}
```

字段规则：
- decision 只能取 buy、watch、reject、unknown 四个值之一。
- buy 表示明确筛选通过、可买、建议买入、重点关注且值得纳入本次筛选结果。
- watch 表示继续观察、暂不行动、条件未完全满足，不计入筛选通过。
- reject 表示不买、不建议买入、不通过、排除、回避，不计入筛选通过。
- unknown 表示无法给出清晰结论或信息不足，不计入筛选通过。
- decision_label 保留你给用户看的原始中文结论，例如“建议买入”“不买”“继续观察”。
- reason 必须简短，适合放入跑批汇总表格。
""".strip()


class BatchRunControl:
    """Cooperative controls for a running batch."""

    def __init__(self):
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.stop_event = threading.Event()

    def pause(self):
        self.pause_event.clear()

    def resume(self):
        self.pause_event.set()

    def stop(self):
        self.stop_event.set()
        self.pause_event.set()

    @property
    def paused(self) -> bool:
        return not self.pause_event.is_set() and not self.stop_event.is_set()

    @property
    def stopping(self) -> bool:
        return self.stop_event.is_set()


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


class BatchRunState:
    """Track progress of a running batch."""

    def __init__(self, run_id: str, total: int, existing_results: Optional[Dict[str, dict]] = None):
        self.run_id = run_id
        self.total = total
        self.results: Dict[str, dict] = _normalize_results(existing_results)
        self.success = sum(1 for result in self.results.values() if result.get("success"))
        self.failed = sum(1 for result in self.results.values() if not result.get("success"))
        self.completed = self.success + self.failed
        self.current_stock: Optional[str] = None
        self.current_message: str = (
            f"已恢复 {self.completed}/{self.total}，准备续跑..." if self.completed > 0 else "准备中..."
        )
        self.status = "running"
        self.active_stocks: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._progress_callbacks: List[Callable] = []

    def _notify(self, callbacks: List[Callable]):
        for cb in callbacks:
            try:
                cb(self)
            except Exception:
                logger.debug("Batch progress callback failed", exc_info=True)

    def start_stock(self, stock_code: str, stock_name: str):
        label = f"{stock_name}({stock_code})" if stock_name and stock_name != stock_code else stock_code
        with self._lock:
            self.current_stock = label
            self.current_message = f"{label}：正在调用 AI 模型并联网搜索..."
            self.active_stocks[stock_code] = label
            callbacks = list(self._progress_callbacks)
        self._notify(callbacks)

    def update_stock_progress(self, stock_code: str, stock_name: str, chars_received: int):
        label = f"{stock_name}({stock_code})" if stock_name and stock_name != stock_code else stock_code
        with self._lock:
            self.current_stock = label
            self.current_message = f"{label}：已接收 {chars_received} 字，仍在生成..."
            self.active_stocks[stock_code] = label
            callbacks = list(self._progress_callbacks)
        self._notify(callbacks)

    def abort_all(self, text: str):
        with self._lock:
            self.completed = self.total
            self.success = 0
            self.failed = self.total
            self.current_stock = None
            self.current_message = text
            self.active_stocks.clear()
            self.results["__all__"] = {
                "success": False,
                "text": text,
                "model": "",
            }
            callbacks = list(self._progress_callbacks)
        self._notify(callbacks)

    def add_result(
        self,
        stock_code: str,
        success: bool,
        text: str,
        model: str,
        decision_meta: Optional[Dict[str, str]] = None,
    ):
        with self._lock:
            self.completed += 1
            if success:
                self.success += 1
                status_text = "分析完成"
            else:
                self.failed += 1
                status_text = "分析失败"
            self.active_stocks.pop(stock_code, None)
            self.current_stock = next(iter(self.active_stocks.values()), None)
            self.current_message = (
                f"{stock_code}：{status_text}"
                if self.current_stock is None
                else f"{self.current_stock}：正在调用 AI 模型并联网搜索..."
            )
            result = {
                "success": success,
                "text": text,
                "model": model,
            }
            if success and decision_meta:
                result.update(decision_meta)
            self.results[stock_code] = result
            callbacks = list(self._progress_callbacks)
        self._notify(callbacks)

    def set_status(self, status: str, message: str):
        with self._lock:
            self.status = status
            self.current_message = message
            if status in {"paused", "stopped"}:
                self.current_stock = None
            callbacks = list(self._progress_callbacks)
        self._notify(callbacks)

    def add_progress_callback(self, cb: Callable):
        self._progress_callbacks.append(cb)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "run_id": self.run_id,
                "total": self.total,
                "completed": self.completed,
                "success": self.success,
                "failed": self.failed,
                "current_stock": self.current_stock,
                "current_message": self.current_message,
                "status": self.status,
                "paused": self.status == "paused",
                "stopping": self.status == "stopping",
                "active_stocks": list(self.active_stocks.values()),
                "results": dict(self.results),
            }


class BatchRunner:
    """批量跑批执行器。

    用法::

        runner = BatchRunner()
        runner.run(
            stock_codes=["600519", "000001"],
            system_prompt="...",
            template_name="默认",
            on_progress=lambda state: logger.info("Completed: %s/%s", state.completed, state.total),
        )
    """

    def __init__(self, max_concurrent: Optional[int] = None):
        if max_concurrent is None:
            max_concurrent = _get_batch_max_concurrent()
        self._semaphore = threading.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent

    def run(
        self,
        stock_codes: List[str],
        system_prompt: str,
        *,
        template_name: str = "默认",
        template_id: str = "",
        triggered_by: str = "manual",
        analysis_mode: str = "template",
        force_refresh: bool = False,
        control: Optional[BatchRunControl] = None,
        on_progress: Optional[Callable[[BatchRunState], None]] = None,
    ) -> BatchRunState:
        """执行批量跑批。

        Args:
            stock_codes: 股票代码列表
            system_prompt: 提示词模板内容（用作 system prompt）
            template_name: 模板名称
            template_id: 模板 ID
            triggered_by: 触发来源 (manual/scheduled)
            analysis_mode: 分析模式 (template/buy_criteria)
            force_refresh: 买入判断模式下是否绕过当日缓存重新分析
            on_progress: 每完成一只股票时回调

        Returns:
            BatchRunState with aggregated results
        """
        import uuid

        run_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc)

        logger.info(
            "Batch run started: run_id=%s stocks=%d template=%s mode=%s",
            run_id,
            len(stock_codes),
            template_name,
            analysis_mode,
        )

        # Save initial batch record
        _save_batch_run_start(
            run_id,
            triggered_by,
            template_id,
            template_name,
            stock_codes,
            analysis_mode,
        )

        return self._execute(
            run_id=run_id,
            stock_codes=stock_codes,
            pending_stock_codes=stock_codes,
            system_prompt=system_prompt,
            template_name=template_name,
            started_at=started_at,
            existing_results=None,
            control=control,
            on_progress=on_progress,
            analysis_mode=analysis_mode,
            force_refresh=force_refresh,
        )

    def resume(
        self,
        *,
        run_id: str,
        stock_codes: List[str],
        system_prompt: str,
        template_name: str = "默认",
        analysis_mode: str = "template",
        force_refresh: bool = False,
        started_at: Optional[datetime] = None,
        existing_results: Optional[Dict[str, dict]] = None,
        control: Optional[BatchRunControl] = None,
        on_progress: Optional[Callable[[BatchRunState], None]] = None,
    ) -> BatchRunState:
        """Resume an existing interrupted batch run without re-running completed stocks."""
        stock_code_set = set(stock_codes)
        existing_results = {
            code: result for code, result in _normalize_results(existing_results).items() if code in stock_code_set
        }
        completed_codes = set(existing_results)
        pending_stock_codes = [code for code in stock_codes if code not in completed_codes]
        if started_at is None:
            started_at = datetime.now(timezone.utc)

        logger.info(
            "Batch run resumed: run_id=%s total=%d completed=%d pending=%d template=%s mode=%s",
            run_id,
            len(stock_codes),
            len(completed_codes),
            len(pending_stock_codes),
            template_name,
            analysis_mode,
        )

        _save_batch_run_resume_start(run_id, stock_codes, existing_results)

        return self._execute(
            run_id=run_id,
            stock_codes=stock_codes,
            pending_stock_codes=pending_stock_codes,
            system_prompt=system_prompt,
            template_name=template_name,
            started_at=started_at,
            existing_results=existing_results,
            control=control,
            on_progress=on_progress,
            analysis_mode=analysis_mode,
            force_refresh=force_refresh,
        )

    def _execute(
        self,
        *,
        run_id: str,
        stock_codes: List[str],
        pending_stock_codes: List[str],
        system_prompt: str,
        template_name: str,
        started_at: datetime,
        existing_results: Optional[Dict[str, dict]],
        control: Optional[BatchRunControl],
        on_progress: Optional[Callable[[BatchRunState], None]],
        analysis_mode: str = "template",
        force_refresh: bool = False,
    ) -> BatchRunState:
        if control is None:
            control = BatchRunControl()
        state = BatchRunState(run_id, len(stock_codes), existing_results=existing_results)

        if on_progress:
            state.add_progress_callback(on_progress)
            on_progress(state)

        analyzer = get_analyzer()
        if not analyzer.is_available():
            logger.error("Batch run aborted: LLM not available")
            state.abort_all("LLM 未配置，无法执行跑批")
            _save_batch_run_end(run_id, state, started_at)
            return state

        stopped = False
        pending_queue = list(pending_stock_codes)
        with ThreadPoolExecutor(max_workers=self._max_concurrent, thread_name_prefix="batch") as pool:
            futures = {}
            while pending_queue or futures:
                if control.stop_event.is_set():
                    stopped = True
                    state.set_status("stopping", "正在终止，等待已开始的请求收尾...")
                    for future in list(futures):
                        if future.cancel():
                            futures.pop(future, None)
                    pending_queue.clear()

                while not control.stop_event.is_set() and not control.pause_event.is_set():
                    state.set_status("paused", f"已暂停：{state.completed}/{state.total}")
                    control.pause_event.wait(timeout=0.5)

                if not control.stop_event.is_set() and state.status == "paused":
                    state.set_status("running", "继续跑批中...")

                while (
                    pending_queue
                    and not control.stop_event.is_set()
                    and control.pause_event.is_set()
                    and len(futures) < self._max_concurrent
                ):
                    code = pending_queue.pop(0)
                    stock_name = _lookup_stock_name(code)
                    future = pool.submit(
                        self._analyze_one,
                        analyzer,
                        system_prompt,
                        code,
                        stock_name,
                        state,
                        analysis_mode,
                        force_refresh,
                    )
                    futures[future] = code

                if not futures:
                    continue

                done, _pending = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    code = futures.pop(future)
                    if future.cancelled():
                        continue
                    try:
                        success, text, model, decision_meta = future.result()
                    except Exception as exc:
                        logger.exception("Batch task for %s crashed", code)
                        success, text, model, decision_meta = False, str(exc), "", None
                    state.add_result(code, success, text, model, decision_meta)
                    _save_batch_run_progress(run_id, state)

        if stopped:
            state.set_status("stopped", f"已终止：保留 {state.completed}/{state.total} 个结果")
            _save_batch_run_progress(run_id, state, status="stopped")
            return state

        # Generate aggregated MD
        report_path = _write_aggregated_report(
            run_id,
            state,
            template_name,
            started_at,
            analysis_mode=analysis_mode,
        )

        # Save final batch record
        _save_batch_run_end(run_id, state, started_at, report_path)

        # Send notification
        _send_batch_notification(
            run_id,
            state,
            template_name,
            report_path,
            analysis_mode=analysis_mode,
        )

        logger.info(
            "Batch run complete: run_id=%s ok=%d fail=%d",
            run_id,
            state.completed,
            state.failed,
        )
        return state

    def _analyze_one(
        self,
        analyzer: GeminiAnalyzer,
        system_prompt: str,
        stock_code: str,
        stock_name: str,
        state: BatchRunState,
        analysis_mode: str = "template",
        force_refresh: bool = False,
    ) -> tuple:
        """Analyze one stock, respecting the concurrency semaphore."""
        with self._semaphore:
            if analysis_mode == "buy_criteria":
                return self._analyze_one_criteria(
                    stock_code,
                    stock_name,
                    state,
                    force_refresh,
                )
            try:
                state.start_stock(stock_code, stock_name)
                text, model, _usage = call_ai_for_stock(
                    analyzer,
                    _with_batch_decision_schema(system_prompt),
                    stock_code,
                    stock_name,
                    stream_progress_callback=lambda chars: state.update_stock_progress(stock_code, stock_name, chars),
                )
                return True, text, model, _extract_structured_decision(text)
            except Exception as exc:
                logger.exception("AI call failed for %s(%s)", stock_name, stock_code)
                return False, str(exc), "", None

    def _analyze_one_criteria(
        self,
        stock_code: str,
        stock_name: str,
        state: BatchRunState,
        force_refresh: bool,
    ) -> tuple:
        """Run buy-criteria (8-step) screening for one stock in a batch."""
        try:
            state.start_stock(stock_code, stock_name)
            from src.services.buy_criteria.orchestrator import CriterionOrchestrator

            summary = CriterionOrchestrator().analyze_for_batch(
                stock_code,
                reuse_cache=not force_refresh,
            )
            text = _format_criteria_detail(stock_code, stock_name, summary)
            return True, text, "buy_criteria", _criteria_decision_meta(summary)
        except Exception as exc:
            logger.exception("Criteria analysis failed for %s(%s)", stock_name, stock_code)
            return False, str(exc), "", None


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


_CRITERIA_NUM_LABELS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"]


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
                record.status = status
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
