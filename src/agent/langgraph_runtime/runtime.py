"""Lifecycle and invocation facade for the application-hosted LangGraph."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from src.agent.run_registry import active_run_registry
from src.tools.registry import ToolRegistry

from .catalog import ToolCatalog
from .events import GraphEventBridge
from .executor import AtomicToolExecutor
from .graph import build_agent_graph
from .model import StructuredModelClient
from .state import AgentGraphInput, GraphContext


CHECKPOINT_THREAD_PREFIX = "agent-v1"
CHECKPOINT_NAMESPACE = ""


def _identity_compact(_tool_name: str, result: Any) -> Any:
    return result


def _identity_fallback(_tool_name: str, _arguments: dict[str, Any], result: Any) -> Any:
    return result


def _postgres_connection_string(value: str) -> str:
    normalized = value.strip()
    for driver in (
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgresql+asyncpg://",
    ):
        if normalized.startswith(driver):
            return "postgresql://" + normalized[len(driver) :]
    return normalized


def _run_timeout_seconds() -> float:
    try:
        value = float(str(os.getenv("AGENT_GRAPH_MAX_ELAPSED_SECONDS") or "900").strip())
    except (TypeError, ValueError):
        value = 900.0
    return max(5.0, min(7_200.0, value))


def _sqlite_checkpoint_path(database_url: str) -> Path:
    configured = str(os.getenv("AGENT_CHECKPOINT_SQLITE_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    value = database_url.strip()
    if value.startswith("sqlite:///"):
        raw_path = value[len("sqlite:///") :]
        if raw_path and raw_path != ":memory:":
            source = Path(raw_path).expanduser()
            if not source.is_absolute():
                source = (Path.cwd() / source).resolve()
            return source.with_name(f"{source.stem}.langgraph{source.suffix or '.sqlite3'}")
    return (Path.cwd() / ".agent-checkpoints" / "langgraph.sqlite3").resolve()


@dataclass(frozen=True)
class GraphRunResult:
    status: str
    final_text: str
    state: dict[str, Any]
    interrupted: bool = False
    pending_interrupt: dict[str, Any] | None = None
    error_code: str | None = None


class LangGraphRuntimeManager:
    """Own one compiled graph and one native checkpointer per application."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        compact_result: Callable[[str, Any], Any] = _identity_compact,
        attach_fallback: Callable[[str, dict[str, Any], Any], Any] = _identity_fallback,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.catalog = ToolCatalog(self.registry)
        self.compact_result = compact_result
        self.attach_fallback = attach_fallback
        self.checkpointer: Any | None = None
        self.graph: Any | None = None
        self._checkpointer_context: AbstractAsyncContextManager[Any] | None = None
        self._backend = "uninitialized"

    @property
    def initialized(self) -> bool:
        return self.graph is not None

    @property
    def backend(self) -> str:
        return self._backend

    def configure_tool_projection(
        self,
        *,
        compact_result: Callable[[str, Any], Any],
        attach_fallback: Callable[[str, dict[str, Any], Any], Any],
    ) -> None:
        self.compact_result = compact_result
        self.attach_fallback = attach_fallback

    async def start(
        self,
        database: Any | None = None,
        *,
        testing: bool = False,
        checkpointer: Any | None = None,
    ) -> None:
        if self.initialized:
            return
        if checkpointer is not None:
            self.checkpointer = checkpointer
            self._backend = type(checkpointer).__name__
        elif testing:
            self.checkpointer = InMemorySaver()
            self._backend = "memory"
        else:
            configured_url = str(os.getenv("AGENT_CHECKPOINT_DATABASE_URL") or "").strip()
            database_url = configured_url or str(
                getattr(database, "_db_url", None) or os.getenv("DATABASE_URL") or ""
            ).strip()
            if database_url.startswith(("postgresql://", "postgres://", "postgresql+")):
                connection_string = _postgres_connection_string(database_url)
                context = AsyncPostgresSaver.from_conn_string(connection_string)
                self._checkpointer_context = context
                self.checkpointer = await context.__aenter__()
                await self.checkpointer.setup()
                self._backend = "postgres"
            else:
                if configured_url.startswith("sqlite:///"):
                    sqlite_path = Path(configured_url[len("sqlite:///") :]).expanduser().resolve()
                elif configured_url and "://" not in configured_url:
                    sqlite_path = Path(configured_url).expanduser().resolve()
                else:
                    sqlite_path = _sqlite_checkpoint_path(database_url)
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                context = AsyncSqliteSaver.from_conn_string(str(sqlite_path))
                self._checkpointer_context = context
                self.checkpointer = await context.__aenter__()
                await self.checkpointer.setup()
                self._backend = f"sqlite:{sqlite_path}"
        self.graph = build_agent_graph(checkpointer=self.checkpointer)

    async def close(self) -> None:
        self.graph = None
        self.checkpointer = None
        context = self._checkpointer_context
        self._checkpointer_context = None
        if context is not None:
            await context.__aexit__(None, None, None)
        self._backend = "uninitialized"

    @staticmethod
    def thread_id(conversation_id: str) -> str:
        return f"{CHECKPOINT_THREAD_PREFIX}:{conversation_id}"

    @classmethod
    def graph_config(cls, conversation_id: str) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": cls.thread_id(conversation_id),
                "checkpoint_ns": CHECKPOINT_NAMESPACE,
            },
            "recursion_limit": 100,
        }

    def _require_graph(self) -> Any:
        if self.graph is None:
            raise RuntimeError("LangGraph runtime has not been initialized")
        return self.graph

    def _context(
        self,
        *,
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
        model: Any | None = None,
        executor: Any | None = None,
    ) -> GraphContext:
        events = GraphEventBridge(controller, run_id=run_id)
        model_client = model or StructuredModelClient(
            llm_config=llm_config,
            database=database,
            run_id=run_id,
            worker_id=active_run_registry.worker_id,
        )
        tool_executor = executor or AtomicToolExecutor(
            self.registry,
            database=database,
            run_id=run_id,
            conversation_id=conversation_id,
            controller=controller,
            events=events,
            compact_result=self.compact_result,
            attach_fallback=self.attach_fallback,
        )
        return GraphContext(
            model=model_client,
            catalog=self.catalog,
            registry=self.registry,
            executor=tool_executor,
            events=events,
            database=database,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=max(1, int(run_attempt)),
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    async def _invoke_with_time_budget(
        self,
        graph: Any,
        graph_input: Any,
        *,
        conversation_id: str,
        context: GraphContext,
    ) -> Mapping[str, Any] | None:
        config = self.graph_config(conversation_id)
        try:
            async with asyncio.timeout(_run_timeout_seconds()):
                return await graph.ainvoke(graph_input, config, context=context)
        except TimeoutError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="time_budget_exceeded",
                message=(
                    "本轮执行达到时间预算，尚未完成最终 Claim-Evidence 校验。"
                    "已保留现有工具结果与证据，未验证结论未发布。"
                ),
            )
        except RuntimeError as exc:
            if "agent provider budget exceeded" not in str(exc).lower():
                raise
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="provider_budget_exceeded",
                message=(
                    "本轮模型调用、Token 或费用预算已耗尽，尚未完成最终 Claim-Evidence 校验。"
                    "已保留现有工具结果与证据，未验证结论未发布。"
                ),
            )

    async def _terminate_partial(
        self,
        graph: Any,
        *,
        config: Mapping[str, Any],
        context: GraphContext,
        error_code: str,
        message: str,
    ) -> Mapping[str, Any]:
        """Fail closed when a hard runtime budget prevents final verification."""
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values or {})
        # Only an answer that already passed verification is safe to retain as
        # visible output. An answer_draft remains checkpointed for audit but is
        # never promoted after a hard budget stop.
        answer = str(state.get("answer_final") or "").strip() or message
        update = {
            "answer_final": answer,
            "status": "partial",
            "error_code": error_code,
        }
        try:
            await graph.aupdate_state(config, update)
        except Exception:
            # The terminal publisher still receives the explicit update;
            # failure to append this final checkpoint must not turn a
            # truthful partial result into a fabricated completion.
            pass
        context.events.stage(
            "publish",
            "completed",
            message,
            error_code=error_code,
        )
        context.events.text(answer)
        return {**state, **update}

    async def run_new(
        self,
        *,
        messages: list[dict[str, Any]],
        user_text: str,
        system_prompt: str,
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
        model: Any | None = None,
        executor: Any | None = None,
    ) -> GraphRunResult:
        graph = self._require_graph()
        context = self._context(
            llm_config=llm_config,
            database=database,
            controller=controller,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=run_attempt,
            tenant_id=tenant_id,
            owner_id=owner_id,
            model=model,
            executor=executor,
        )
        input_state: AgentGraphInput = {
            "input_run_id": run_id,
            "input_conversation_id": conversation_id,
            "input_messages": [dict(item) for item in messages],
            "input_user_text": user_text,
            "input_system_prompt": system_prompt,
        }
        output = await self._invoke_with_time_budget(
            graph,
            input_state,
            conversation_id=conversation_id,
            context=context,
        )
        return await self._result(output, conversation_id=conversation_id, events=context.events)

    async def resume(
        self,
        *,
        interrupt_id: str,
        decision: Mapping[str, Any],
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
        model: Any | None = None,
        executor: Any | None = None,
    ) -> GraphRunResult:
        graph = self._require_graph()
        context = self._context(
            llm_config=llm_config,
            database=database,
            controller=controller,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=run_attempt,
            tenant_id=tenant_id,
            owner_id=owner_id,
            model=model,
            executor=executor,
        )
        output = await self._invoke_with_time_budget(
            graph,
            Command(resume={str(interrupt_id): dict(decision)}),
            conversation_id=conversation_id,
            context=context,
        )
        return await self._result(output, conversation_id=conversation_id, events=context.events)

    async def recover(
        self,
        *,
        llm_config: Mapping[str, Any],
        database: Any | None,
        controller: Any | None,
        run_id: str,
        conversation_id: str,
        run_attempt: int,
        tenant_id: str,
        owner_id: str,
    ) -> GraphRunResult:
        graph = self._require_graph()
        context = self._context(
            llm_config=llm_config,
            database=database,
            controller=controller,
            run_id=run_id,
            conversation_id=conversation_id,
            run_attempt=run_attempt,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        output = await self._invoke_with_time_budget(
            graph,
            None,
            conversation_id=conversation_id,
            context=context,
        )
        return await self._result(output, conversation_id=conversation_id, events=context.events)

    async def _result(
        self,
        output: Mapping[str, Any] | None,
        *,
        conversation_id: str,
        events: GraphEventBridge,
    ) -> GraphRunResult:
        graph = self._require_graph()
        raw_output = dict(output or {})
        interrupts = list(raw_output.pop("__interrupt__", []) or [])
        snapshot = await graph.aget_state(self.graph_config(conversation_id))
        state = dict(snapshot.values or raw_output)
        if interrupts:
            item = interrupts[0]
            value = dict(getattr(item, "value", {}) or {})
            pending = {
                "interrupt_id": str(getattr(item, "id", "")),
                **value,
                "created_at": datetime.now().astimezone().isoformat(),
            }
            events.approval_required(pending)
            return GraphRunResult(
                status="interrupted",
                final_text=str(state.get("answer_final") or state.get("answer_draft") or ""),
                state=state,
                interrupted=True,
                pending_interrupt=pending,
                error_code=None,
            )
        return GraphRunResult(
            status=str(state.get("status") or "failed"),
            final_text=str(state.get("answer_final") or state.get("answer_draft") or ""),
            state=state,
            interrupted=False,
            pending_interrupt=None,
            error_code=(str(state.get("error_code")) if state.get("error_code") else None),
        )

    async def pending_interrupt(self, conversation_id: str) -> dict[str, Any] | None:
        graph = self._require_graph()
        snapshot = await graph.aget_state(self.graph_config(conversation_id))
        for task in snapshot.tasks:
            for item in getattr(task, "interrupts", ()) or ():
                value = dict(getattr(item, "value", {}) or {})
                return {
                    "interrupt_id": str(getattr(item, "id", "")),
                    **value,
                }
        return None

    async def get_state(self, conversation_id: str) -> dict[str, Any]:
        graph = self._require_graph()
        snapshot = await graph.aget_state(self.graph_config(conversation_id))
        return dict(snapshot.values or {})

    async def has_checkpoint(self, conversation_id: str) -> bool:
        graph = self._require_graph()
        snapshot = await graph.aget_state(self.graph_config(conversation_id))
        return bool(snapshot.config)

    async def delete_thread(self, conversation_id: str) -> None:
        if self.checkpointer is None:
            return
        await self.checkpointer.adelete_thread(self.thread_id(conversation_id))


agent_graph_runtime = LangGraphRuntimeManager()


__all__ = [
    "CHECKPOINT_NAMESPACE",
    "CHECKPOINT_THREAD_PREFIX",
    "GraphRunResult",
    "LangGraphRuntimeManager",
    "agent_graph_runtime",
]
