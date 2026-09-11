"""Background task queue method group 1."""

from __future__ import annotations

from src.services.task_queue import (
    asyncio,
    logging,
    threading,
    uuid,
    ThreadPoolExecutor,
    Future,
    dataclass,
    field,
    datetime,
    Enum,
    Optional,
    Dict,
    List,
    Any,
    TYPE_CHECKING,
    Literal,
    Callable,
    logger,
    TaskStatus,
    TaskInfo,
 )

class _AnalysisTaskQueueMethods1:
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    def __init__(self, max_workers: int = 3):
        # 防止重复初始化
        if hasattr(self, "_initialized") and self._initialized:
            return

        self._max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None

        # 核心数据结构
        self._tasks: Dict[str, TaskInfo] = {}  # task_id -> TaskInfo
        self._futures: Dict[str, Future] = {}  # task_id -> Future

        # SSE 订阅者列表（asyncio.Queue 实例）
        self._subscribers: List["AsyncQueue"] = []
        self._subscribers_lock = threading.Lock()

        # 主事件循环引用（用于跨线程广播）
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        # 线程安全锁
        self._data_lock = threading.RLock()

        # 任务历史保留数量（内存中）
        self._max_history = 100

        self._initialized = True
        logger.info(f"[TaskQueue] 初始化完成，最大并发: {max_workers}")
    @property
    def executor(self) -> ThreadPoolExecutor:
        """懒加载线程池"""
        self._ensure_executor()
        return self._executor
    def _ensure_executor(self) -> None:
        """线程安全地创建 executor（_data_lock 保护）"""
        if self._executor is not None:
            return
        with self._data_lock:
            if self._executor is not None:
                return
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="background_task_")
    @property
    def max_workers(self) -> int:
        """Return current executor max worker setting."""
        return self._max_workers
    def _has_inflight_tasks_locked(self) -> bool:
        """Check whether queue has any pending/processing tasks."""
        return any(task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING) for task in self._tasks.values())
    def sync_max_workers(
        self,
        max_workers: int,
        *,
        log: bool = True,
    ) -> Literal["applied", "unchanged", "deferred_busy"]:
        """
        Try to sync queue concurrency without replacing singleton instance.

        Returns:
            - "applied": new value applied immediately (idle queue only)
            - "unchanged": target equals current value or invalid target
            - "deferred_busy": queue is busy, apply is deferred
        """
        try:
            target = max(1, int(max_workers))
        except (TypeError, ValueError):
            if log:
                logger.warning("[TaskQueue] 忽略非法 MAX_WORKERS 值: %r", max_workers)
            return "unchanged"

        executor_to_shutdown: Optional[ThreadPoolExecutor] = None
        previous: int
        with self._data_lock:
            previous = self._max_workers
            if target == previous:
                return "unchanged"

            if self._has_inflight_tasks_locked():
                if log:
                    logger.info(
                        "[TaskQueue] 最大并发调整延后: 当前繁忙 (%s -> %s)",
                        previous,
                        target,
                    )
                return "deferred_busy"

            self._max_workers = target
            executor_to_shutdown = self._executor
            self._executor = None

        if executor_to_shutdown is not None:
            executor_to_shutdown.shutdown(wait=False)

        if log:
            logger.info("[TaskQueue] 最大并发已更新: %s -> %s", previous, target)
        return "applied"
    def submit_background_task(
        self,
        run_task: Callable[[], Optional[Any]],
        *,
        stock_code: str,
        stock_name: Optional[str] = None,
        report_type: str = "detailed",
        message: Optional[str] = "任务已加入队列",
        task_id: Optional[str] = None,
    ) -> TaskInfo:
        """
        Submit a generic background callable with task lifecycle tracking.

        This is used by callers that need task status visibility for a
        long-running background operation.
        """
        task_id = task_id or uuid.uuid4().hex
        task_info = TaskInfo(
            task_id=task_id,
            stock_code=stock_code,
            stock_name=stock_name,
            status=TaskStatus.PENDING,
            message=message,
            report_type=report_type,
        )

        with self._data_lock:
            if task_id in self._tasks:
                raise ValueError(f"任务 ID 已存在: {task_id}")
            self._tasks[task_id] = task_info
            try:
                future = self.executor.submit(self._execute_background_task, task_id, run_task)
            except Exception:
                del self._tasks[task_id]
                raise

            self._futures[task_id] = future
            self._broadcast_event("task_created", task_info.to_dict())

        return task_info.copy()
    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """
        获取任务信息

        Args:
            task_id: 任务 ID

        Returns:
            TaskInfo 或 None
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            return task.copy() if task else None
    def list_pending_tasks(self) -> List[TaskInfo]:
        """
        获取所有进行中的任务（pending + processing）

        Returns:
            任务列表（副本）
        """
        with self._data_lock:
            return [
                task.copy()
                for task in self._tasks.values()
                if task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
            ]
    def list_all_tasks(self, limit: int = 50) -> List[TaskInfo]:
        """
        获取所有任务（按创建时间倒序）

        Args:
            limit: 返回数量限制

        Returns:
            任务列表（副本）
        """
        with self._data_lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
            return [t.copy() for t in tasks[:limit]]
    def get_task_stats(self) -> Dict[str, int]:
        """
        获取任务统计信息

        Returns:
            统计信息字典
        """
        with self._data_lock:
            stats = {
                "total": len(self._tasks),
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
            }
            for task in self._tasks.values():
                stats[task.status.value] = stats.get(task.status.value, 0) + 1
            return stats
    def update_task_progress(
        self,
        task_id: str,
        progress: int,
        message: Optional[str] = None,
        *,
        event_type: str = "task_progress",
    ) -> Optional[TaskInfo]:
        """
        Update in-flight task progress and broadcast an SSE event.

        Only pending/processing tasks are updated. Progress is clamped to
        [0, 99] so terminal states remain controlled by completion/failure.
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                return None

            next_progress = max(task.progress, max(0, min(99, int(progress))))
            changed = False
            if next_progress != task.progress:
                task.progress = next_progress
                changed = True
            if message is not None and message != task.message:
                task.message = message
                changed = True

            if not changed:
                return task.copy()

            task_snapshot = task.copy()

        self._broadcast_event(event_type, task_snapshot.to_dict())
        return task_snapshot
