"""AnalysisTaskQueue method group 2."""

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
    Tuple,
    Literal,
    Callable,
    canonical_stock_code,
    normalize_stock_code,
    SELECTION_SOURCES,
    logger,
    _dedupe_stock_code_key,
    TaskStatus,
    TaskInfo,
    DuplicateTaskError,
 )

class _AnalysisTaskQueueMethods2:
    def update_task_result(
        self,
        task_id: str,
        result: Dict[str, Any],
        *,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        event_type: str = "task_progress",
    ) -> Optional[TaskInfo]:
        """
        Merge partial task result data and broadcast an SSE update.

        This is used by long-running tasks that need to push incremental
        payloads (for example, streaming model output) to the frontend.
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                return None

            changed = False
            existing_result = task.result if isinstance(task.result, dict) else {}
            if result:
                task.result = {**existing_result, **result}
                changed = True

            if progress is not None:
                next_progress = max(task.progress, max(0, min(99, int(progress))))
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
    def _clear_analyzing_stock_locked(self, task: TaskInfo) -> None:
        """Remove an in-flight stock marker only when it still points to this task."""
        dedupe_key = _dedupe_stock_code_key(task.stock_code)
        if self._analyzing_stocks.get(dedupe_key) == task.task_id:
            del self._analyzing_stocks[dedupe_key]
    def _mark_task_completed_locked(
        self,
        task_id: str,
        result: Any,
        message: str = "分析完成",
    ) -> Optional[TaskInfo]:
        """Transition a task to completed and return a broadcast snapshot."""
        task = self._tasks.get(task_id)
        if not task:
            return None

        task.status = TaskStatus.COMPLETED
        task.progress = 100
        task.completed_at = datetime.now()
        task.result = result
        task.message = message
        if isinstance(result, dict):
            task.stock_name = result.get("stock_name", task.stock_name)
        self._clear_analyzing_stock_locked(task)
        return task.copy()
    def _mark_task_failed_locked(
        self,
        task_id: str,
        error_msg: str,
        *,
        message_prefix: str = "分析失败",
        message_limit: int = 50,
    ) -> Optional[TaskInfo]:
        """Transition a task to failed and return a broadcast snapshot."""
        task = self._tasks.get(task_id)
        if not task:
            return None

        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now()
        task.error = error_msg[:200]
        task.message = f"{message_prefix}: {error_msg[:message_limit]}"
        self._clear_analyzing_stock_locked(task)
        return task.copy()
    def _execute_task(
        self,
        task_id: str,
        stock_code: str,
        report_type: str,
        force_refresh: bool,
        notify: bool = True,
        prompt_template_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        执行分析任务（在线程池中运行）

        Args:
            task_id: 任务 ID
            stock_code: 股票代码
            report_type: 报告类型
            force_refresh: 是否强制刷新

        Returns:
            分析结果字典
        """
        # 更新状态为处理中
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "正在分析中..."
            task.progress = 10

        self._broadcast_event("task_started", task.to_dict())

        try:
            # 导入分析服务（延迟导入避免循环依赖）
            from src.services.analysis_service import AnalysisService

            # 执行分析
            service = AnalysisService()

            def _on_progress(progress: int, message: str) -> None:
                self.update_task_progress(task_id, progress, message)

            def _on_conversation(conversation: Dict[str, Any]) -> None:
                with self._data_lock:
                    task = self._tasks.get(task_id)
                    if not task:
                        return
                    task.conversation = conversation
                    task.prompt_template_id = conversation.get("template_id") or task.prompt_template_id
                    task.prompt_template_name = conversation.get("template_name") or task.prompt_template_name
                    snapshot = task.copy()
                self._broadcast_event("task_progress", snapshot.to_dict())

            result = service.analyze_stock(
                stock_code=stock_code,
                report_type=report_type,
                force_refresh=force_refresh,
                query_id=task_id,
                send_notification=notify,
                progress_callback=_on_progress,
                prompt_template_id=prompt_template_id,
                conversation_callback=_on_conversation,
            )

            if result:
                with self._data_lock:
                    task_snapshot = self._mark_task_completed_locked(task_id, result)

                if task_snapshot is not None:
                    self._broadcast_event("task_completed", task_snapshot.to_dict())
                logger.info(f"[TaskQueue] 任务完成: {task_id} ({stock_code})")

                # 清理过期任务
                self._cleanup_old_tasks()

                return result
            else:
                # 分析返回空结果
                raise Exception(service.last_error or "分析返回空结果")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TaskQueue] 任务失败: {task_id} ({stock_code}), 错误: {error_msg}")

            with self._data_lock:
                task_snapshot = self._mark_task_failed_locked(task_id, error_msg)

            if task_snapshot is not None:
                self._broadcast_event("task_failed", task_snapshot.to_dict())

            # 清理过期任务
            self._cleanup_old_tasks()

            return None
    def _execute_background_task(
        self,
        task_id: str,
        run_task: Callable[[], Optional[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """
        执行通用后台任务（支持自定义运行逻辑）

        Args:
            task_id: 任务 ID
            run_task: 任务执行函数

        Returns:
            任务执行结果字典（可选）
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "任务执行中"
            task.progress = 10
            self._broadcast_event("task_started", task.to_dict())

        try:
            result = run_task()
            if result is None:
                raise RuntimeError("任务返回空结果，未生成可持久化内容")

            with self._data_lock:
                task_snapshot = self._mark_task_completed_locked(
                    task_id,
                    result,
                    message="任务执行完成",
                )

            if task_snapshot is not None:
                self._broadcast_event("task_completed", task_snapshot.to_dict())
            logger.info(f"[TaskQueue] 自定义任务完成: {task_id}")

            self._cleanup_old_tasks()
            return result

        except Exception as e:  # pragma: no cover - behavior verified in downstream tests
            error_msg = str(e)
            logger.error(f"[TaskQueue] 自定义任务失败: {task_id}, 错误: {error_msg}")

            with self._data_lock:
                task_snapshot = self._mark_task_failed_locked(
                    task_id,
                    error_msg,
                    message_prefix="任务失败",
                    message_limit=80,
                )

            if task_snapshot is not None:
                self._broadcast_event("task_failed", task_snapshot.to_dict())

            self._cleanup_old_tasks()
            return None
    def _cleanup_old_tasks(self) -> int:
        """
        清理过期的已完成任务

        保留最近 _max_history 个任务

        Returns:
            清理的任务数量
        """
        with self._data_lock:
            if len(self._tasks) <= self._max_history:
                return 0

            # 按时间排序，删除旧的已完成任务
            completed_tasks = sorted(
                [t for t in self._tasks.values() if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)],
                key=lambda t: t.created_at,
            )

            to_remove = len(self._tasks) - self._max_history
            removed = 0

            for task in completed_tasks[:to_remove]:
                del self._tasks[task.task_id]
                if task.task_id in self._futures:
                    del self._futures[task.task_id]
                removed += 1

            if removed > 0:
                logger.debug(f"[TaskQueue] 清理了 {removed} 个过期任务")

            return removed
    def subscribe(self, queue: "AsyncQueue") -> None:
        """
        订阅任务事件

        Args:
            queue: asyncio.Queue 实例，用于接收事件
        """
        with self._subscribers_lock:
            self._subscribers.append(queue)
            # 捕获当前事件循环（应在主线程的 async 上下文中调用）
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                # 如果不在 async 上下文中，尝试获取事件循环
                try:
                    self._main_loop = asyncio.get_event_loop()
                except RuntimeError:
                    pass
            logger.debug(f"[TaskQueue] 新订阅者加入，当前订阅者数: {len(self._subscribers)}")
    def unsubscribe(self, queue: "AsyncQueue") -> None:
        """
        取消订阅任务事件

        Args:
            queue: 要取消订阅的 asyncio.Queue 实例
        """
        with self._subscribers_lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)
                logger.debug(f"[TaskQueue] 订阅者离开，当前订阅者数: {len(self._subscribers)}")
    def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        广播事件到所有订阅者

        使用 call_soon_threadsafe 确保跨线程安全

        Args:
            event_type: 事件类型
            data: 事件数据
        """
        event = {"type": event_type, "data": data}

        with self._subscribers_lock:
            subscribers = self._subscribers.copy()
            loop = self._main_loop

        if not subscribers:
            return

        if loop is None:
            logger.warning("[TaskQueue] 无法广播事件：主事件循环未设置")
            return

        for queue in subscribers:
            try:
                # 使用 call_soon_threadsafe 将事件放入 asyncio 队列
                # 这是从工作线程向主事件循环发送消息的安全方式
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError as e:
                # 事件循环已关闭
                logger.debug(f"[TaskQueue] 广播事件跳过（循环已关闭）: {e}")
            except Exception as e:
                logger.warning(f"[TaskQueue] 广播事件失败: {e}")
    def shutdown(self) -> None:
        """关闭任务队列"""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
            logger.info("[TaskQueue] 线程池已关闭")
