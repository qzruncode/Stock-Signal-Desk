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


def test_timeline_projection_preserves_native_parts_and_their_order() -> None:
    async def run() -> list[object]:
        broadcaster = RunBroadcaster()
        active_run = ActiveRun(conversation_id="timeline-projection", broadcaster=broadcaster)
        queue = broadcaster.subscribe()
        broadcaster.append_text("正在整理证据")
        broadcaster.add_data({"event": "agent_stage", "stage": "execute", "status": "started"})
        tool = await broadcaster.add_tool_call("read_source", "call-1")
        tool.append_args_text('{"url":"https://example.com"}')
        tool.set_response({"success": True, "content": "large payload" * 1_000})
        broadcaster.append_text("已完成读取，下一步整理结论")
        broadcaster.mark_finished()

        collected: list[object] = []
        async for chunk in timeline_presentation_stream(subscriber_stream(active_run, queue)):
            collected.append(chunk)
        return collected

    chunks = asyncio.run(run())

    assert [type(chunk) for chunk in chunks] == [
        TextDeltaChunk,
        DataChunk,
        ToolCallBeginChunk,
        ToolCallDeltaChunk,
        ToolResultChunk,
        TextDeltaChunk,
    ]


def test_broadcaster_projects_terminal_parts_from_the_ordered_stream() -> None:
    broadcaster = RunBroadcaster(run_id="run-display")
    broadcaster.add_data({
        "event": "agent_stage",
        "stage": "model",
        "status": "started",
        "round_id": "1",
    })
    broadcaster.append_text("先确认取证范围。")
    tool = asyncio.run(broadcaster.add_tool_call(
        "read_source",
        "call-1",
        parent_id="action-1",
    ))
    tool.append_args_text('{"url":"https://example.com"}')
    tool.set_response({"success": True, "data_time": "2026-09-02"})
    broadcaster.add_data({
        "event": "agent_stage",
        "stage": "model",
        "status": "started",
        "round_id": "2",
    })
    broadcaster.append_text("已完成读取，准备整理结论。")
    broadcaster.add_data({
        "event": "agent_stage",
        "stage": "publish",
        "status": "completed",
        "round_id": "2",
    })
    broadcaster.append_text("最终结论")

    parts = broadcaster.display_parts_snapshot(final_text="最终结论")

    assert [part["type"] for part in parts] == ["text", "tool-call", "text", "text"]
    assert parts[0]["text"] == "先确认取证范围。"
    assert parts[0]["round_id"] == "1"
    assert parts[1]["tool_call_id"] == "call-1"
    assert parts[1]["tool_name"] == "read_source"
    assert parts[1]["args_text"] == '{"url":"https://example.com"}'
    assert parts[1]["parent_id"] == "action-1"
    assert parts[1]["result"]["success"] is True
    assert parts[1]["round_id"] == "1"
    assert parts[2]["round_id"] == "2"
    assert parts[3]["text"] == "最终结论"
    assert parts[3]["display_kind"] == "answer"
