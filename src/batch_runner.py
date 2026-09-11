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
            run_id,
            len(stock_codes),
            template_name,
        )

        # Save initial batch record
        _save_batch_run_start(
            run_id,
            triggered_by,
            template_id,
            template_name,
            stock_codes,
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
            code: result for code, result in _normalize_results(existing_results).items() if code in stock_code_set
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
            state.set_status("completed", "跑批已结束：LLM 未配置，所有股票均未完成分析")
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

        state.set_status(
            "completed",
            f"跑批完成：成功 {state.success}，失败 {state.failed}",
        )

        # Generate aggregated MD
        report_path = _write_aggregated_report(
            run_id,
            state,
            template_name,
            started_at,
        )

        # Save final batch record
        _save_batch_run_end(run_id, state, started_at, report_path)

        # Send notification
        _send_batch_notification(
            run_id,
            state,
            template_name,
            report_path,
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
    ) -> tuple:
        """Analyze one stock, respecting the concurrency semaphore."""
        with self._semaphore:
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


from . import _batch_runner_functions1 as _batch_runner_functions1
from . import _batch_runner_functions2 as _batch_runner_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_batch_runner_functions1, _batch_runner_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
