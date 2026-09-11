# -*- coding: utf-8 -*-
"""
===================================
 A股自选股智能分析系统 - 通用后台任务队列
===================================

职责：
1. 管理通用后台任务的生命周期
2. 提供 SSE 事件广播机制
3. 提供线程池并发调整与进度更新
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
from typing import Optional, Dict, List, Any, TYPE_CHECKING, Literal, Callable

if TYPE_CHECKING:
    from asyncio import Queue as AsyncQueue

logger = logging.getLogger(__name__)


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

    Used for generic background-task status and event delivery.
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
            result=dict(self.result) if isinstance(self.result, dict) else self.result,
        )


from ._task_queue_methods1 import _AnalysisTaskQueueMethods1
from ._task_queue_methods2 import _AnalysisTaskQueueMethods2
class AnalysisTaskQueue(_AnalysisTaskQueueMethods1, _AnalysisTaskQueueMethods2):
        """
        通用后台任务队列

        单例模式，全局唯一实例

        特性：
        1. 线程池执行后台任务
        2. SSE 事件广播机制
        3. 支持任务进度与结果查询
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
