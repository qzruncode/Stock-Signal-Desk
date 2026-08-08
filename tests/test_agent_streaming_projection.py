# -*- coding: utf-8 -*-
"""Presentation projection tests for the durable Agent stream."""

from __future__ import annotations

import asyncio

from assistant_stream.assistant_stream_chunk import (
    DataChunk,
    TextDeltaChunk,
    ToolCallBeginChunk,
    ToolCallDeltaChunk,
    ToolResultChunk,
)

from src.agent.run_registry import ActiveRun, RunBroadcaster
from src.agent.run_streaming import subscriber_stream, timeline_presentation_stream


def test_timeline_projection_keeps_stage_and_text_but_not_legacy_tool_parts() -> None:
    async def run() -> list[object]:
        broadcaster = RunBroadcaster()
        active_run = ActiveRun(conversation_id="timeline-projection", broadcaster=broadcaster)
        queue = broadcaster.subscribe()
        broadcaster.append_text("正在整理证据")
        broadcaster.add_data({"event": "agent_stage", "stage": "execute", "status": "started"})
        tool = await broadcaster.add_tool_call("read_source", "call-1")
        tool.append_args_text('{"url":"https://example.com"}')
        tool.set_response({"success": True, "content": "large payload" * 1_000})
        broadcaster.mark_finished()

        collected: list[object] = []
        async for chunk in timeline_presentation_stream(subscriber_stream(active_run, queue)):
            collected.append(chunk)
        return collected

    chunks = asyncio.run(run())

    assert any(isinstance(chunk, TextDeltaChunk) for chunk in chunks)
    assert any(isinstance(chunk, DataChunk) for chunk in chunks)
    assert not any(
        isinstance(chunk, (ToolCallBeginChunk, ToolCallDeltaChunk, ToolResultChunk))
        for chunk in chunks
    )
