"""LangChain tool shells for the application's atomic operation registry.

Execution deliberately happens in middleware, not in these shells: middleware
adds approval, idempotency, isolation, audit records, compact observations and
state updates around every model-requested tool call.  The shells exist solely
to provide ``create_agent`` the real, complete function schemas.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from src.tools.registry import ToolRegistry


async def _middleware_owned_execution(**_arguments: Any) -> str:
    raise RuntimeError("Agent operation execution is owned by ToolExecutionMiddleware")


def build_langchain_tools(registry: ToolRegistry) -> list[StructuredTool]:
    """Expose every registered operation schema to ``create_agent``.

    This is intentionally not a retrieved subset.  ``source_operations``
    keeps the complete directory compact enough to bind all operation schemas
    without collapsing sources into a generic fallback.
    """
    tools: list[StructuredTool] = []
    for name in registry.get_tool_names():
        spec = registry.get_tool(name)
        if spec is None or spec.args_model is None:
            continue
        tools.append(
            StructuredTool.from_function(
                coroutine=_middleware_owned_execution,
                name=spec.name,
                description=spec.description,
                args_schema=spec.args_model,
            )
        )
    return tools


__all__ = ["build_langchain_tools"]
