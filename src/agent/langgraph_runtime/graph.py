"""Build the application's one generic LangChain/LangGraph Agent loop."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ContextEditingMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult

from src.tools.registry import ToolRegistry

from .agent_tools import build_langchain_tools
from .answer_contract import StructuredAgentAnswer
from .context import ContextBudgetMiddleware
from .memory import ConversationMemoryMiddleware
from .middleware import (
    AgentPromptMiddleware,
    OperationPolicyMiddleware,
    ReflectionMiddleware,
    TerminalPublicationMiddleware,
    ToolExecutionMiddleware,
)
from .planning import PlanningCoordinatorMiddleware
from .state import AgentState, GraphContext


class _RuntimeModelPlaceholder(BaseChatModel):
    """Compile-time placeholder replaced by AgentPromptMiddleware per run."""

    @property
    def _llm_type(self) -> str:
        return "runtime_model_placeholder"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("the run-scoped chat model was not injected")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("the run-scoped chat model was not injected")

    def bind_tools(self, tools: list[Any], *, tool_choice: Any | None = None, **kwargs: Any) -> Any:
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)


DEFAULT_RESPONSE_FORMAT = ToolStrategy(StructuredAgentAnswer)


def build_agent_graph(
    *,
    checkpointer: Any,
    registry: ToolRegistry,
    response_format: Any | None = DEFAULT_RESPONSE_FORMAT,
) -> Any:
    """Return the durable standard ``model → tools → model`` agent graph.

    ``create_agent`` compiles a LangGraph StateGraph with dynamic ``Send``
    fan-out for independent tool calls.  The application supplies policy via
    middleware only; it does not compile user requests into a business DAG.
    """
    return create_agent(
        model=_RuntimeModelPlaceholder(),
        tools=build_langchain_tools(registry),
        middleware=(
            ConversationMemoryMiddleware(),
            AgentPromptMiddleware(),
            ContextEditingMiddleware(token_count_method="approximate"),
            ContextBudgetMiddleware(),
            ReflectionMiddleware(),
            OperationPolicyMiddleware(),
            ToolExecutionMiddleware(),
            TerminalPublicationMiddleware(),
            PlanningCoordinatorMiddleware(),
        ),
        state_schema=AgentState,
        context_schema=GraphContext,
        checkpointer=checkpointer,
        response_format=response_format,
        name="application_agent",
    )


__all__ = ["DEFAULT_RESPONSE_FORMAT", "build_agent_graph"]
