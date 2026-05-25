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
            f"已恢复 {self.completed}/{self.total}，准备续跑..."
            if self.completed > 0
            else "准备中..."
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

    def add_result(self, stock_code: str, success: bool, text: str, model: str):
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
            self.results[stock_code] = {
                "success": success,
                "text": text,
                "model": model,
            }
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
            on_progress=lambda state: print(state.completed),
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
            on_progress: 每完成一只股票时回调

        Returns:
            BatchRunState with aggregated results
        """
        import uuid

        run_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc)

        logger.info(
            "Batch run started: run_id=%s stocks=%d template=%s",
            run_id, len(stock_codes), template_name,
        )

        # Save initial batch record
        _save_batch_run_start(run_id, triggered_by, template_id, template_name, stock_codes)

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
        )

    def resume(
        self,
        *,
        run_id: str,
        stock_codes: List[str],
        system_prompt: str,
        template_name: str = "默认",
        started_at: Optional[datetime] = None,
        existing_results: Optional[Dict[str, dict]] = None,
        control: Optional[BatchRunControl] = None,
        on_progress: Optional[Callable[[BatchRunState], None]] = None,
    ) -> BatchRunState:
        """Resume an existing interrupted batch run without re-running completed stocks."""
        stock_code_set = set(stock_codes)
        existing_results = {
            code: result
            for code, result in _normalize_results(existing_results).items()
            if code in stock_code_set
        }
        completed_codes = set(existing_results)
        pending_stock_codes = [code for code in stock_codes if code not in completed_codes]
        if started_at is None:
            started_at = datetime.now(timezone.utc)

        logger.info(
            "Batch run resumed: run_id=%s total=%d completed=%d pending=%d template=%s",
            run_id,
            len(stock_codes),
            len(completed_codes),
            len(pending_stock_codes),
            template_name,
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
                        analyzer, system_prompt, code, stock_name, state,
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
                        success, text, model = future.result()
                    except Exception as exc:
                        logger.exception("Batch task for %s crashed", code)
                        success, text, model = False, str(exc), ""
                    state.add_result(code, success, text, model)
                    _save_batch_run_progress(run_id, state)

        if stopped:
            state.set_status("stopped", f"已终止：保留 {state.completed}/{state.total} 个结果")
            _save_batch_run_progress(run_id, state, status="stopped")
            return state

        # Generate aggregated MD
        report_path = _write_aggregated_report(run_id, state, template_name, started_at)

        # Save final batch record
        _save_batch_run_end(run_id, state, started_at, report_path)

        # Send notification
        _send_batch_notification(run_id, state, template_name, report_path)

        logger.info(
            "Batch run complete: run_id=%s ok=%d fail=%d",
            run_id, state.completed, state.failed,
        )
        return state

    def _analyze_one(
        self,
        analyzer: GeminiAnalyzer,
        system_prompt: str,
        stock_code: str,
        stock_name: str,
        state: BatchRunState,
    ) -> tuple:
        """Analyze one stock, respecting the concurrency semaphore."""
        with self._semaphore:
            try:
                state.start_stock(stock_code, stock_name)
                text, model, _usage = call_ai_for_stock(
                    analyzer,
                    system_prompt,
                    stock_code,
                    stock_name,
                    stream_progress_callback=lambda chars: state.update_stock_progress(
                        stock_code, stock_name, chars
                    ),
                )
                return True, text, model
            except Exception as exc:
                logger.exception("AI call failed for %s(%s)", stock_name, stock_code)
                return False, str(exc), ""


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

    result_items = _get_result_items(state)
    failed_items = [(code, result) for code, result in result_items if not result.get("success")]
    passed_items = _get_passed_stock_summaries(result_items)
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
        f"| 分析成功率 | {success_rate:.1f}% |",
        "",
    ]

    lines.extend(["## 筛选通过股票", ""])
    if passed_items:
        lines.extend([
            "| 股票 | 结论 | 摘要理由 | 模型 |",
            "| --- | --- | --- | --- |",
        ])
        for item in passed_items:
            lines.append(
                f"| {item['code']} | {item['decision']} | {item['reason']} | `{item['model']}` |"
            )
    else:
        lines.append("本次跑批没有识别到明确筛选通过的股票。")
    lines.append("")

    if failed_items:
        lines.extend(["## 分析失败", ""])
        for code, result in failed_items:
            reason = _one_line(result.get("text") or "未知错误", limit=100)
            lines.append(f"- **{code}**: {reason}")
        lines.append("")

    content = "\n".join(lines)
    filepath.write_text(content, encoding="utf-8")
    logger.info("Batch report saved: %s", filepath)
    return str(filepath)


def _get_result_items(state: BatchRunState) -> List[tuple[str, dict]]:
    return [
        (code, result)
        for code, result in state.results.items()
        if code != "__all__" and isinstance(result, dict)
    ]


def _one_line(text: str, limit: int = 80) -> str:
    compact = _escape_table_cell(" ".join(str(text).split()))
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."


def _escape_table_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _extract_decision(text: str) -> str:
    patterns = [
        r"(?:操作建议|投资建议|最终建议|结论|核心结论|筛选结果|评级)[:：]\s*([^\n。；;|]{2,40})",
        r"(强烈买入|建议买入|可以买入|买入|重点关注|可关注|继续关注|筛选通过|通过)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _one_line(match.group(1), limit=28)
    return "通过"


def _extract_reason(text: str) -> str:
    candidates = []
    for raw_line in str(text).splitlines():
        line = raw_line.strip().strip("-*#> ")
        if not line:
            continue
        if any(token in line for token in ("理由", "原因", "看点", "核心", "摘要", "结论", "优势", "催化")):
            candidates.append(line)
    if not candidates:
        candidates = [line.strip().strip("-*#> ") for line in str(text).splitlines() if line.strip()]
    return _one_line(candidates[0] if candidates else "模型未给出摘要理由", limit=90)


def _is_passed_stock(text: str) -> bool:
    lowered = str(text).lower()
    negative_patterns = [
        "筛选不通过", "未通过", "不通过", "不建议买入", "暂不建议", "不宜买入",
        "回避", "淘汰", "排除", "观望为主", "继续观察", "暂不纳入",
    ]
    if any(token in lowered for token in negative_patterns):
        return False
    positive_patterns = [
        "筛选通过", "通过", "建议买入", "强烈买入", "可以买入", "买入",
        "重点关注", "可关注", "纳入观察", "值得关注", "机会较好",
    ]
    return any(token in lowered for token in positive_patterns)


def _get_passed_stock_summaries(result_items: List[tuple[str, dict]]) -> List[dict]:
    passed = []
    for code, result in result_items:
        if not result.get("success"):
            continue
        text = result.get("text") or ""
        if not _is_passed_stock(text):
            continue
        passed.append({
            "code": _escape_table_cell(code),
            "decision": _extract_decision(text),
            "reason": _extract_reason(text),
            "model": _escape_table_cell(result.get("model") or "-"),
        })
    return passed


def _send_batch_notification(
    run_id: str,
    state: BatchRunState,
    template_name: str,
    report_path: str,
):
    """Send WeChat notification about completed batch run."""
    try:
        from src.notification import get_notification_service

        content = _build_batch_notification_content(run_id, state, template_name, report_path)
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
    from datetime import datetime

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_name = Path(report_path).name

    result_items = _get_result_items(state)
    failed_items = [(code, result) for code, result in result_items if not result.get("success")]
    passed_items = _get_passed_stock_summaries(result_items)
    success_rate = (state.success / state.total * 100) if state.total else 0

    lines = [
        "## 跑批筛选汇总",
        "",
        f"> 模板: **{template_name}**",
        f"> 时间: {now}",
        f"> 完成: **{state.completed}/{state.total}**",
        f"> 分析成功: **{state.success}** | 分析失败: **{state.failed}** | 分析成功率: **{success_rate:.1f}%**",
        f"> 筛选通过: **{len(passed_items)}**",
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
    lines.append(f"| 分析成功率 | {success_rate:.1f}% |")
    lines.append("")

    lines.append("### 筛选通过股票")
    lines.append("")
    if passed_items:
        lines.append("| 股票 | 结论 | 摘要理由 |")
        lines.append("| --- | --- | --- |")
        for item in passed_items[:20]:
            lines.append(f"| {item['code']} | {item['decision']} | {item['reason']} |")
        if len(passed_items) > 20:
            lines.append(f"| ... | ... | 另有 {len(passed_items) - 20} 只通过，请查看完整报告 |")
    else:
        lines.append("本次跑批没有识别到明确筛选通过的股票。")
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
