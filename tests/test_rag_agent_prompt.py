from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from src.agent.langgraph_runtime.middleware import AgentPromptMiddleware, ToolExecutionMiddleware


def test_selected_pdf_forces_a_native_retrieval_only_model_turn() -> None:
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
    state = {
        "messages": [HumanMessage(content="Level 0 和 Level 1 有什么差异？")],
        "user_text": "Level 0 和 Level 1 有什么差异？",
        "knowledge_base_ids": ["kb-1"],
        "knowledge_base_search_required": True,
        "tool_results": [],
        "evidence": [],
        "tool_call_count": 0,
        "tool_call_limit": 10,
        "planning_enabled": False,
        "planning_status": "not_started",
        "structured_output_required": True,
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
        observed["tool_choice"] = actual_request.tool_choice
        observed["response_format"] = actual_request.response_format
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

    assert observed["tools"] == ["search_knowledge_base"]
    assert observed["tool_choice"] == "required"
    assert observed["response_format"] is None
    assert "当前是本轮 PDF 检索步骤" in str(observed["system_message"])
    assert "核心表述的双语关键词" in str(observed["system_message"])
    assert "旧检索" in str(observed["system_message"])
    assert "历史助手回答、阶段进度和旧检索都不是证据" in str(observed["system_message"])
    assert "不能引用一次检索的聚合 evidence_id" in str(observed["system_message"])
    assert "不得从一方未提及某项能力反推另一方具备该能力" in str(observed["system_message"])
    assert "除非用户要求表格" in str(observed["system_message"])


def test_pdf_search_is_hidden_after_initial_search_unless_server_requests_one_repair() -> None:
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
    ) -> tuple[list[str], str, list[str], object]:
        state = {
            "messages": [
                HumanMessage(content="Earlier request: list the financial report figures."),
                AIMessage(content="Earlier answer: report revenue and page 51."),
                HumanMessage(content=user_text),
            ],
            "user_text": user_text,
            "knowledge_base_ids": ["kb-1"],
            "knowledge_base_search_required": search_required,
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
        # The runtime's initial search is already in tool_results. Hide the
        # search tool after a hit; expose it only for a server-requested repair.
        assert (await call_middleware(1, search_required=False))[0] == ["search_web_source"]
        allowed, _prompt, _messages, choice = await call_middleware(1, search_required=True)
        assert allowed == ["search_knowledge_base"]
        assert choice == "required"
        assert (await call_middleware(2, search_required=True))[0] == ["search_web_source"]

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
        assert events.stages

    asyncio.run(scenario())
