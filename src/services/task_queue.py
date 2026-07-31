# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 异步任务队列
===================================

职责：
1. 管理异步分析任务的生命周期
2. 防止相同股票代码重复提交
3. 提供 SSE 事件广播机制
4. 任务完成后持久化到数据库
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, TYPE_CHECKING, Tuple, Literal, Callable

if TYPE_CHECKING:
    from asyncio import Queue as AsyncQueue

from data_provider.utils import canonical_stock_code, normalize_stock_code
from src.utils.analysis_metadata import SELECTION_SOURCES

logger = logging.getLogger(__name__)


def _dedupe_stock_code_key(stock_code: str) -> str:
    """
    Build the internal duplicate-detection key for a stock code.

    The task queue should treat equivalent market code shapes as the same
    underlying stock, e.g. ``600519`` and ``600519.SH``.
    """
    return canonical_stock_code(normalize_stock_code(stock_code))


class TaskStatus(str, Enum):
    """Task status enumeration"""

    PENDING = "pending"  # Waiting for execution
    PROCESSING = "processing"  # In progress
    COMPLETED = "completed"  # Completed
    FAILED = "failed"  # Failed


@dataclass
class TaskInfo:
    """
    Task information dataclass.

    Used for API responses and internal task management.
    """

    task_id: str
    stock_code: str
    stock_name: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    report_type: str = "detailed"
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    original_query: Optional[str] = None
    selection_source: Optional[str] = None
    prompt_template_id: Optional[str] = None
    prompt_template_name: Optional[str] = None
    conversation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert task info into an API-friendly dictionary."""
        return {
            "task_id": self.task_id,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "report_type": self.report_type,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "original_query": self.original_query,
            "selection_source": self.selection_source,
            "prompt_template_id": self.prompt_template_id,
            "prompt_template_name": self.prompt_template_name,
            "conversation": self.conversation,
            "result": self.result,
        }

    def copy(self) -> "TaskInfo":
        """Create a shallow copy of the task information."""
        return TaskInfo(
            task_id=self.task_id,
            stock_code=self.stock_code,
            stock_name=self.stock_name,
            status=self.status,
            progress=self.progress,
            message=self.message,
            error=self.error,
            report_type=self.report_type,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            original_query=self.original_query,
            selection_source=self.selection_source,
            prompt_template_id=self.prompt_template_id,
            prompt_template_name=self.prompt_template_name,
            conversation=dict(self.conversation) if isinstance(self.conversation, dict) else self.conversation,
            result=dict(self.result) if isinstance(self.result, dict) else self.result,
        )


class DuplicateTaskError(Exception):
    """
    重复提交异常

    当股票已在分析中时抛出此异常
    """

    def __init__(self, stock_code: str, existing_task_id: str):
        self.stock_code = stock_code
        self.existing_task_id = existing_task_id
        super().__init__(f"股票 {stock_code} 正在分析中 (task_id: {existing_task_id})")


from ._task_queue_methods1 import _AnalysisTaskQueueMethods1
from ._task_queue_methods2 import _AnalysisTaskQueueMethods2
class AnalysisTaskQueue(_AnalysisTaskQueueMethods1, _AnalysisTaskQueueMethods2):
        """
        异步分析任务队列

        单例模式，全局唯一实例

        特性：
        1. 防止相同股票代码重复提交
        2. 线程池执行分析任务
        3. SSE 事件广播机制
        4. 任务完成后自动持久化
        """
        _instance: Optional["AnalysisTaskQueue"] = None
        _instance_lock = threading.Lock()


def _bind_mixin_member(_member):
    import functools
    import types

    if isinstance(_member, staticmethod):
        return staticmethod(_bind_mixin_member(_member.__func__))
    if isinstance(_member, classmethod):
        return classmethod(_bind_mixin_member(_member.__func__))
    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _mixin in (_AnalysisTaskQueueMethods1, _AnalysisTaskQueueMethods2):
    for _name, _member in _mixin.__dict__.items():
        if _name not in {"__dict__", "__weakref__"}:
            setattr(AnalysisTaskQueue, _name, _bind_mixin_member(_member))


# ========== 便捷函数 ==========


def get_task_queue() -> AnalysisTaskQueue:
    """
    获取任务队列单例

    Returns:
        AnalysisTaskQueue 实例
    """
    queue = AnalysisTaskQueue()
    try:
        from src.config import get_config

        config = get_config()
        target_workers = max(1, int(getattr(config, "max_workers", queue.max_workers)))
        queue.sync_max_workers(target_workers, log=False)
    except Exception as exc:
        logger.debug("[TaskQueue] 读取 MAX_WORKERS 失败，使用当前并发设置: %s", exc)

    return queue
