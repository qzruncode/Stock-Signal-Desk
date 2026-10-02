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
from src.agent import run_registry as run_registry_module
from src.agent.run_streaming import (
    OrderedDataStreamEncoder,
    subscriber_stream,
    timeline_presentation_stream,
)


def test_review_report_stays_typed_and_complete_in_live_and_terminal_replay(monkeypatch) -> None:
    from src.agent.langgraph_runtime.events import GraphEventBridge

    monkeypatch.setattr(run_registry_module, "_DISPLAY_PARTS_MAX_BYTES", 200)
    monkeypatch.setattr(run_registry_module, "_DISPLAY_PARTS_MAX_ITEMS", 1)
    broadcaster = RunBroadcaster()
    bridge = GraphEventBridge(broadcaster, run_id="review-replay")
    content = "已交接观察。" * 600 + "核验记录末尾"
    bridge.publish_team_review_report(
        {"title": "研究交接与核验记录", "blocks": [{"section": "行情核验", "content": content}]},
        collaboration_id="review-replay",
    )
    bridge.commit_model_answer("针对用户问题的最终回答", structured_answer={
        "profile": "general", "blocks": [{"kind": "answer", "content": "针对用户问题的最终回答"}],
    })

    parts = broadcaster.display_parts_snapshot()
    report = next(part for part in parts if part.get("name") == "team-review-report")
    assert report["data"]["scope"] == "review"
    assert report["data"]["blocks"][0]["content"] == content
    answer = "".join(part.get("text", "") for part in parts if part.get("display_kind") == "answer")
    assert answer == "针对用户问题的最终回答"
    assert "已交接观察" not in answer


def test_unmatched_structured_candidate_cannot_override_terminal_answer() -> None:
    from src.agent.langgraph_runtime.events import GraphEventBridge

    run_id = "terminal-answer-rejects-unmatched-candidate"
    broadcaster = RunBroadcaster(run_id=run_id)
    bridge = GraphEventBridge(broadcaster, run_id=run_id)
    terminal_answer = "未能取得支持本次分析的有效外部数据，本轮分析已结束。"
    candidate_table = "| 指标 | 2026H1 | 是否含营业外收支 | 是否含营业外收支 |"

    bridge.commit_model_answer(
        terminal_answer,
        structured_answer={
            "profile": "research",
            "blocks": [{
                "kind": "fact",
                "presentation_type": "table",
                "content": candidate_table,
                "source_ids": [],
            }],
        },
    )

    assert terminal_answer in broadcaster.assistant_text_snapshot
    assert candidate_table not in broadcaster.assistant_text_snapshot


def test_plain_terminal_fallback_is_not_duplicated_after_progress():
    from src.agent.langgraph_runtime.events import GraphEventBridge

    broadcaster = RunBroadcaster(run_id="plain-fallback")
    bridge = GraphEventBridge(broadcaster, run_id="plain-fallback")
    progress = "本轮没有收到完整答案，执行详情已保留。"
    answer = "本轮未能生成符合要求的结构化回答。"
    bridge.stage("response_format", "failed", "empty response", user_message=progress)
    bridge.commit_model_answer(answer)
    bridge.commit_model_answer(answer)
    parts = broadcaster.display_parts_snapshot(final_text=answer)
    assert "".join(p.get("text", "") for p in parts if p.get("display_kind") == "progress").strip() == progress
    assert "".join(p.get("text", "") for p in parts if p.get("display_kind") == "answer") == answer
    assert sum(p.get("text", "").count(answer) for p in parts) == 1


def test_terminal_answer_survives_trace_byte_and_item_budgets(monkeypatch) -> None:
    monkeypatch.setattr(run_registry_module, "_DISPLAY_PARTS_MAX_BYTES", 800)
    monkeypatch.setattr(run_registry_module, "_DISPLAY_PARTS_MAX_ITEMS", 3)
    broadcaster = RunBroadcaster()
    for index in range(5):
        broadcaster.add_data({
            "event": "agent_display_part",
            "part": {"name": "team-model-projection", "data": {"text": "过程" * 300, "index": index}},
        })
    broadcaster.add_data({"event": "agent_display_part", "part": {"name": "agent-answer-boundary"}})
    broadcaster.append_text("## 结论\n保留有效结论。\n")
    broadcaster.add_data({
        "event": "agent_display_part",
        "part": {"name": "stock-chart", "data": {"chart_id": "large", "data": ["数据" * 300]}},
    })
    broadcaster.append_text("## 基本面\n" + "已核验的内容。" * 2000 + "\n## 新闻\n最后一段也必须保留。")

    parts = broadcaster.display_parts_snapshot()
    answer = "".join(item["text"] for item in parts if item.get("display_kind") == "answer")
    assert answer.startswith("## 结论\n保留有效结论。")
    assert answer.endswith("## 新闻\n最后一段也必须保留。")
    assert answer.count("已核验的内容。") == 2000
    assert len([item for item in parts if item.get("display_kind") == "answer"]) == 2


def test_terminal_snapshot_preserves_visible_narrative_before_budgeting_details(monkeypatch) -> None:
    monkeypatch.setattr(run_registry_module, "_DISPLAY_PARTS_MAX_BYTES", 800)
    monkeypatch.setattr(run_registry_module, "_DISPLAY_PARTS_MAX_ITEMS", 2)
    broadcaster = RunBroadcaster()
    tool = asyncio.run(broadcaster.add_tool_call("read_source", "call-large"))
    tool.set_response({"success": True, "rows": ["结果" * 400] * 24})
    narrative = "已经收到三个专家的完整报告。" * 120
    broadcaster.add_data({
        "event": "agent_display_part",
        "part": {
            "name": "team-model-projection",
            "part_id": "team:draft",
            "data": {
                "projection_source": "model", "scope": "coordinator",
                "phase": "aggregation", "kind": "draft", "text": narrative,
            },
        },
    })
    broadcaster.add_data({"event": "agent_display_part", "part": {"name": "agent-answer-boundary"}})
    broadcaster.append_text("最终回答")

    parts = broadcaster.display_parts_snapshot()

    assert [item.get("name") or item.get("display_kind") or item["type"] for item in parts] == ["tool-call", "team-model-projection", "answer"]
    assert parts[0]["tool_call_id"] == "call-large"
    assert parts[0]["result"]["success"] is True
    assert parts[0]["result"]["_stream_presentation"]["truncated"] is True
    assert parts[1]["part_id"] == "team:draft"
    assert parts[1]["data"]["text"] == narrative
    assert parts[2]["text"] == "最终回答"


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


def test_broadcaster_keeps_typed_stage_and_chart_parts_in_stream_order() -> None:
    broadcaster = RunBroadcaster(run_id="run-ordered-display")
    broadcaster.append_text("先确认行情数据。")
    broadcaster.add_data({
        "event": "agent_display_part",
        "part": {
            "type": "data",
            "name": "agent-stage",
            "part_id": "team:market:worker:1",
            "data": {
                "event": "agent_stage",
                "run_id": "run-ordered-display",
                "stage": "planning",
                "status": "completed",
                "summary": "行情方向已完成交接",
            },
        },
    })
    broadcaster.append_text("行情证据已经整理完成。")
    tool = asyncio.run(broadcaster.add_tool_call("read_source", "call-ordered"))
    tool.set_response({"success": True})
    broadcaster.append_text("下面展示对应的资金变化。")
    broadcaster.add_data({
        "event": "agent_display_part",
        "part": {
            "type": "data",
            "name": "stock-chart",
            "part_id": "chart-ordered",
            "data": {
                "chart_id": "chart-ordered",
                "chart_type": "line",
                "title": "资金变化",
                "series": [{"key": "close", "label": "收盘价"}],
                "data": [{"x": "2026-09-16", "close": 12.3}],
            },
        },
    })
    broadcaster.append_text("图表之后继续说明结论。")

    parts = broadcaster.display_parts_snapshot()

    assert [part["type"] for part in parts] == [
        "text",
        "data",
        "text",
        "tool-call",
        "text",
        "data",
        "text",
    ]
    assert parts[1]["name"] == "agent-stage"
    assert parts[3]["tool_call_id"] == "call-ordered"
    assert parts[5]["name"] == "stock-chart"
    assert parts[6]["text"] == "图表之后继续说明结论。"


def test_broadcaster_replays_one_model_projection_for_same_part_id() -> None:
    broadcaster = RunBroadcaster(run_id="run-single-model-projection")
    broadcaster.add_data({
        "event": "agent_display_part",
        "part": {
            "type": "data",
            "name": "team-model-projection",
            "part_id": "team-1:plan",
            "data": {
                "projection_source": "model",
                "text": "我已经拆分独立证据方向，现在开始并行核验。",
                "projection_id": "team-1:plan",
            },
        },
    })
    broadcaster.add_data({
        "event": "agent_display_part",
        "part": {
            "type": "data",
            "name": "team-model-projection",
            "part_id": "team-1:plan",
            "data": {
                "projection_source": "model",
                "text": "开始并行核验。",
                "projection_id": "team-1:plan",
            },
        },
    })

    parts = broadcaster.display_parts_snapshot()

    assert len(parts) == 1
    assert parts[0]["name"] == "team-model-projection"
    assert parts[0]["data"]["text"] == "我已经拆分独立证据方向，现在开始并行核验。"


def test_broadcaster_replays_one_direct_model_projection_for_same_part_id() -> None:
    broadcaster = RunBroadcaster(run_id="run-direct-model-projection")
    for sequence, text in enumerate(("我已经完成取证，", "我已经完成取证，正在整理答案。"), start=1):
        broadcaster.add_data({
            "event": "agent_display_part",
            "part": {
                "type": "data",
                "name": "agent-model-projection",
                "part_id": "run-direct:answer:1",
                "data": {
                    "projection_source": "model",
                    "projection_id": "run-direct:answer:1",
                    "scope": "direct",
                    "text": text,
                    "sequence": sequence,
                },
            },
        })

    parts = broadcaster.display_parts_snapshot()

    assert len(parts) == 1
    assert parts[0]["name"] == "agent-model-projection"
    assert parts[0]["data"]["text"] == "我已经完成取证，正在整理答案。"


def test_terminal_snapshot_anchors_late_chart_to_its_tool_result() -> None:
    broadcaster = RunBroadcaster(run_id="run-chart-anchor")
    broadcaster.append_text("先确认行情数据。")
    tool = asyncio.run(
        broadcaster.add_tool_call(
            "read_recent_kline",
            "call-chart-anchor",
            parent_id="action-chart-anchor",
        )
    )
    tool.set_response({"success": True})
    broadcaster.append_text("行情数据已核验，下面给出对应图表。")
    broadcaster.add_data({
        "event": "agent_display_part",
        "part": {
            "type": "data",
            "name": "stock-chart",
            "part_id": "chart-late",
            "data": {
                "chart_id": "chart-late",
                "action_id": "action-chart-anchor",
                "chart_type": "line",
                "title": "行情走势",
                "series": [{"key": "close", "label": "收盘价"}],
                "data": [{"x": "2026-09-16", "close": 12.3}],
            },
        },
    })

    parts = broadcaster.display_parts_snapshot()

    assert [part["type"] for part in parts] == [
        "text",
        "tool-call",
        "data",
        "text",
    ]
    assert parts[2]["name"] == "stock-chart"
    assert parts[2]["data"]["action_id"] == "action-chart-anchor"


def test_ordered_data_encoder_uses_assistant_ui_data_parts_without_changing_legacy_data() -> None:
    encoder = OrderedDataStreamEncoder()

    ordered = encoder.encode_chunk(DataChunk(data={
        "event": "agent_display_part",
        "part": {"type": "data", "name": "stock-chart", "data": {"chart_id": "c-1"}},
    }))
    legacy = encoder.encode_chunk(DataChunk(data={"event": "agent_stage", "stage": "publish"}))

    assert ordered.startswith("aui-data:")
    assert '"name": "stock-chart"' in ordered
    assert legacy.startswith("2:")
