"""ActiveRunRegistry method group 2."""

from __future__ import annotations

from src.agent.run_registry import (
    asyncio,
    logging,
    os,
    socket,
    uuid,
    dataclass,
    field,
    datetime,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    AssistantStreamChunk,
    DataChunk,
    ErrorChunk,
    ReasoningDeltaChunk,
    TextDeltaChunk,
    ToolCallBeginChunk,
    ToolCallDeltaChunk,
    ToolResultChunk,
    logger,
    _SUBSCRIBER_QUEUE_MAXSIZE,
    _RUN_HISTORY_MAX_CHUNKS,
    _RUN_RETENTION_SECONDS,
    _RUN_LEASE_SECONDS,
    _RUN_HEARTBEAT_SECONDS,
    _RUN_CANCEL_POLL_SECONDS,
    _EVENT_BATCH_MAX_CHUNKS,
    _EVENT_BATCH_FLUSH_SECONDS,
    runtime_worker_id,
    _json_safe,
    serialize_assistant_chunk,
    deserialize_assistant_chunk,
    _BroadcasterToolCallController,
    RunBroadcaster,
    RunStatus,
    RunCapacityExceeded,
    ActiveRun,
 )

class _ActiveRunRegistryMethods2:
    async def shutdown(self) -> None:
        """进程关闭时取消所有进行中的后台 task (lifespan 调用)。"""
        self._shutting_down = True
        async with self._lock:
            runs = list(self._runs.values())
            cleanup_handles = list(self._cleanup_handles)
            for handle in cleanup_handles:
                handle.cancel()
            self._cleanup_handles.clear()
            cleanup_tasks = list(self._cleanup_tasks)
        if self._database is not None:
            for run in runs:
                if run.is_running:
                    try:
                        await asyncio.to_thread(
                            self._database.release_agent_run_lease,
                            run.run_id,
                            worker_id=self._worker_id,
                        )
                    except Exception:
                        logger.exception(
                            "[AgentRun] failed to release lease run_id=%s",
                            run.run_id,
                        )
        for run in runs:
            if run.task is not None and not run.task.done():
                run.cancel_reason = "restart"
                run.task.cancel()
            if run.lease_task is not None and not run.lease_task.done():
                run.lease_task.cancel()
        for run in runs:
            if run.task is None:
                continue
            try:
                await run.task
            except (asyncio.CancelledError, Exception):
                pass
        for task in cleanup_tasks:
            task.cancel()
        lease_tasks = [run.lease_task for run in runs if run.lease_task is not None]
        if lease_tasks:
            await asyncio.gather(*lease_tasks, return_exceptions=True)
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        self._cleanup_tasks.clear()
        self._runs.clear()
        self._shutting_down = False
