"""LangChain tools for the application's atomic operation registry.

The compiled Agent graph owns tool fan-out and invokes these tools through its
native ``ToolNode`` handler.  The run-scoped executor is still available as a
per-call adapter for application-specific result/evidence contracts, but the
middleware no longer has to bypass LangGraph's tool execution path for reads.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import json
from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool

from src.tools.registry import ToolRegistry


NATIVE_TOOL_RESULT_MARKER = "__dsa_native_tool_result__"
_NATIVE_TOOL_CONTEXT: ContextVar[tuple[Any, str] | None] = ContextVar(
    "dsa_native_tool_context",
    default=None,
)


@contextmanager
def native_tool_context(context: Any, tool_call_id: str):
    """Bridge run-scoped context into a schema-only native tool callable."""
    token = _NATIVE_TOOL_CONTEXT.set((context, str(tool_call_id)))
    try:
        yield
    finally:
        _NATIVE_TOOL_CONTEXT.reset(token)


def _native_tool_result_envelope(
    record: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> str:
    """Serialize the application result before LangChain formats tool output.

    ``StructuredTool`` formats a mapping return value itself.  If a nested
    provider value is not JSON serializable (for example a date-like scalar),
    LangChain falls back to Python's repr, while the middleware still expects
    the application envelope to be JSON.  Serializing at this boundary keeps
    the native tool contract stable and preserves non-JSON leaves as display
    strings, matching the rest of the runtime's projection policy.
    """
    return json.dumps(
        {
            NATIVE_TOOL_RESULT_MARKER: True,
            "record": record,
            "evidence": evidence,
        },
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _native_tool_coroutine(
    tool_name: str,
    registry: ToolRegistry,
) -> Callable[..., Awaitable[str]]:
    """Build one real async tool callable for LangGraph's native handler.

    Middleware sets a task-local run context immediately before it calls
    LangGraph's handler.  The application executor remains the single owner
    of validation, idempotency, isolation, retries, and evidence persistence.
    """

    async def execute(**arguments: Any) -> str:
        runtime_context = _NATIVE_TOOL_CONTEXT.get()
        if runtime_context is None:
            raise RuntimeError("agent tool runtime context is unavailable")
        context, action_id = runtime_context
        if context is None or getattr(context, "executor", None) is None:
            raise RuntimeError("agent tool runtime context is unavailable")
        if registry.get_tool(tool_name) is None:
            raise KeyError(f"Tool not found: {tool_name}")
        action_id = str(action_id or "").strip()
        if not action_id:
            raise ValueError(f"{tool_name} tool call is missing an id")
        action = {
            "action_id": action_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
        }
        record, evidence = await context.executor.execute(action, approved=False)
        return _native_tool_result_envelope(
            dict(record),
            dict(evidence) if isinstance(evidence, dict) else None,
        )

    execute.__name__ = f"execute_{tool_name}"
    return execute


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
                coroutine=_native_tool_coroutine(spec.name, registry),
                name=spec.name,
                description=spec.description,
                args_schema=spec.model_args_model(),
            )
        )
    return tools


__all__ = [
    "NATIVE_TOOL_RESULT_MARKER",
    "build_langchain_tools",
    "native_tool_context",
]
