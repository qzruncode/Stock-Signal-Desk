"""Existing-document reuse, pre-approval contracts, and resume narration."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent.langgraph_runtime.knowledge_research import selected_document_catalog
from src.agent.langgraph_runtime.planning import (
    PlanningPlan, _contract_retry_instruction, _planner_messages, normalize_plan,
)
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.agent.run_registry import ActiveRun, RunBroadcaster
from src.services import company_report_service as reports
from src.tools.base import tool_effect_approval, tool_execution_context
from src.tools import company_financial_report_import as report_tools
from tests.test_langgraph_agent_runtime import FakeAtomicExecutor, ScriptedChatModel, _registry
from tests.test_agent_planning import _planner_call, _report, _step


def _document():
    return {
        "id": "owned-document", "knowledge_base_id": "selected-library",
        "filename": "新强联2026年半年度报告.pdf", "status": "ready",
        "active_index_version_id": "active-index", "page_count": 177,
        "source": {
            "security_code": "300850", "security_name": "新强联", "exchange": "深交所",
            "announcement_id": "szse-filing", "announcement_title": "新强联：2026年半年度报告",
            "pdf_url": "https://disc.static.szse.cn/reports/2026.PDF",
            "report_period": "2026年半年度", "report_type": "semiannual",
        },
    }


def _arguments():
    source = _document()["source"]
    return {
        "company": "新强联", "report_title": source["announcement_title"],
        "candidate_id": reports._candidate_id(
            exchange="szse", company_code=source["security_code"],
            announcement_id=source["announcement_id"], title=source["announcement_title"],
            pdf_url=source["pdf_url"],
        ),
    }


def _discovery():
    arguments = _arguments()
    return {
        "success": True, "company": {"code": "300850", "name": "新强联"},
        "candidates": [{"candidate_id": arguments["candidate_id"], "title": arguments["report_title"]}],
    }


def _call(name, arguments, call_id):
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": arguments, "id": call_id, "type": "tool_call",
    }])


def test_catalog_is_owned_metadata_not_pdf_evidence_or_model_controlled_ids():
    with patch("src.services.rag_knowledge_base_service.RagKnowledgeBaseService") as service_class:
        service_class.return_value.list_documents.return_value = [_document()]
        database = object()
        catalog = selected_document_catalog(
            database, ["selected-library", "selected-library"], tenant_id="tenant", owner_id="owner",
        )
        service_class.return_value.list_documents.assert_called_once_with(
            "selected-library", tenant_id="tenant", owner_id="owner",
        )
    assert catalog["status"] == "available"
    assert catalog["documents"][0]["searchable"] is True
    assert "owned-document" not in json.dumps(catalog)
    assert "selected-library" not in json.dumps(catalog)
    assert "pdf_url" not in json.dumps(catalog)
    context = SimpleNamespace(
        knowledge_base_catalog=catalog,
        catalog=SimpleNamespace(planner_catalog=lambda: [
            {"operation": "search_knowledge_base", "description": "检索所选材料", "effect": "read"},
        ]),
    )
    prompt, payload = _planner_messages(
        {"knowledge_base_ids": ["selected-library"], "user_text": "分析下新强联2026年半年度报告"},
        context, phase="plan",
    )
    assert json.loads(payload.content)["selected_document_catalog"] == catalog
    assert "不要把重新下载或导入写成" in prompt.content
    assert "不存在必须先搜索" in prompt.content
    assert "不得把自行补充的可选字段或未知文档章节变成必须存在的硬条件" in prompt.content


def test_unavailable_inventory_is_not_an_empty_successful_inventory():
    with patch("src.services.rag_knowledge_base_service.RagKnowledgeBaseService") as service_class:
        service_class.return_value.list_documents.side_effect = RuntimeError("unavailable")
        catalog = selected_document_catalog(object(), ["library"], tenant_id="tenant", owner_id="owner")
    assert catalog == {"status": "unavailable", "documents": []}


def test_team_planner_receives_only_the_selected_document_inventory():
    from src.agent.langgraph_runtime.team.graph import _plan_messages

    catalog = {"status": "available", "documents": [{"filename": "已有财报.pdf", "searchable": True}]}
    registry = _registry(*report_tools.TOOLS)
    selected_prompt = str(_plan_messages(
        {"user_text": "分析已有财报", "knowledge_base_ids": ["selected-library"]},
        registry, document_catalog=catalog,
    )[0].content)
    assert '"selected_document_catalog":{"status":"available"' in selected_prompt
    assert "已有财报.pdf" in selected_prompt
    unselected_prompt = str(_plan_messages(
        {"user_text": "分析财报", "knowledge_base_ids": []}, registry, document_catalog=catalog,
    )[0].content)
    assert "已有财报.pdf" not in unselected_prompt


def test_replanner_sees_actual_retained_ids_and_repairs_dangling_partial_step():
    completed = {**_step("step_1", "已取得财务数据"), "status": "completed"}
    partial = {**_step("step_3", "还需补查"), "status": "running"}
    state = {"user_text": "分析财报", "planning_plan": {"steps": [completed, partial]}}
    context = SimpleNamespace(catalog=SimpleNamespace(planner_catalog=lambda: []))
    messages = _planner_messages(state, context, replan_reason="可选展望尚未命中")
    assert json.loads(messages[1].content)["completed_step_ids"] == ["step_1"]
    raw = {
        "goal": "分析财报", "initial_state": "已有部分观察", "completion_criteria": ["取得所需证据"],
        "steps": [_step("step_4", "补齐所需数据", depends_on=["step_3"])],
    }
    with pytest.raises(ValueError) as error:
        normalize_plan(raw, known_tools=["search_source"], completed_steps=[completed], revision=2)
    assert "missing=['step_3']" in str(error.value)
    assert "completed_step_ids=['step_1']" in str(error.value)
    repair = _contract_retry_instruction(PlanningPlan, {"message": str(error.value)})
    assert "未完成的旧步骤不会自动保留" in repair
    assert "同时返回该步骤的剩余工作定义" in repair
    repaired = normalize_plan(
        {**raw, "steps": [partial, *raw["steps"]]}, known_tools=["search_source"],
        completed_steps=[completed], revision=2,
    )
    assert [step["step_id"] for step in repaired["steps"]] == ["step_1", "step_3", "step_4"]
    assert repaired["steps"][1]["status"] == "pending"
    assert repaired["completion_criteria"] == raw["completion_criteria"]


@pytest.mark.parametrize("change", ["company", "candidate_id", "report_title", "filename_only"])
def test_reuse_requires_exact_persisted_official_identity(change):
    arguments = _arguments()
    document = _document()
    if change == "filename_only":
        document["source"] = None
    else:
        arguments[change] = {"company": "另一家公司", "candidate_id": "report_" + "f" * 32,
                             "report_title": "新强联：2025年年度报告"}[change]
    service = MagicMock()
    service.list_documents.return_value = [document]
    assert reports.existing_company_financial_report(
        company_query=arguments["company"], candidate_id=arguments["candidate_id"],
        report_title=arguments["report_title"], knowledge_base_id="selected-library",
        tenant_id="tenant", owner_id="owner", kb_service=service,
    ) is None


def test_existing_original_reuses_before_external_lookup_or_download():
    with (
        patch.object(reports, "RagKnowledgeBaseService") as service_class,
        patch.object(reports, "_resolve_company") as company_lookup,
        patch.object(reports, "_stream_official_pdf") as downloader,
    ):
        service_class.return_value.list_documents.return_value = [_document()]
        arguments = _arguments()
        result = reports.import_company_financial_report(
            company_query=arguments["company"], candidate_id=arguments["candidate_id"],
            report_title=arguments["report_title"], knowledge_base_id="selected-library",
            tenant_id="tenant", owner_id="owner",
        )
        company_lookup.assert_not_called()
        downloader.assert_not_called()
        service_class.return_value.upload_pdf_path.assert_not_called()
        service_class.return_value.set_document_source.assert_not_called()
    assert result["read_only_reuse"] is True
    assert result["downloaded_bytes"] == 0
    assert result["searchable"] is True


def test_search_explicitly_reports_existing_original_and_read_only_reuse_needs_no_approval():
    with (
        tool_execution_context(knowledge_base_ids=["selected-library"]),
        tool_effect_approval(False),
        patch.object(report_tools, "_find_reports", return_value=_discovery()),
        patch.object(report_tools, "_active_selected_knowledge_bases", return_value=[{
            "id": "selected-library", "name": "财报库", "status": "active",
        }]),
        patch.object(report_tools, "_existing_report", return_value=_document()),
        patch.object(report_tools, "_import_report") as importer,
    ):
        discovery = report_tools.search_company_financial_reports("新强联")
        assert discovery["candidates"][0]["already_in_knowledge_base"] is True
        assert discovery["candidates"][0]["knowledge_base_document"]["searchable"] is True
        assert report_tools.TOOLS[1].effect_for(_arguments()) == "read"
        result = report_tools.import_company_financial_report(**_arguments())
        importer.assert_not_called()
    assert result["read_only_reuse"] is True


@pytest.mark.parametrize("candidate_id", [
    "report_ee47c3a3059b6adf889a00000000000000",  # Actual incident's malformed key.
    "report_" + "f" * 32,  # Well-formed, but not a discovered candidate.
])
def test_invalid_import_is_rejected_before_native_approval_or_executor(candidate_id):
    async def scenario():
        executor = FakeAtomicExecutor()
        broadcaster = RunBroadcaster()
        manager = LangGraphRuntimeManager(registry=_registry(report_tools.TOOLS[1]), response_format=None)
        await manager.start(testing=True)
        try:
            arguments = {**_arguments(), "candidate_id": candidate_id}
            result = await manager.run_new(
                messages=[{"role": "user", "content": "导入财报"}], user_text="导入财报", system_prompt="",
                llm_config={}, database=None, controller=broadcaster,
                run_id="invalid-import", conversation_id="invalid-import", run_attempt=1,
                tenant_id="tenant", owner_id="owner", knowledge_base_ids=["selected-library"],
                agent_mode="direct", executor=executor,
                model=ScriptedChatModel(responses=[
                    _call("import_company_financial_report", arguments, "invalid-import-call"),
                    AIMessage(content="参数错误，没有导入。"),
                ]),
            )
            assert result.pending_interrupt is None
            assert executor.calls == []
            assert not any(stage["stage"] == "approval" for stage in broadcaster.stage_history_snapshot())
            assert any(isinstance(message, ToolMessage) and message.status == "error"
                       for message in result.state["messages"])
        finally:
            await manager.close()
    with patch.object(report_tools, "_existing_report", return_value=None):
        asyncio.run(scenario())


def test_new_original_still_requires_approval_and_resume_narrates_it_only_once():
    async def scenario():
        executor = FakeAtomicExecutor(outcomes={
            "search_company_financial_reports": [{"result": _discovery()}],
        })
        broadcaster = RunBroadcaster()
        manager = LangGraphRuntimeManager(registry=_registry(*report_tools.TOOLS), response_format=None)
        await manager.start(testing=True)
        common = {
            "llm_config": {}, "database": None, "controller": broadcaster,
            "run_id": "approve-once-report", "conversation_id": "approve-once-report", "run_attempt": 1,
            "tenant_id": "tenant", "owner_id": "owner", "executor": executor,
        }
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "下载并导入财报"}],
                user_text="下载并导入财报", system_prompt="", agent_mode="direct",
                knowledge_base_ids=["selected-library"], **common,
                model=ScriptedChatModel(responses=[
                    _call("search_company_financial_reports", {"company": "新强联"}, "discovery-call"),
                    _call("import_company_financial_report", _arguments(), "real-import-call"),
                ]),
            )
            assert result.status == "interrupted"
            assert len(executor.calls) == 1
            pending = result.pending_interrupt
            await manager.resume(
                interrupt_id=pending["interrupt_id"],
                decision={"decision": "approve", "fingerprint": pending["fingerprint"]},
                model=ScriptedChatModel(responses=[AIMessage(
                    content="已找到新强联的半年度报告候选【证据 ev_discovery-call】。导入操作结果已经返回。",
                )]), **common,
            )
            assert len(executor.calls) == 2
            assert executor.calls[1]["approved"] is True
            stages = [stage for stage in broadcaster.stage_history_snapshot() if stage["stage"] == "approval"]
            assert [stage["status"] for stage in stages] == ["started", "completed"]
            assert broadcaster.assistant_text_snapshot.count("我会先等你确认后再继续") == 1
            assert broadcaster.assistant_text_snapshot.count("你已确认这项操作") == 1
        finally:
            await manager.close()
    with patch.object(report_tools, "_existing_report", return_value=None):
        asyncio.run(scenario())


def test_native_existing_original_is_a_read_with_no_approval():
    async def scenario():
        executor = FakeAtomicExecutor(outcomes={
            "import_company_financial_report": [{"result": reports.existing_report_result(_document())}],
        })
        broadcaster = RunBroadcaster()
        manager = LangGraphRuntimeManager(registry=_registry(report_tools.TOOLS[1]), response_format=None)
        await manager.start(testing=True)
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "确认报告是否已入库"}],
                user_text="确认报告是否已入库", system_prompt="", llm_config={}, database=None,
                controller=broadcaster, run_id="reuse", conversation_id="reuse", run_attempt=1,
                tenant_id="tenant", owner_id="owner", knowledge_base_ids=["selected-library"],
                agent_mode="direct", executor=executor,
                model=ScriptedChatModel(responses=[
                    _call("import_company_financial_report", _arguments(), "reuse-call"),
                    AIMessage(content="该报告原件已经在所选知识库中，可检索【证据 ev_reuse-call】。"),
                ]),
            )
            assert result.pending_interrupt is None
            assert len(executor.calls) == 1 and executor.calls[0]["approved"] is False
            assert not any(stage["stage"] == "approval" for stage in broadcaster.stage_history_snapshot())
            assert result.state["tool_results"][0]["result"]["downloaded_bytes"] == 0
        finally:
            await manager.close()
    with patch.object(report_tools, "RagKnowledgeBaseService") as service_class:
        service_class.return_value.list_documents.return_value = [_document()]
        asyncio.run(scenario())


@pytest.mark.parametrize("already_owned", [True, False])
def test_goal_validates_real_candidates_and_reuses_owned_original_without_approval(already_owned):
    from src.agent.langgraph_runtime.goal.graph import _goal_execute
    from src.tools.base import current_tool_execution_context
    from tests.test_agent_goal import _goal_state, _Events

    executor = FakeAtomicExecutor()
    context = SimpleNamespace(
        registry=_registry(*report_tools.TOOLS), executor=executor, events=_Events(),
        run_id="goal-owned-report", conversation_id="goal-owned-report", tenant_id="tenant", owner_id="owner",
        knowledge_base_ids=("selected-library",),
    )
    state = {**_goal_state(), "tool_results": [{
        "tool_name": "search_company_financial_reports", "success": True, "result": _discovery(),
    }], "goal_action": {
        "kind": "tool", "action_id": "reuse-report", "tool_name": "import_company_financial_report",
        "arguments": _arguments(), "criterion_ids": ["criterion-1"],
    }}

    def existing(_arguments):
        scope = current_tool_execution_context()
        assert scope["knowledge_base_ids"] == "selected-library"
        assert scope["tenant_id"] == "tenant" and scope["owner_id"] == "owner"
        return _document() if already_owned else None

    with (
        patch.object(report_tools, "_existing_report", side_effect=existing),
        patch("src.agent.langgraph_runtime.goal.graph.interrupt", return_value={"decision": "approve"}) as approval,
    ):
        result = asyncio.run(_goal_execute(state, SimpleNamespace(context=context)))
    assert result["goal_action_status"] == "completed"
    assert len(executor.calls) == 1 and executor.calls[0]["approved"] is (not already_owned)
    assert approval.call_count == (0 if already_owned else 1)


def test_goal_rejects_undiscovered_candidate_before_approval():
    from src.agent.langgraph_runtime.goal.graph import _goal_execute
    from tests.test_agent_goal import _goal_state, _Events

    executor = FakeAtomicExecutor()
    context = SimpleNamespace(
        registry=_registry(*report_tools.TOOLS), executor=executor, events=_Events(),
        run_id="goal-invalid-report", conversation_id="goal-invalid-report", tenant_id="tenant", owner_id="owner",
        knowledge_base_ids=("selected-library",),
    )
    state = {**_goal_state(), "goal_action": {
        "kind": "tool", "action_id": "invalid-report", "tool_name": "import_company_financial_report",
        "arguments": {**_arguments(), "candidate_id": "report_" + "f" * 32}, "criterion_ids": ["criterion-1"],
    }}
    with (
        patch.object(report_tools, "_existing_report", return_value=None),
        patch("src.agent.langgraph_runtime.goal.graph.interrupt") as approval,
    ):
        result = asyncio.run(_goal_execute(state, SimpleNamespace(context=context)))
    assert result["goal_action_status"] == "failed"
    assert executor.calls == []
    approval.assert_not_called()


def test_plan_hands_real_candidate_parameters_to_dependent_step_before_approval():
    async def scenario():
        executor = FakeAtomicExecutor(outcomes={
            "search_company_financial_reports": [{"result": _discovery()}],
        })
        manager = LangGraphRuntimeManager(registry=_registry(*report_tools.TOOLS), response_format=None)
        await manager.start(testing=True)
        steps = [
            {**_step("find", "查找用户明确要求导入的财报"),
             "allowed_tools": ["search_company_financial_reports"]},
            {**_step("import", "按真实候选编号导入", depends_on=["find"]),
             "allowed_tools": ["import_company_financial_report"]},
        ]
        model = ScriptedChatModel(responses=[
            _planner_call("plan", steps),
            _call("search_company_financial_reports", {"company": "新强联"}, "discovery-call"),
            _report("find", ["discovery-call"]),
            _call("import_company_financial_report", _arguments(), "import-call"),
        ])
        try:
            result = await manager.run_new(
                messages=[{"role": "user", "content": "下载并导入新强联财报"}],
                user_text="下载并导入新强联财报", system_prompt="", llm_config={}, database=None,
                controller=RunBroadcaster(), run_id="plan-import", conversation_id="plan-import",
                run_attempt=1, tenant_id="tenant", owner_id="owner", knowledge_base_ids=["selected-library"],
                planning_mode="planned", agent_mode="plan", executor=executor, model=model,
            )
            assert result.interrupted
            dependent_messages = model.calls[-1]
            dependent_prompt = "\n".join(str(message.content) for message in dependent_messages)
            assert "本轮前序步骤已返回的工具观察" in dependent_prompt
            assert _arguments()["candidate_id"] in dependent_prompt
            assert _arguments()["report_title"] in dependent_prompt
            assert not any(isinstance(message, ToolMessage)
                           and message.tool_call_id == "discovery-call" for message in dependent_messages)
            assert len(executor.calls) == 1
        finally:
            await manager.close()
    with patch.object(report_tools, "_existing_report", return_value=None):
        asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["cancelled", "failed"])
def test_progress_is_not_saved_again_as_a_terminal_answer(terminal):
    from api.v1.endpoints.agent import chat_background_runner as runner

    async def scenario():
        broadcaster = RunBroadcaster()
        progress = "你已确认这项操作，我现在继续执行。\n\n导入没有成功返回。\n\n"
        broadcaster.append_text(progress)
        run = ActiveRun(conversation_id="cancel-progress", broadcaster=broadcaster, run_id="cancel-progress")
        database = MagicMock()
        database.get_agent_run.return_value = {}
        database.update_agent_run_runtime_metadata.return_value = True
        prompt_service = MagicMock()
        prompt_service.get_active_system_prompt.return_value = ("", True)
        prompt_service.get_active_system_prompt_metadata.return_value = {}
        publisher = MagicMock()
        publisher.commit = AsyncMock()
        with (
            patch("src.services.agent_prompt_service.AgentPromptService", return_value=prompt_service),
            patch.object(runner, "AgentTerminalPublisher", return_value=publisher),
            patch.object(runner, "agent_graph_runtime") as runtime,
            patch.object(runner, "active_run_registry") as registry,
        ):
            runtime.run_new = AsyncMock(side_effect=(
                asyncio.CancelledError() if terminal == "cancelled" else RuntimeError("scripted runtime failure")
            ))
            runtime.finalize_checkpoint = AsyncMock(return_value={
                "status": terminal, "answer_final": "", "answer_draft": "未核验的草稿",
            })
            registry.shutting_down = False
            registry.mark_done = AsyncMock()
            async def execute():
                await runner._execute_background_agent_run(
                    controller=broadcaster, run=run, messages=[], body={}, llm_cfg={},
                    conversation_id="cancel-progress", db_manager=database,
                    session_service=MagicMock(), tenant_id="tenant", owner_id="owner",
                )
            if terminal == "cancelled":
                with pytest.raises(asyncio.CancelledError):
                    await execute()
            else:
                await execute()
        final = publisher.commit.call_args.kwargs["final_text"]
        assert publisher.commit.call_args.kwargs["status"] == terminal
        assert progress.strip() not in final
        assert "未核验的草稿" not in final
        if terminal == "cancelled":
            assert final == "[已停止]"
        parts = broadcaster.display_parts_snapshot(final_text=final)
        assert "".join(part["text"] for part in parts if part.get("display_kind") == "progress") == progress
        assert "".join(part["text"] for part in parts if part.get("display_kind") == "answer") == final
    asyncio.run(scenario())
