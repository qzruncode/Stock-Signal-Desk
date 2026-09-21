# -*- coding: utf-8 -*-
"""HTTP-independent stream adapters for local and durable Agent runs."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Mapping

from assistant_stream.assistant_stream_chunk import AssistantStreamChunk
from assistant_stream.serialization.assistant_stream_response import AssistantStreamResponse
from assistant_stream.serialization.data_stream import DataStreamEncoder, StateProxyJSONEncoder

from src.agent.run_registry import (
    ActiveRun,
    deserialize_assistant_chunk,
)
from src.storage import DatabaseManager


logger = logging.getLogger(__name__)


class OrderedDataStreamEncoder(DataStreamEncoder):
    """Keep assistant-ui data parts in the same stream position as text/tools."""

    def encode_chunk(self, chunk: AssistantStreamChunk) -> str:
        if chunk.type == "data" and isinstance(getattr(chunk, "data", None), Mapping):
            envelope = chunk.data
            if envelope.get("event") == "agent_display_part":
                part = envelope.get("part")
                if isinstance(part, Mapping):
                    name = str(part.get("name") or "").strip()
                    if name:
                        payload: dict[str, Any] = {
                            "name": name,
                            "data": part.get("data"),
                        }
                        parent_id = part.get("parent_id") or part.get("parentId")
                        if parent_id:
                            payload["parentId"] = str(parent_id)
                        part_id = part.get("part_id") or part.get("partId")
                        if part_id:
                            payload["partId"] = str(part_id)
                        return (
                            "aui-data:"
                            + json.dumps(payload, ensure_ascii=False, cls=StateProxyJSONEncoder)
                            + "\n"
                        )
        return super().encode_chunk(chunk)


class OrderedDataStreamResponse(AssistantStreamResponse):
    """Data-stream response with the assistant-ui ordered data-part extension."""

    def __init__(self, stream: AsyncIterator[AssistantStreamChunk]):
        super().__init__(stream, OrderedDataStreamEncoder())

_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "partial",
        "failed",
        "cancelled",
        "blocked",
        # A LangGraph interrupt is terminal for this HTTP stream, while the
        # durable run itself remains resumable through the approval endpoint.
        "interrupted",
    }
)


async def timeline_presentation_stream(
    source: AsyncIterator[AssistantStreamChunk],
) -> AsyncIterator[AssistantStreamChunk]:
    """Keep standard assistant-stream parts in their original order.

    ``assistant-ui`` can render text and tool-call parts as one ordered
    message stream. The previous projection removed tool parts and rebuilt a
    separate top-level timeline, which made the browser lose the real
    ``plan -> tool -> next model turn`` order. Stage ``data`` chunks remain
    available as the durable execution record; they are no longer a reason to
    discard the native parts that carry ordering.
    """
    async for chunk in source:
        yield chunk


async def subscriber_stream(
    run: ActiveRun,
    queue: "asyncio.Queue | None" = None,
    *,
    replay_from: int | None = None,
) -> AsyncIterator[AssistantStreamChunk]:
    """Stream one locally owned run from an in-memory subscriber queue."""
    broadcaster = run.broadcaster
    if queue is None:
        queue = broadcaster.subscribe(replay_from=replay_from)
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        broadcaster.unsubscribe(queue)


async def durable_subscriber_stream(
    db_manager: DatabaseManager,
    run_record: Mapping[str, Any],
    *,
    replay_from: int = 0,
) -> AsyncIterator[AssistantStreamChunk]:
    """Replay ordered events across workers/restarts with adaptive polling."""
    run_id = str(run_record.get("run_id") or "")
    cursor = max(0, int(replay_from or 0))
    if not run_id:
        return
    idle_sleep_seconds = 0.05
    while True:
        batch = await asyncio.to_thread(
            db_manager.read_agent_run_event_batch,
            run_id,
            after_sequence=cursor,
            limit=1000,
        )
        if batch is None:
            break
        events = batch.get("events") or []
        for event in events:
            sequence = int(event.get("sequence") or 0)
            if sequence < cursor:
                continue
            cursor = sequence + 1
            try:
                yield deserialize_assistant_chunk(
                    str(event.get("event_type") or ""),
                    event.get("payload") or {},
                )
            except (TypeError, ValueError):
                logger.warning(
                    "[Agent] skipped invalid persisted event run_id=%s sequence=%s",
                    run_id,
                    sequence,
                    exc_info=True,
                )

        latest = batch.get("run") or {}
        event_cursor = int(latest.get("event_cursor") or 0)
        if latest.get("status") in _TERMINAL_STATUSES and cursor >= event_cursor:
            break
        if events:
            idle_sleep_seconds = 0.05
            continue
        await asyncio.sleep(idle_sleep_seconds)
        idle_sleep_seconds = min(1.0, idle_sleep_seconds * 1.7)


__all__ = [
    "durable_subscriber_stream",
    "OrderedDataStreamEncoder",
    "OrderedDataStreamResponse",
    "subscriber_stream",
    "timeline_presentation_stream",
]
