"""ActiveRunRegistry method group 1."""

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

class _ActiveRunRegistryMethods1:
    def __init__(
        self,
        database: Any | None = None,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._runs: Dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()
        self._cleanup_handles: Set["asyncio.TimerHandle"] = set()
        self._cleanup_tasks: Set["asyncio.Task"] = set()
        self._total_started = 0
        self._terminal_counts: Dict[str, int] = {
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "cancelled": 0,
        }
        self._event_batches = 0
        self._event_chunks = 0
        self._event_batch_failures = 0
        self._event_write_ms_total = 0.0
        self._cache_hits = 0
        self._cache_misses = 0
        self._database = database
        self._worker_id = worker_id or runtime_worker_id()
        self._shutting_down = False
    def configure(self, database: Any) -> None:
        """Attach the application database once dependencies are initialized."""
        self._database = database
    @property
    def durable(self) -> bool:
        return self._database is not None
    @property
    def worker_id(self) -> str:
        return self._worker_id
    @property
    def shutting_down(self) -> bool:
        return self._shutting_down
    def record_execution_cache_result(self, *, hit: bool) -> None:
        if hit:
            self._cache_hits += 1
        else:
            self._cache_misses += 1
    def _persist_event(
        self,
        run_id: str,
        sequence: int,
        chunk: AssistantStreamChunk,
    ) -> None:
        if self._database is None:
            return
        payload = serialize_assistant_chunk(chunk)
        if isinstance(chunk, ReasoningDeltaChunk):
            # Reasoning is an auxiliary/private channel. Keep the ordered
            # cursor replayable, but never write model-authored hidden content
            # into the durable run record.
            payload["reasoning_delta"] = "[reasoning-redacted]"
        self._database.append_agent_run_event(
            run_id=run_id,
            sequence=sequence,
            event_type=str(payload.get("type") or type(chunk).__name__),
            payload=payload,
        )
    def _persist_events(
        self,
        run_id: str,
        start_sequence: int,
        chunks: List[AssistantStreamChunk],
    ) -> None:
        if self._database is None:
            return
        started_at = datetime.now()
        try:
            persisted = self._database.append_agent_run_events(
                run_id=run_id,
                start_sequence=start_sequence,
                events=[
                    {
                        "event_type": str(
                            (payload := serialize_assistant_chunk(chunk)).get("type") or type(chunk).__name__
                        ),
                        "payload": (
                            {
                                **payload,
                                "reasoning_delta": "[reasoning-redacted]",
                            }
                            if isinstance(chunk, ReasoningDeltaChunk)
                            else payload
                        ),
                    }
                    for chunk in chunks
                ],
            )
            if not persisted:
                raise RuntimeError(f"durable Agent run disappeared while writing events: {run_id}")
        except Exception:
            self._event_batch_failures += 1
            raise
        finally:
            self._event_write_ms_total += (datetime.now() - started_at).total_seconds() * 1000
        self._event_batches += 1
        self._event_chunks += len(chunks)
    async def _heartbeat(self, run_id: str) -> bool:
        if self._database is None:
            return False
        record = await asyncio.to_thread(
            self._database.heartbeat_agent_run,
            run_id,
            worker_id=self._worker_id,
            lease_seconds=_RUN_LEASE_SECONDS,
        )
        if record is None:
            raise RuntimeError("durable Agent run lease no longer exists")
        if record.get("worker_id") != self._worker_id:
            raise RuntimeError("durable Agent run lease is owned by another worker")
        return bool(record.get("cancel_requested"))
    async def _cancel_requested(self, run_id: str) -> bool:
        if self._database is None:
            return False
        record = await asyncio.to_thread(
            self._database.get_agent_run,
            run_id=run_id,
        )
        if record is None:
            raise RuntimeError("durable Agent run no longer exists")
        if record.get("worker_id") != self._worker_id and record.get("status") in {"queued", "running", "recovering"}:
            raise RuntimeError("durable Agent run is owned by another worker")
        return bool(record.get("cancel_requested"))
    async def start_or_get(self, conversation_id: str) -> ActiveRun:
        """若有 running run 则返回它 (首连复用),否则建一个新 run (尚未启动 task)。

        返回的 run.task 可能为 None —— 调用方需在首连接 subscribe 之后调
        ``run.start(factory)`` 启动后台生成,确保首连能收到首个 chunk。
        """
        async with self._lock:
            existing = self._runs.get(conversation_id)
            if existing is not None and existing.is_running:
                return existing
            broadcaster = RunBroadcaster()
            run = ActiveRun(
                conversation_id=conversation_id,
                broadcaster=broadcaster,
            )
            self._runs[conversation_id] = run
            self._total_started += 1
            return run
    async def try_claim(
        self,
        conversation_id: str,
        *,
        max_active_runs: Optional[int] = None,
        max_owner_active_runs: Optional[int] = None,
        run_id: str | None = None,
        request_payload: Mapping[str, Any] | None = None,
        tenant_id: str = "local",
        owner_id: str = "admin",
    ) -> Optional[ActiveRun]:
        """原子地「判定无活跃 run + 创建新 run」。

        与 ``start_or_get`` 的区别:后者在已有 running run 时复用(返回已有 run),
        本方法在已有 running run 时返回 ``None``(让调用方走 409 拒绝并发)。

        解决 ``agent_chat`` 的双请求竞态:``is_active``(无锁)与 ``start_or_get``
        (锁内)之间的窗口会让两个并发请求都通过 ``is_active`` 检查、各自落库
        messages,然后第二个请求静默 attach 到第一个 run、其 messages 被丢弃。
        用本方法把「判定 + 创建」合并进锁内,只有第一个请求能拿到新 run。
        """
        async with self._lock:
            existing = self._runs.get(conversation_id)
            if existing is not None and existing.is_running:
                return None
            active_count = sum(1 for candidate in self._runs.values() if candidate.is_running)
            claimed_run_id = run_id or uuid.uuid4().hex
            if self._database is not None:
                claim = await asyncio.to_thread(
                    self._database.claim_agent_run,
                    run_id=claimed_run_id,
                    conversation_id=conversation_id,
                    request_payload=dict(request_payload or {}),
                    worker_id=self._worker_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    lease_seconds=_RUN_LEASE_SECONDS,
                    max_active_runs=max_active_runs,
                    max_owner_active_runs=max_owner_active_runs,
                )
                if not claim.get("claimed"):
                    if claim.get("reason") in {
                        "capacity",
                        "owner_capacity",
                    }:
                        raise RunCapacityExceeded(
                            f"durable Agent {claim.get('reason')} exhausted "
                            f"({claim.get('active_count')}/"
                            f"{max_owner_active_runs if claim.get('reason') == 'owner_capacity' else max_active_runs})"
                        )
                    return None
            elif max_active_runs is not None and active_count >= max_active_runs:
                raise RunCapacityExceeded(f"active Agent run capacity exhausted ({active_count}/{max_active_runs})")

            broadcaster = RunBroadcaster(
                run_id=claimed_run_id if self._database is not None else None,
                event_batch_sink=(self._persist_events if self._database is not None else None),
            )
            run = ActiveRun(
                conversation_id=conversation_id,
                broadcaster=broadcaster,
                run_id=claimed_run_id,
                attempt=(int((claim.get("run") or {}).get("attempt") or 1) if self._database is not None else 1),
                lease_callback=((lambda: self._heartbeat(claimed_run_id)) if self._database is not None else None),
                cancel_callback=(
                    (lambda: self._cancel_requested(claimed_run_id)) if self._database is not None else None
                ),
            )
            self._runs[conversation_id] = run
            self._total_started += 1
            logger.info(
                "[AgentRun] claimed run_id=%s conversation_id=%s active=%s",
                run.run_id,
                conversation_id,
                active_count + 1,
            )
            return run
    async def adopt_recovered(
        self,
        *,
        conversation_id: str,
        run_id: str,
        event_cursor: int,
        attempt: int,
    ) -> ActiveRun:
        """Register a database-reclaimed run as locally owned execution."""
        if self._database is None:
            raise RuntimeError("durable database is not configured")
        async with self._lock:
            existing = self._runs.get(conversation_id)
            if existing is not None and existing.is_running:
                return existing
            broadcaster = RunBroadcaster(
                run_id=run_id,
                event_batch_sink=self._persist_events,
                initial_sequence=event_cursor,
            )
            run = ActiveRun(
                conversation_id=conversation_id,
                broadcaster=broadcaster,
                run_id=run_id,
                attempt=max(1, int(attempt)),
                status="running",
                lease_callback=lambda: self._heartbeat(run_id),
                cancel_callback=lambda: self._cancel_requested(run_id),
            )
            self._runs[conversation_id] = run
            self._total_started += 1
            return run
    def get(self, conversation_id: str) -> Optional[ActiveRun]:
        return self._runs.get(conversation_id)
    def is_active(self, conversation_id: str) -> bool:
        run = self._runs.get(conversation_id)
        return run is not None and run.is_running
    async def mark_done(
        self,
        conversation_id: str,
        status: RunStatus,
        final_text: Optional[str] = None,
        error: Optional[str] = None,
        *,
        persist: bool = True,
    ) -> None:
        """run 结束:置状态、通知订阅者、延迟清理注册表项。"""
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                return
            run.status = status
            run.final_text = final_text
            run.error = error
            if run.lease_task is not None and not run.lease_task.done():
                run.lease_task.cancel()

        # Event I/O may be slow and must not hold the registry lock. The
        # broadcaster publishes only committed events and closes subscribers
        # after its final batch becomes durable.
        run.broadcaster.mark_finished()
        await run.broadcaster.drain()
        if self._database is not None and persist:
            await asyncio.to_thread(
                self._database.finish_agent_run,
                run.run_id,
                status=status,
                final_text=final_text,
                error_code=error,
                error_detail=error,
            )

        async with self._lock:
            if not run.terminal_recorded:
                self._terminal_counts[status] = self._terminal_counts.get(status, 0) + 1
                run.terminal_recorded = True
            duration_ms = int((datetime.now() - run.started_at).total_seconds() * 1000)
            logger.info(
                "[AgentRun] finished run_id=%s conversation_id=%s status=%s duration_ms=%s chunks=%s",
                run.run_id,
                conversation_id,
                status,
                duration_ms,
                run.broadcaster.history_length,
            )

        # 延迟清理:让最后断开的连接仍能拿到 final chunk / None 哨兵。
        retention = _RUN_RETENTION_SECONDS

        expected_run_id = run.run_id

        async def _cleanup():
            async with self._lock:
                current = self._runs.get(conversation_id)
                # A new run may have replaced this retained terminal run.  An
                # old timer must never delete the replacement.
                if current is not None and current.run_id == expected_run_id:
                    self._runs.pop(conversation_id, None)

        def _start_cleanup() -> None:
            self._cleanup_handles.discard(cleanup_handle)
            cleanup_task = asyncio.create_task(_cleanup())
            self._cleanup_tasks.add(cleanup_task)
            cleanup_task.add_done_callback(self._cleanup_tasks.discard)

        # A timer handle does not leave a pending asyncio Task behind when a
        # short-lived request/test loop closes.  The actual coroutine is only
        # created after the retention window expires.
        cleanup_handle = asyncio.get_running_loop().call_later(retention, _start_cleanup)
        self._cleanup_handles.add(cleanup_handle)

    async def mark_interrupted(self, conversation_id: str) -> None:
        """Close the current stream while LangGraph keeps the run checkpoint."""
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                return
            run.status = "interrupted"
            if run.lease_task is not None and not run.lease_task.done():
                run.lease_task.cancel()
        run.broadcaster.mark_finished()
        await run.broadcaster.drain()
        async with self._lock:
            current = self._runs.get(conversation_id)
            if current is run:
                self._runs.pop(conversation_id, None)
        logger.info(
            "[AgentRun] waiting for approval run_id=%s conversation_id=%s",
            run.run_id,
            conversation_id,
        )
    async def cancel(self, conversation_id: str, *, remove: bool = True) -> bool:
        """显式取消一个 run,用于删除会话/用户放弃任务。

        与普通断连不同,删除会话意味着这个后台生成结果已经没有落点,
        必须取消 task,否则会继续占用模型请求并拖慢新会话。
        """
        if self._database is not None:
            durable_cancelled = await asyncio.to_thread(
                self._database.request_agent_run_cancel,
                conversation_id,
            )
        else:
            durable_cancelled = False
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None or not run.is_running:
                return durable_cancelled
            task = run.task
            run.cancel_reason = "user_cancelled"

        if task is not None and not task.done():
            task.cancel()
            # task.cancel() only schedules cancellation. Wait until the run
            # callback has persisted its partial answer before returning.
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._database is not None:
            try:
                await asyncio.shield(
                    asyncio.to_thread(
                        self._database.release_agent_resources_for_run,
                        run.run_id,
                    )
                )
            except Exception:
                # The terminal transcript has priority.  A failed cleanup is
                # still bounded by the ordinary lease expiry and must not make
                # the user's explicit cancellation look like a failed run.
                logger.warning(
                    "[AgentRun] failed to release resources for cancelled run_id=%s",
                    run.run_id,
                    exc_info=True,
                )
        current = self._runs.get(conversation_id)
        if current is run and run.is_running:
            # A run that never entered its callback still needs a terminal
            # state. Normal running callbacks commit their transcript first
            # and call mark_done themselves.
            await self.mark_done(
                conversation_id,
                "cancelled",
                final_text=run.final_text,
                error="cancelled",
            )
        if remove:
            async with self._lock:
                current = self._runs.get(conversation_id)
                if current is run:
                    self._runs.pop(conversation_id, None)
        logger.info(
            "[AgentRun] cancelled run_id=%s conversation_id=%s remove=%s",
            run.run_id,
            conversation_id,
            remove,
        )
        return True
    def stats(self) -> Dict[str, Any]:
        """Return non-sensitive process-local runtime metrics for readiness."""
        now = datetime.now()
        active = [run for run in self._runs.values() if run.is_running]
        oldest_seconds = max(
            ((now - run.started_at).total_seconds() for run in active),
            default=0.0,
        )
        stats = {
            "active_runs": len(active),
            "retained_runs": len(self._runs),
            "oldest_active_seconds": round(max(0.0, oldest_seconds), 3),
            "total_started": self._total_started,
            "terminal": dict(self._terminal_counts),
            "event_persistence": {
                "batches": self._event_batches,
                "chunks": self._event_chunks,
                "batch_failures": self._event_batch_failures,
                "average_batch_size": (
                    round(
                        self._event_chunks / self._event_batches,
                        3,
                    )
                    if self._event_batches
                    else 0.0
                ),
                "average_write_ms": (
                    round(
                        self._event_write_ms_total / self._event_batches,
                        3,
                    )
                    if self._event_batches
                    else 0.0
                ),
            },
            "execution_cache": {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_rate": (
                    round(
                        self._cache_hits / (self._cache_hits + self._cache_misses),
                        6,
                    )
                    if self._cache_hits + self._cache_misses
                    else None
                ),
            },
        }
        if self._database is not None:
            try:
                stats["durable"] = self._database.agent_runtime_metrics()
            except Exception:
                logger.debug("[AgentRun] durable metrics unavailable", exc_info=True)
        return stats
