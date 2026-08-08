"""Build the application's one generic LangChain/LangGraph Agent loop."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult

from src.tools.registry import ToolRegistry

from .agent_tools import build_langchain_tools
from .middleware import (
    AgentPromptMiddleware,
    OperationPolicyMiddleware,
    TerminalPublicationMiddleware,
    ToolExecutionMiddleware,
)
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


def build_agent_graph(*, checkpointer: Any, registry: ToolRegistry) -> Any:
    """Return the durable standard ``model → tools → model`` agent graph.

    ``create_agent`` compiles a LangGraph StateGraph with dynamic ``Send``
    fan-out for independent tool calls.  The application supplies policy via
    middleware only; it does not compile user requests into a business DAG.
    """
    return create_agent(
        model=_RuntimeModelPlaceholder(),
        tools=build_langchain_tools(registry),
        middleware=(
            AgentPromptMiddleware(),
            OperationPolicyMiddleware(),
            ToolExecutionMiddleware(),
            TerminalPublicationMiddleware(),
        ),
        state_schema=AgentState,
        context_schema=GraphContext,
        checkpointer=checkpointer,
        name="application_agent",
    )


__all__ = ["build_agent_graph"]
