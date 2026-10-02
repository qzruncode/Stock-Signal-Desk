"""Goal narration through the real model adapter, ordered stream and replay."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from api.v1.endpoints.agent.conversations import _execution_trace_for_run
from src.agent.langgraph_runtime.events import GraphEventBridge
from src.agent.langgraph_runtime.goal.contracts import GoalAction, GoalAssessment, GoalContract, GoalFinalAnswer
from src.agent.langgraph_runtime.goal.graph import _goal_action_select, _goal_monitor, _structured_call
from src.agent.langgraph_runtime.model import GuardedAnthropicChatModel, GuardedModelGateway
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.run_registry import RunBroadcaster, serialize_assistant_chunk
from src.agent.terminal_publisher import AgentTerminalPublisher
from tests.test_agent_goal import _Events, _SequenceStructuredModel, _goal_state
from tests.test_langgraph_agent_runtime import FakeAtomicExecutor, _registry, _search_operation


def _streaming_model(responses, *, monkeypatch, after_chunk=None):
    responses = iter(responses)

    async def provider_stream(self, messages, stop=None, run_manager=None, **kwargs):
        value = next(responses)
        name = type(value).__name__
        assert kwargs["tool_choice"]["name"] == name
        payload = json.dumps(value.model_dump(mode="json"), ensure_ascii=False)

        for offset in range(0, len(payload), 24):
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[{
                "index": 0,
                **({"id": f"call-{name}", "name": name} if offset == 0 else {}),
                "args": payload[offset:offset + 24],
            }]))
            if after_chunk:
                after_chunk(offset, payload)

    monkeypatch.setattr(ChatAnthropic, "_astream", provider_stream)
    gateway = GuardedModelGateway(
        llm_config={"model": "test-model"}, database=None,
        run_id="goal-projection", worker_id="test",
    )
    return GuardedAnthropicChatModel(gateway=gateway, llm_config={"model": "test-model"})


def test_goal_intake_narrates_before_structured_call_finishes(monkeypatch):
    async def scenario():
        contract = GoalContract(objective="核对名称", progress_text="我先帮你确认名称对应的对象，再核对它的公开信息。")
        broadcaster = RunBroadcaster()
        seen_before_parse = []

        def after_chunk(offset, payload):
            if offset + 24 < len(payload) and broadcaster.display_parts_snapshot():
                seen_before_parse.append(offset)

        context = SimpleNamespace(
            events=GraphEventBridge(broadcaster, run_id="goal-projection"),
            model=_streaming_model([contract], monkeypatch=monkeypatch, after_chunk=after_chunk),
        )
        await _structured_call(
            context, type(contract), [], projection_phase="test",
            projection_kind=type(contract).__name__, projection_id="goal-projection:1",
        )
        assert seen_before_parse, "Narration must arrive before the contract finishes"
        parts = broadcaster.display_parts_snapshot()
        assert len(parts) == 1
        assert parts[0]["data"]["text"] == contract.progress_text
        assert parts[0]["data"]["scope"] == "goal"

    asyncio.run(scenario())


def test_goal_progress_projection_deduplicates_near_duplicate_narration():
    async def scenario():
        broadcaster = RunBroadcaster()
        events = GraphEventBridge(broadcaster, run_id="goal-dedupe")
        events.publish_goal_progress_projection(
            "这条记录目前只在一个公开来源中出现，我会再核对另一份独立索引。",
            phase="goal_action_select", kind="GoalAction", projection_id="goal-dedupe:1",
        )
        events.publish_goal_progress_projection(
            "这条记录目前只在一个公开来源中出现，我会再核对另一份独立索引。",
            phase="goal_action_select", kind="GoalAction", projection_id="goal-dedupe:2",
        )
        assert len(broadcaster.display_parts_snapshot()) == 1

    asyncio.run(scenario())


def test_action_progress_is_published_only_after_registry_validation():
    async def scenario():
        broadcaster = RunBroadcaster()
        model = _SequenceStructuredModel([
            GoalAction(
                kind="tool", action_id="invalid", tool_name="search_source",
                arguments={"query": "公告"}, criterion_ids=["criterion-1"],
                progress_text="我已经开始核对这份公告。",
            ),
            GoalAction(
                kind="tool", action_id="valid", tool_name="search_source",
                arguments={"source_id": "primary", "query": "公告"},
                criterion_ids=["criterion-1"],
                progress_text="我先查原始发布渠道，核对这份公告的主体和日期。",
            ),
        ])
        context = SimpleNamespace(
            run_id="goal-validated-projection",
            conversation_id="goal-validated-projection",
            events=GraphEventBridge(broadcaster, run_id="goal-validated-projection"),
            model=model,
            registry=_registry(_search_operation()),
            catalog=SimpleNamespace(compact_catalog=lambda: [{"operation": "search_source"}]),
        )
        result = await _goal_action_select(
            _goal_state(),
            SimpleNamespace(context=context),
        )

        parts = broadcaster.display_parts_snapshot()
        assert result["goal_action"]["action_id"] == "valid"
        assert len(parts) == 1
        assert parts[0]["data"]["text"] == "我先查原始发布渠道，核对这份公告的主体和日期。"

    asyncio.run(scenario())


def test_goal_monitor_never_streams_a_rejected_completion_claim(monkeypatch):
    async def scenario():
        broadcaster = RunBroadcaster()
        context = SimpleNamespace(
            run_id="goal-rejected",
            events=GraphEventBridge(broadcaster, run_id="goal-rejected"),
            model=_streaming_model([GoalAssessment(
                status="completed", progress_text="已全部确认，可以交付了。",
                criteria=[{"criterion_id": "criterion-1", "status": "satisfied", "evidence_ids": ["invented"]}],
            )], monkeypatch=monkeypatch),
        )
        update = await _goal_monitor({**_goal_state(), "model_turn_count": 0}, SimpleNamespace(context=context))
        assert update["goal_status"] == "running"
        assert broadcaster.display_parts_snapshot() == []
        assert "已全部确认" not in update["goal_progress"]

    asyncio.run(scenario())


def test_goal_narration_tool_failure_retry_and_answer_preserve_native_order(monkeypatch):
    async def scenario():
        broadcaster = RunBroadcaster()

        class Executor(FakeAtomicExecutor):
            async def execute(self, action, *, approved=False):
                tool = await broadcaster.add_tool_call(action["tool_name"], action["action_id"])
                tool.append_args_text(json.dumps(action["arguments"]))
                record, evidence = await super().execute(action, approved=approved)
                tool.set_response(record, is_error=not record["success"])
                return record, evidence

        model = _streaming_model([
            GoalContract(
                objective="核对公开记录", scope="测试对象", constraints=["只读"],
                progress_text="我来帮你核对这条公开记录，先确认来源是否可用。",
                success_criteria=[{"criterion_id": "criterion-1", "description": "有实际记录", "verification_method": "tool_result"}],
            ),
            GoalAction(kind="tool", action_id="first", tool_name="search_source", arguments={"source_id": "primary", "query": "记录"},
                       criterion_ids=["criterion-1"],
                       progress_text="我先查原始发布渠道。"),
            GoalAssessment(status="replan", progress_text="这个来源暂时没有返回记录，我换个渠道核对。"),
            GoalAction(kind="tool", action_id="second", tool_name="search_source", arguments={"source_id": "secondary", "query": "记录 备用"},
                       criterion_ids=["criterion-1"],
                       progress_text="这个来源暂时没有返回记录，我换另一份公开索引核对。"),
            GoalAssessment(
                status="completed", progress_text="找到了，对象和发布时间都能对应上。",
                criteria=[{"criterion_id": "criterion-1", "status": "satisfied", "evidence_ids": ["ev_second"]}],
            ),
            GoalFinalAnswer(answer="已经核对，这条记录有效。"),
        ], monkeypatch=monkeypatch)
        manager = LangGraphRuntimeManager(registry=_registry(_search_operation()))
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "核对公开记录"}], user_text="核对公开记录",
                system_prompt="", llm_config={}, database=None, controller=broadcaster,
                run_id="goal-ordered", conversation_id="goal-ordered", run_attempt=1,
                tenant_id="test", owner_id="test", model=model,
                executor=Executor({"search_source": [{"success": False}, {"success": True}]}),
                agent_mode="goal",
            )
            assert result.status == "completed"
            parts = broadcaster.display_parts_snapshot(final_text=result.final_text)
            assert [part["type"] for part in parts] == ["data", "data", "tool-call", "data", "tool-call", "text"]
            assert [part["data"]["text"] for part in parts if part["type"] == "data"] == [
                "我来帮你核对这条公开记录，先确认来源是否可用。", "我先查原始发布渠道。",
                "这个来源暂时没有返回记录，我换另一份公开索引核对。",
            ]
            assert parts[2]["is_error"] is True
            assert parts[-1]["display_kind"] == "answer"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_interrupted_goal_replays_prose_and_resume_keeps_the_prefix_once():
    async def scenario():
        events = []

        def persist(_run_id, sequence, chunk):
            payload = serialize_assistant_chunk(chunk)
            events.append({"sequence": sequence, "event_type": payload["type"], "payload": payload})

        first = RunBroadcaster(run_id="paused", event_sink=persist)
        bridge = GraphEventBridge(first, run_id="paused")
        bridge.publish_model_projection(
            "需要你确认的是统计范围：只看本月，还是包含上月？", scope="goal",
            display_part_name="agent-model-projection", projection_id="paused:intake:1",
        )
        await first.drain()
        database = Mock()
        database.list_agent_run_events.return_value = events
        trace = _execution_trace_for_run(
            db_manager=database, trace={"run_id": "previous", "final_text": "上一轮的答案"}, run=None,
            durable_run={"run_id": "paused", "status": "interrupted"},
        )
        assert len(trace["display_parts"]) == 1
        assert trace["display_parts"][0]["data"]["text"].startswith("需要你确认")

        resumed = RunBroadcaster(initial_sequence=len(events))
        resumed_bridge = GraphEventBridge(resumed, run_id="paused")
        resumed_bridge.publish_model_projection(
            "收到，我只核对本月的数据。", scope="goal",
            display_part_name="agent-model-projection", projection_id="paused:action:2",
        )
        resumed_bridge.text("本月的数据已核对。")
        replay = resumed.display_parts_snapshot(persisted_events=events, final_text="本月的数据已核对。")
        assert [part["type"] for part in replay] == ["data", "data", "text"]
        assert replay[0] == trace["display_parts"][0]
        # Passing the same durable events to the original broadcaster must
        # not concatenate its already-local text/argument fragments twice.
        assert first.display_parts_snapshot(persisted_events=events) == trace["display_parts"]

        # Approval resumes do not increment the worker attempt. The event
        # cursor, not attempt > 1, tells the terminal publisher it has a prefix.
        database.commit_agent_run_terminal.return_value = True
        await AgentTerminalPublisher(
            controller=resumed, run=SimpleNamespace(run_id="paused", attempt=1),
            messages=[], request_body={}, conversation_id="paused",
            database=database, worker_id="test",
            session_service=SimpleNamespace(normalize_messages=lambda items: items),
        ).commit(status="completed", final_text="本月的数据已核对。")
        committed = database.commit_agent_run_terminal.call_args.kwargs["trace"]
        assert committed["quality_projection"]["display_parts"] == replay

    asyncio.run(scenario())
