from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from src.agent.langgraph_runtime.graph import DEFAULT_RESPONSE_FORMAT
from src.agent.langgraph_runtime.knowledge_research import knowledge_research_instructions
from src.agent.langgraph_runtime.agent_tools import NATIVE_TOOL_RESULT_MARKER
from src.agent.langgraph_runtime.answer_contract import STRUCTURED_OUTPUT_TOOL_NAME
from src.agent.langgraph_runtime.middleware import (
    AgentPromptMiddleware,
    ToolExecutionMiddleware,
    _recover_serialized_structured_answer,
)
from src.tools.search_knowledge_base import TOOL as SEARCH_KNOWLEDGE_BASE_TOOL


def test_search_tool_description_preserves_table_comparison_context() -> None:
    description = str(SEARCH_KNOWLEDGE_BASE_TOOL.description)
    assert "报告期/版本" in description
    assert "列口径（如本报告期、上年同期、同比增减）" in description


def test_evidence_repair_retains_the_draft_and_observations_with_an_error_receipt() -> None:
    @tool
    def search_knowledge_base(query: str) -> str:
        """Search selected report evidence."""
        return query

    original_answer = {"profile": "research", "title": "完整财报分析", "blocks": [
        {"kind": "fact", "content": "已核实的财务表格内容", "source_ids": [1]},
        {"kind": "risk", "content": "待修订的风险段", "source_ids": []},
    ]}
    messages = [
        HumanMessage(content="分析整份财报"),
        AIMessage(content="", tool_calls=[{"name": "search_knowledge_base", "args": {"query": "财报"}, "id": "kb", "type": "tool_call"}]),
        ToolMessage(content="本轮财报正文与完整表格", tool_call_id="kb", name="search_knowledge_base"),
        AIMessage(content="", tool_calls=[{"name": STRUCTURED_OUTPUT_TOOL_NAME, "args": original_answer, "id": "draft", "type": "tool_call"}]),
        ToolMessage(content="Returning structured response", tool_call_id="draft", name=STRUCTURED_OUTPUT_TOOL_NAME),
    ]
    state = {"messages": messages, "user_text": "分析整份财报", "knowledge_base_ids": ["kb-1"],
             "evidence_feedback": "只修订风险段的来源，保留财务表格。", "structured_output_required": True,
             "evidence": [], "tool_results": [], "tool_call_count": 1, "tool_call_limit": 10}
    context = SimpleNamespace(
        model=SimpleNamespace(llm_config={"model": "ai/Qwen3.8-27B"}),
        events=SimpleNamespace(begin_model_turn=lambda *a, **k: None, stage=lambda *a, **k: None, commit_model_progress=lambda *a, **k: None),
        registry=SimpleNamespace(get_tool=lambda _name: None),
        catalog=SimpleNamespace(model_context=lambda: "[]", size=1, version="test"),
        run_id="repair-run", conversation_id="repair-chat", knowledge_base_ids=("kb-1",), recovery_only_tools=frozenset(),
    )
    request = ModelRequest(model=context.model, messages=messages, tools=[search_knowledge_base],
                           response_format=DEFAULT_RESPONSE_FORMAT, state=state, runtime=SimpleNamespace(context=context))
    observed = {}

    async def handler(actual):
        observed["messages"] = actual.messages
        observed["tools"] = [tool.name for tool in actual.tools]
        return SimpleNamespace(result=[AIMessage(content="")], structured_response=None)

    asyncio.run(AgentPromptMiddleware().awrap_model_call(request, handler))
    actual = observed["messages"]
    assert actual[:-1] == messages[:-1]
    assert actual[-1].status == "error"
    assert actual[-1].tool_call_id == "draft"
    assert "只修订风险段" in actual[-1].content
    assert observed["tools"] == ["search_knowledge_base"]
    assert messages[-1].status == "success"


@pytest.mark.parametrize("planning_enabled", [False, True])
def test_selected_knowledge_base_is_an_optional_tool_without_scope_ids(planning_enabled) -> None:
    @tool
    def search_knowledge_base(query: str) -> str:
        """Search the PDF selected for this conversation."""
        return query

    @tool
    def search_web_source(query: str) -> str:
        """Search the public web."""
        return query

    class Events:
        def __init__(self) -> None:
            self.progress_calls = []

        def begin_model_turn(self, _turn: int) -> None:
            pass

        def stage(self, *_args, **_kwargs) -> None:
            pass

        def commit_model_progress(self, _text: str) -> None:
            self.progress_calls.append(_text)

    class Registry:
        def get_tool(self, _name: str):
            return SimpleNamespace(sensitive_fields=(), server_controlled_fields=())

    events = Events()
    context = SimpleNamespace(
        model=SimpleNamespace(llm_config={"model": "ai/Qwen3.8-27B"}),
        events=events,
        registry=Registry(),
        catalog=SimpleNamespace(
            model_context=lambda: json.dumps(
                [
                    {"operation": "search_knowledge_base", "description": "selected-kb-marker"},
                    {"operation": "search_web_source", "description": "unrelated-web-marker"},
                ],
                ensure_ascii=False,
            ),
            size=2,
            version="test",
        ),
        run_id="run-1",
        conversation_id="conversation-1",
        knowledge_base_ids=("kb-1",),
        recovery_only_tools=frozenset(),
    )
    state = {
        "messages": [HumanMessage(content="Level 0 和 Level 1 有什么差异？")],
        "user_text": "Level 0 和 Level 1 有什么差异？",
        "knowledge_base_ids": ["kb-1"],
        "evidence_feedback": "",
        "tool_results": [],
        "evidence": [],
        "tool_call_count": 0,
        "tool_call_limit": 10,
        "planning_enabled": planning_enabled,
        "planning_status": "executing" if planning_enabled else "not_started",
        "planning_plan": {
            "steps": [{"step_id": "step_1", "depends_on": [], "allowed_tools": ["search_knowledge_base"]}]
        },
        "planning_current_step_id": "step_1",
        "structured_output_required": True,
    }
    request = ModelRequest(
        model=context.model,
        messages=state["messages"],
        tools=[search_knowledge_base, search_web_source],
        tool_choice="auto",
        response_format=DEFAULT_RESPONSE_FORMAT,
        state=state,
        runtime=SimpleNamespace(context=context),
    )
    observed: dict[str, object] = {}

    async def handler(actual_request):
        observed["tools"] = [item.name for item in actual_request.tools]
        observed["tool_choice"] = actual_request.tool_choice
        observed["response_format"] = actual_request.response_format
        observed["model_settings"] = dict(actual_request.model_settings or {})
        observed["system_message"] = actual_request.system_message.content
        return SimpleNamespace(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_knowledge_base",
                            "args": {"query": "Level 0 Agent Level 1 Agent"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            structured_response=None,
        )

    asyncio.run(AgentPromptMiddleware().awrap_model_call(request, handler))

    assert observed["tools"] == (
        ["search_knowledge_base"]
        if planning_enabled
        else ["search_knowledge_base", "search_web_source"]
    )
    assert observed["tool_choice"] == "auto"
    assert observed["response_format"] is DEFAULT_RESPONSE_FORMAT
    assert observed["model_settings"].get("_stream") is None
    assert "output_config" not in observed["model_settings"]
    assert "知识库检索是当前 Agent 工具循环中的可选取证能力" in str(observed["system_message"])
    assert "范围由服务端从本轮用户选择中注入" in str(observed["system_message"])
    assert '"kb-1"' not in str(observed["system_message"])
    assert "历史回答只用于上下文，不是本轮证据" in str(observed["system_message"])
    assert "只处理最新一条用户消息" in str(observed["system_message"])
    assert "每个事实段只引用直接支持它的单条命中" in str(observed["system_message"])
    assert "段内 PDF 页码必须能在该段引用的命中中核对" in str(observed["system_message"])
    assert "列口径（如本报告期、上年同期、同比增减）" in str(observed["system_message"])
    assert "不能引用一次检索的聚合 evidence_id" in str(observed["system_message"])
    assert "不得从一方未提及某项能力反推另一方具备该能力" in str(observed["system_message"])
    assert "回答的排版形式由你根据问题决定" in str(observed["system_message"])
    assert bool(events.progress_calls) is (not planning_enabled)
    if planning_enabled:
        directory = str(observed["system_message"]).split(
            "工具来源与执行效果目录（source_params 只能使用所选来源声明的参数；"
            "需要分类或话题编号时，按目录说明调用现有目录工具取得编号）：\n",
            1,
        )[1].split("\n\n本轮可引用来源目录", 1)[0]
        operations = {item["operation"] for item in json.loads(directory)}
        assert operations == {"search_knowledge_base"}
        assert "unrelated-web-marker" not in str(observed["system_message"])


def test_plan_finalizer_prioritizes_answer_blocks_over_progress_metadata() -> None:
    user_text = "核对所选半年报中的营业收入、归母净利润和页码，简洁表格回答。"

    class Events:
        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    context = SimpleNamespace(
        model=FakeListChatModel(responses=[""]),
        events=Events(),
        registry=SimpleNamespace(get_tool=lambda _name: None),
        catalog=SimpleNamespace(model_context=lambda: "[]", size=0, version="test"),
        run_id="run-plan-finalizer",
        conversation_id="plan-finalizer",
        knowledge_base_ids=("kb-1",),
        recovery_only_tools=frozenset(),
    )
    full_hit_text = (
        "| 指标 | 本报告期 | 上年同期 | 同比 |\n|---|---:|---:|---:|\n"
        "| 营业收入 | 2,073,339,945.39 元 | 2,209,580,741.66 元 | -6.17% |\n"
        + ("正文补充。" * 100)
        + "| 经营活动现金流净额 | 363,763,010.34 元 | 96,805,794.84 元 | 275.77% |"
    )
    kb_record = {
        "action_id": "kb-search",
        "tool_name": "search_knowledge_base",
        "success": True,
        "effect": "read",
        "evidence_id": "ev_kb_search",
        "result": {
            "success": True,
            "query": "新强联 半年报 第7页 主要会计数据",
            "results": [{
                "evidence_id": "ev_kb_page7",
                "filename": "新强联半年报.pdf",
                "page_start": 7,
                "page_end": 7,
                "url": "/documents/report/content#page=7",
                "text": full_hit_text,
                "snippet": "仅供页面预览",
            }],
            "retrieval": {"dense_sparse_fusion": "internal-retrieval-marker"},
        },
    }
    state = {
        "messages": [HumanMessage(content=user_text)],
        "user_text": user_text,
        "knowledge_base_ids": ["kb-1"],
        "tool_results": [kb_record],
        "evidence": [kb_record],
        "tool_call_count": 0,
        "tool_call_limit": 10,
        "planning_enabled": True,
        "planning_status": "finalizing",
        "planning_mode": "planned",
        "planning_current_step_id": "",
        "planning_step_tool_call_ids": [],
        "planning_step_reports": [
            {
                "step_id": "step_1",
                "status": "completed",
                "completed_summary": "已核对报告指标及页码。",
                "observed_facts": ["营业收入金额、同比和页码已核实。"],
                "source_ids": [1],
            }
        ],
        "planning_plan": {
            "plan_id": "plan-finalizer",
            "revision": 1,
            "goal": "核对报告指标",
            "initial_state": "已取得报告观察",
            "constraints": [],
            "completion_criteria": ["指标和页码已核实"],
            "steps": [
                {
                    "step_id": "step_1",
                    "step_kind": "execute",
                    "objective": "检索报告指标",
                    "status": "completed",
                    "depends_on": [],
                    "allowed_tools": ["search_knowledge_base"],
                    "inputs": [],
                    "expected_observation": "报告指标及页码",
                    "completion_criteria": ["指标和页码已核实"],
                }
            ],
        },
        "planning_original_structured_output_required": True,
        "structured_output_required": True,
        "response_format_feedback": (
            "结构化输出校验失败：blocks Field required。上次遗漏了必填字段 blocks。"
            "请至少输出一个区块，把完整最终答案写入其 content；title、profile、progress_text 只是元数据，不能代替 blocks。"
            "若用户要求表格，content 中必须包含完整表格和至少一条数据行。"
        ),
    }
    request = ModelRequest(
        model=context.model,
        messages=state["messages"],
        tools=[],
        tool_choice=None,
        response_format=DEFAULT_RESPONSE_FORMAT,
        state=state,
        runtime=SimpleNamespace(context=context),
    )
    observed: dict[str, object] = {}

    async def handler(actual_request):
        observed["system_message"] = actual_request.system_message.content
        return SimpleNamespace(result=[AIMessage(content="")], structured_response=None)

    asyncio.run(AgentPromptMiddleware().awrap_model_call(request, handler))
    prompt = str(observed["system_message"])
    assert "blocks 是必填的非空数组，完整答案必须写入 blocks 项的 content" in prompt
    assert "Plan 进度已单独展示，progress_text 可以省略" in prompt
    assert "上次遗漏了必填字段 blocks" in prompt
    assert "title、profile、progress_text 只是元数据，不能代替 blocks" in prompt
    assert "363,763,010.34 元" in prompt
    assert "275.77%" in prompt
    assert "internal-retrieval-marker" not in prompt


def test_structured_answer_recovers_json_stringified_blocks_locally() -> None:
    call_id = "answer-call"
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": {
                            "profile": "general",
                            "blocks": json.dumps(
                                [{"kind": "answer", "content": "结构化结果正常。"}],
                                ensure_ascii=False,
                            ),
                        },
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="Error: blocks must be a list",
                tool_call_id=call_id,
                name=STRUCTURED_OUTPUT_TOOL_NAME,
                status="error",
            ),
        ]
    )

    recovered = _recover_serialized_structured_answer(response)

    assert recovered.structured_response is not None
    block = recovered.structured_response["blocks"][0]
    assert block["kind"] == "answer"
    assert block["content"] == "结构化结果正常。"
    receipt = recovered.result[-1]
    assert isinstance(receipt, ToolMessage)
    assert receipt.status == "success"


def test_structured_answer_recovers_duplicate_valid_calls_without_merging_candidates() -> None:
    def call(call_id: str, blocks: list[dict[str, str]]) -> dict[str, object]:
        return {
            "name": STRUCTURED_OUTPUT_TOOL_NAME,
            "args": {"profile": "general", "blocks": blocks},
            "id": call_id,
            "type": "tool_call",
        }

    full_candidate = [
        {"kind": "answer", "content": "已核对的简要结论。"},
        {"kind": "context", "content": "适用范围说明。"},
    ]
    response = ModelResponse(
        result=[
            AIMessage(content="", tool_calls=[
                call("short-answer", [{"kind": "answer", "content": "简要结论。"}]),
                call("full-answer", full_candidate),
            ])
        ]
    )

    recovered = _recover_serialized_structured_answer(response)

    assert recovered.structured_response is not None
    assert len(recovered.structured_response["blocks"]) == 2
    assert [block["content"] for block in recovered.structured_response["blocks"]] == [
        "已核对的简要结论。",
        "适用范围说明。",
    ]
    assert len(recovered.result[0].tool_calls) == 1
    assert recovered.result[0].tool_calls[0]["id"] == "full-answer"


def test_structured_answer_uses_valid_duplicate_when_other_candidate_is_malformed() -> None:
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": {"profile": "general", "blocks": [{"kind": "answer", "content": "可用候选。"}]},
                        "id": "valid-answer",
                        "type": "tool_call",
                    },
                    {
                        "name": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": {"profile": "general", "blocks": {"not": "a list"}},
                        "id": "invalid-answer",
                        "type": "tool_call",
                    },
                ],
            )
        ]
    )

    recovered = _recover_serialized_structured_answer(response)

    assert recovered.structured_response is not None
    assert recovered.structured_response["blocks"][0]["content"] == "可用候选。"
    assert [call["id"] for call in recovered.result[0].tool_calls] == ["valid-answer"]


def test_structured_answer_does_not_recover_invalid_serialized_blocks() -> None:
    response = ModelResponse(
        result=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": STRUCTURED_OUTPUT_TOOL_NAME,
                        "args": {"profile": "general", "blocks": json.dumps({"not": "a list"})},
                        "id": "bad-answer-call",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="Error: blocks must be a list",
                tool_call_id="bad-answer-call",
                name=STRUCTURED_OUTPUT_TOOL_NAME,
                status="error",
            ),
        ]
    )

    assert _recover_serialized_structured_answer(response) is response


@pytest.mark.parametrize(
    ("model_name", "expected_effort"),
    [("ai/Qwen3.8-27B", "low"), ("test-model", None)],
)
def test_finalizing_plan_omits_unbound_operation_catalog(model_name, expected_effort) -> None:
    @tool
    def search_web_source(query: str) -> str:
        """Search a public source."""
        return query

    class Events:
        def begin_model_turn(self, _turn: int) -> None:
            pass

        def stage(self, *_args, **_kwargs) -> None:
            pass

        def commit_model_progress(self, _text: str) -> None:
            pass

    class Registry:
        def get_tool(self, _name: str):
            return None

    context = SimpleNamespace(
        model=SimpleNamespace(llm_config={"model": model_name}),
        events=Events(),
        registry=Registry(),
        catalog=SimpleNamespace(
            model_context=lambda: json.dumps(
                [{"operation": "search_web_source", "description": "must-not-appear-marker"}]
            ),
            size=1,
            version="test",
        ),
        run_id="run-finalizing-plan",
        conversation_id="conversation-finalizing-plan",
        knowledge_base_ids=(),
        searchable_knowledge_bases=(),
        recovery_only_tools=frozenset(),
    )
    state = {
        "messages": [HumanMessage(content="总结已核验内容")],
        "user_text": "总结已核验内容",
        "knowledge_base_ids": [],
        "tool_results": [],
        "evidence": [],
        "planning_enabled": True,
        "planning_status": "finalizing",
        "structured_output_required": True,
        "model_turn_count": 0,
    }
    request = ModelRequest(
        model=context.model,
        messages=state["messages"],
        tools=[search_web_source],
        tool_choice="auto",
        response_format=DEFAULT_RESPONSE_FORMAT,
        state=state,
        runtime=SimpleNamespace(context=context),
    )
    observed: dict[str, str] = {}

    async def handler(actual_request):
        observed["system_message"] = actual_request.system_message.content
        observed["model_settings"] = actual_request.model_settings
        observed["tools"] = actual_request.tools
        return ModelResponse(
            result=[AIMessage(content="")],
            structured_response={"profile": "general", "blocks": [{"kind": "answer", "content": "已整理。"}]},
        )

    asyncio.run(AgentPromptMiddleware().awrap_model_call(request, handler))

    assert "工具来源与执行效果目录" not in observed["system_message"]
    assert "must-not-appear-marker" not in observed["system_message"]
    assert observed["tools"] == []
    assert observed["model_settings"]["_stream"] is False
    output_config = observed["model_settings"].get("output_config") or {}
    assert output_config.get("effort") == expected_effort


def test_model_keeps_optional_search_and_only_pdf_restricts_sources() -> None:
    @tool
    def search_knowledge_base(query: str) -> str:
        """Search the PDF selected for this conversation."""
        return query

    @tool
    def search_web_source(query: str) -> str:
        """Search the public web."""
        return query

    class Events:
        def begin_model_turn(self, _turn: int) -> None:
            pass

        def stage(self, *_args, **_kwargs) -> None:
            pass

        def commit_model_progress(self, _text: str) -> None:
            pass

    class Registry:
        def get_tool(self, _name: str):
            return SimpleNamespace(sensitive_fields=(), server_controlled_fields=())

    context = SimpleNamespace(
        model=FakeListChatModel(responses=[""]),
        events=Events(),
        registry=Registry(),
        catalog=SimpleNamespace(model_context=lambda: "", size=2, version="test"),
        run_id="run-1",
        conversation_id="conversation-1",
        knowledge_base_ids=("kb-1",),
        recovery_only_tools=frozenset(),
    )

    async def call_middleware(
        search_count: int,
        *,
        search_required: bool,
        user_text: str = "Level 0 和 Level 1 有什么差异？",
        knowledge_base_selected: bool = True,
    ) -> tuple[list[str], str, list[str], object]:
        context.knowledge_base_ids = ("kb-1",) if knowledge_base_selected else ()
        state = {
            "messages": [
                HumanMessage(content="Earlier request: list the financial report figures."),
                AIMessage(content="Earlier answer: report revenue and page 51."),
                HumanMessage(content=user_text),
            ],
            "user_text": user_text,
            "knowledge_base_ids": ["kb-1"] if knowledge_base_selected else [],
            "evidence_feedback": "请核对答案中 PDF 页码引用。" if search_required else "",
            "tool_results": [
                {"tool_name": "search_knowledge_base", "success": True}
                for _ in range(search_count)
            ],
            "evidence": [],
            "tool_call_count": 0,
            "tool_call_limit": 10,
            "planning_enabled": False,
            "planning_status": "not_started",
            "structured_output_required": False,
        }
        request = ModelRequest(
            model=context.model,
            messages=state["messages"],
            tools=[search_knowledge_base, search_web_source],
            state=state,
            runtime=SimpleNamespace(context=context),
        )
        observed: dict[str, object] = {}

        async def handler(actual_request):
            observed["tools"] = [item.name for item in actual_request.tools]
            observed["system_message"] = str(actual_request.system_message.content)
            observed["messages"] = [str(message.content) for message in actual_request.messages]
            observed["tool_choice"] = actual_request.tool_choice
            return SimpleNamespace(result=[AIMessage(content="已检索")], structured_response=None)

        await AgentPromptMiddleware().awrap_model_call(request, handler)
        return (
            list(observed["tools"]),
            str(observed["system_message"]),
            list(observed["messages"]),
            observed["tool_choice"],
        )

    async def scenario() -> None:
        # A search result does not end the model's research loop. The model
        # may refine the PDF query or choose another source for an evidence gap.
        first_tools, first_prompt, _messages, first_choice = await call_middleware(0, search_required=False)
        assert first_tools == ["search_knowledge_base", "search_web_source"]
        assert "知识库检索是当前 Agent 工具循环中的可选取证能力" in first_prompt
        assert "首轮必须" not in first_prompt
        assert "skip_knowledge_base" not in first_prompt
        assert "实体、报告期/版本、指标/概念和口径" in first_prompt
        assert "列口径（如本报告期、上年同期、同比增减）" in first_prompt
        assert "多个所求事实可能位于同一表格或章节时" in first_prompt
        assert "避免重复等价 query" in first_prompt
        assert "停止检索并作答" in first_prompt
        assert first_choice != "required"
        assert (await call_middleware(1, search_required=False))[0] == [
            "search_knowledge_base", "search_web_source"
        ]
        allowed, _prompt, _messages, choice = await call_middleware(1, search_required=True)
        assert allowed == ["search_knowledge_base", "search_web_source"]
        assert choice != "required"
        assert (await call_middleware(2, search_required=True))[0] == [
            "search_knowledge_base", "search_web_source"
        ]
        assert (await call_middleware(3, search_required=False))[0] == [
            "search_knowledge_base", "search_web_source"
        ]

        only_pdf_tools, only_pdf_prompt, _messages, only_pdf_choice = await call_middleware(
            0,
            search_required=False,
            user_text="只依据已选 PDF，说明第 7 页的结论。",
        )
        assert only_pdf_tools == ["search_knowledge_base"]
        assert only_pdf_choice != "required"
        assert "不得调用其他来源" in only_pdf_prompt

        mixed_tools = (await call_middleware(
            0,
            search_required=False,
            user_text="根据所选 PDF 的财务数据，结合今日股价分析。",
        ))[0]
        assert mixed_tools == ["search_knowledge_base", "search_web_source"]

        no_kb_tools, no_kb_prompt, _messages, _choice = await call_middleware(
            0,
            search_required=False,
            knowledge_base_selected=False,
        )
        assert no_kb_tools == ["search_web_source"]
        assert "知识库检索是当前 Agent 工具循环中的可选取证能力" not in no_kb_prompt

    asyncio.run(scenario())


def test_pdf_only_source_constraint_is_enforced_at_tool_execution_boundary() -> None:
    class Events:
        def __init__(self) -> None:
            self.stages: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def stage(self, *args, **kwargs) -> None:
            self.stages.append((args, kwargs))

    class Registry:
        def get_tool(self, _name: str):
            return SimpleNamespace(sensitive_fields=(), server_controlled_fields=())

    events = Events()
    context = SimpleNamespace(events=events, registry=Registry())
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        state={
            "knowledge_base_ids": ["kb-1"],
            "user_text": "只依据已选 PDF 回答，不要使用其他工具。",
        },
        tool_call={
            "id": "call-stock-search",
            "name": "search_stocks",
            "args": {"keyword": "上海天气"},
        },
    )
    handler_called = False

    async def handler(_request):
        nonlocal handler_called
        handler_called = True
        raise AssertionError("the forbidden tool must not reach its executor")

    async def scenario() -> None:
        result = await ToolExecutionMiddleware().awrap_tool_call(request, handler)
        assert handler_called is False
        assert result.update["tool_results"][0]["error_code"] == "knowledge_base_only_restriction"

    asyncio.run(scenario())


def test_unselected_knowledge_search_is_rejected_at_execution_boundary() -> None:
    class Events:
        def __init__(self) -> None:
            self.stages: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def stage(self, *args, **kwargs) -> None:
            self.stages.append((args, kwargs))

    class Registry:
        def get_tool(self, _name: str):
            return SimpleNamespace(sensitive_fields=(), server_controlled_fields=())

    events = Events()
    context = SimpleNamespace(events=events, registry=Registry())
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        state={"knowledge_base_ids": [], "user_text": "总结今天的市场变化。"},
        tool_call={
            "id": "call-unselected-kb",
            "name": "search_knowledge_base",
            "args": {"query": "市场变化"},
        },
    )
    handler_called = False

    async def handler(_request):
        nonlocal handler_called
        handler_called = True
        raise AssertionError("search must not execute without server-injected scope")

    async def scenario() -> None:
        result = await ToolExecutionMiddleware().awrap_tool_call(request, handler)
        assert handler_called is False
        assert result.update["tool_results"][0]["error_code"] == "knowledge_base_scope_missing"

    asyncio.run(scenario())


def test_selected_knowledge_base_does_not_gate_other_tools() -> None:
    class Events:
        def stage(self, *_args, **_kwargs) -> None:
            pass

    class Registry:
        def get_tool(self, _name: str):
            return SimpleNamespace(sensitive_fields=(), server_controlled_fields=())

        def normalize_arguments(self, _name: str, arguments: dict[str, object]):
            return arguments

        def effect_for(self, _name: str, _arguments: dict[str, object]):
            return "read"

    context = SimpleNamespace(events=Events(), registry=Registry(), knowledge_base_ids=("kb-a",))
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        state={"knowledge_base_ids": ["kb-a"], "user_text": "比较报告中的营收和市场情况", "messages": []},
        tool_call={"id": "call-market", "name": "search_stocks", "args": {"keyword": "新强联"}},
    )
    called = False

    async def handler(actual_request):
        nonlocal called
        called = True
        call = actual_request.tool_call
        record = {
            "id": call["id"],
            "action_id": call["id"],
            "tool_name": call["name"],
            "success": True,
            "effect": "read",
            "result": {"success": True, "results": [{"symbol": "300850"}]},
        }
        return SimpleNamespace(
            content=json.dumps({NATIVE_TOOL_RESULT_MARKER: True, "record": record, "evidence": None})
        )

    async def scenario() -> None:
        result = await ToolExecutionMiddleware().awrap_tool_call(request, handler)
        assert called is True
        assert result.update["tool_results"][0]["success"] is True

    asyncio.run(scenario())


def test_selected_knowledge_search_can_run_alongside_another_tool_in_the_same_turn() -> None:
    class Events:
        def stage(self, *_args, **_kwargs) -> None:
            pass

    class Registry:
        def get_tool(self, _name: str):
            return SimpleNamespace(sensitive_fields=(), server_controlled_fields=())

        def normalize_arguments(self, _name: str, arguments: dict[str, object]):
            return arguments

        def effect_for(self, _name: str, _arguments: dict[str, object]):
            return "read"

    calls = [
        {"id": "call-kb", "name": "search_knowledge_base", "args": {"query": "经营风险"}, "type": "tool_call"},
        {"id": "call-market", "name": "search_stocks", "args": {"keyword": "新强联"}, "type": "tool_call"},
    ]
    message = AIMessage(content="", tool_calls=calls)
    context = SimpleNamespace(events=Events(), registry=Registry(), knowledge_base_ids=("kb-a",))
    state = {
        "knowledge_base_ids": ["kb-a"],
        "user_text": "核实报告经营风险并结合今日行情",
        "messages": [message],
        "tool_results": [],
    }
    executed: list[str] = []

    async def handler(actual_request):
        call = actual_request.tool_call
        executed.append(str(call["name"]))
        record = {
            "id": call["id"],
            "action_id": call["id"],
            "tool_name": call["name"],
            "success": True,
            "effect": "read",
            "result": {"success": True, "results": [{"symbol": "300850"}]},
        }
        return SimpleNamespace(
            content=json.dumps({NATIVE_TOOL_RESULT_MARKER: True, "record": record, "evidence": None})
        )

    async def scenario() -> None:
        for call in calls:
            request = SimpleNamespace(
                runtime=SimpleNamespace(context=context),
                state=state,
                tool_call=call,
            )
            result = await ToolExecutionMiddleware().awrap_tool_call(request, handler)
            assert result.update["tool_results"][0]["success"] is True
        assert executed == ["search_knowledge_base", "search_stocks"]

    asyncio.run(scenario())


def test_unselected_knowledge_search_is_not_exposed_from_a_catalog() -> None:
    @tool
    def search_knowledge_base(query: str) -> str:
        """Search the knowledge base selected for this conversation."""
        return query

    @tool
    def search_web_source(query: str) -> str:
        """Search the public web."""
        return query

    class Events:
        def begin_model_turn(self, _turn: int) -> None:
            pass

        def stage(self, *_args, **_kwargs) -> None:
            pass

        def commit_model_progress(self, _text: str) -> None:
            pass

    context = SimpleNamespace(
        model=FakeListChatModel(responses=[""]),
        events=Events(),
        registry=SimpleNamespace(get_tool=lambda _name: None),
        catalog=SimpleNamespace(model_context=lambda: "[]", size=2, version="test"),
        run_id="run-kb-catalog",
        conversation_id="conversation-kb-catalog",
        knowledge_base_ids=(),
        recovery_only_tools=frozenset(),
    )
    state = {
        "messages": [HumanMessage(content="总结年报中的风险")],
        "user_text": "总结年报中的风险",
        "knowledge_base_ids": [],
        "tool_results": [],
        "evidence": [],
        "planning_enabled": False,
        "planning_status": "not_started",
        "structured_output_required": False,
    }
    request = ModelRequest(
        model=context.model,
        messages=state["messages"],
        tools=[search_knowledge_base, search_web_source],
        state=state,
        runtime=SimpleNamespace(context=context),
    )
    observed: dict[str, object] = {}

    async def handler(actual_request):
        observed["tools"] = [item.name for item in actual_request.tools]
        observed["system"] = str(actual_request.system_message.content)
        return SimpleNamespace(result=[AIMessage(content="done")], structured_response=None)

    async def scenario() -> None:
        await AgentPromptMiddleware().awrap_model_call(request, handler)

    asyncio.run(scenario())
    assert observed["tools"] == ["search_web_source"]
    assert '"id":"kb-a"' not in str(observed["system"])
