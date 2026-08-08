"""Lifecycle and invocation facade for the application-hosted Agent graph."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from langchain_core.messages import convert_to_messages
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command, Overwrite

from src.agent.model_runtime import (
    ModelProviderReportedTimeoutError,
    ModelProviderUnavailableError,
)
from src.agent.run_registry import active_run_registry
from src.agent.runtime_safety import get_agent_runtime_limits
from src.tools.registry import ToolRegistry

from .catalog import ToolCatalog
from .events import GraphEventBridge
from .executor import AtomicToolExecutor
from .graph import build_agent_graph
from .model import LiteLLMChatModel, LiteLLMGateway
from .state import AgentGraphInput, GraphContext


# ``checkpoint_ns`` is reserved by LangGraph for subgraph routing and the root
# graph must use its empty namespace.  The versioned thread prefix is the
# durable engine namespace here; it prevents a checkpoint created by the
# removed semantic DAG from being resumed by this loop.
CHECKPOINT_THREAD_PREFIX = "agent-v2"
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


def _evidence_repair_limit() -> int:
    try:
        return max(0, min(8, int(str(os.getenv("AGENT_EVIDENCE_REPAIR_LIMIT") or "2").strip())))
    except ValueError:
        return 2


@dataclass(frozen=True)
class GraphRunResult:
    status: str
    final_text: str
    state: dict[str, Any]
    stage_history: list[dict[str, Any]] | None = None
    interrupted: bool = False
    pending_interrupt: dict[str, Any] | None = None
    error_code: str | None = None


class LangGraphRuntimeManager:
    """Own one compiled ``create_agent`` graph and one native checkpointer."""

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
                context = AsyncPostgresSaver.from_conn_string(_postgres_connection_string(database_url))
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
        self.graph = build_agent_graph(checkpointer=self.checkpointer, registry=self.registry)

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
        limits = get_agent_runtime_limits()
        return {
            "configurable": {
                "thread_id": cls.thread_id(conversation_id),
                "checkpoint_ns": CHECKPOINT_NAMESPACE,
            },
            # This guards the number of graph transitions, never the duration
            # of a provider's reasoning.  Tool/provider budgets remain the
            # actual work limits.
            "recursion_limit": max(1_000, min(10_000, limits.max_tool_calls * 4 + 32)),
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
        if model is None:
            gateway = LiteLLMGateway(
                llm_config=llm_config,
                database=database,
                run_id=run_id,
                worker_id=active_run_registry.worker_id,
            )
            model_client: Any = LiteLLMChatModel(gateway=gateway, llm_config=dict(llm_config))
        else:
            model_client = model
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
            side_effect_lock=asyncio.Lock(),
        )

    async def _invoke_graph(
        self,
        graph: Any,
        graph_input: Any,
        *,
        conversation_id: str,
        context: GraphContext,
    ) -> Mapping[str, Any] | None:
        config = self.graph_config(conversation_id)
        try:
            return await graph.ainvoke(graph_input, config, context=context)
        except ModelProviderReportedTimeoutError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="model_provider_timeout",
                message="上游模型服务返回超时；已保留已有工具观察和证据。",
            )
        except ModelProviderUnavailableError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="model_provider_unavailable",
                message="模型服务暂时不可用；已保留已有工具观察和证据。",
            )
        except GraphRecursionError:
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="agent_loop_budget_exceeded",
                message="本轮工具/循环预算已耗尽；已保留已有观察并停止继续调用。",
            )
        except RuntimeError as exc:
            if "agent provider budget exceeded" not in str(exc).lower():
                raise
            return await self._terminate_partial(
                graph,
                config=config,
                context=context,
                error_code="provider_budget_exceeded",
                message="本轮模型调用、Token 或费用预算已耗尽；已保留已有工具观察和证据。",
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
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values or {})
        answer = str(state.get("answer_final") or state.get("answer_draft") or "").strip()
        if not answer:
            answer = message
        update = {"answer_final": answer, "status": "partial", "error_code": error_code}
        try:
            await graph.aupdate_state(config, update)
        except Exception:
            pass
        context.events.close_open_stages(status="failed", reason=message, error_code=error_code)
        context.events.stage("publish", "failed", message, error_code=error_code)
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
        limits = get_agent_runtime_limits()
        input_state: AgentGraphInput = {
            "messages": Overwrite(convert_to_messages(messages)),
            "engine": "langgraph_agent_loop",
            "run_id": run_id,
            "conversation_id": conversation_id,
            "user_text": user_text,
            "system_prompt": system_prompt,
            "reference_time": datetime.now().astimezone().isoformat(),
            "tool_results": Overwrite([]),
            "evidence": Overwrite([]),
            "claim_evidence": [],
            "completed_tool_call_ids": Overwrite([]),
            "approved_tool_call_ids": Overwrite([]),
            "rejected_tool_call_ids": Overwrite([]),
            "tool_call_count": Overwrite(0),
            "model_turn_count": Overwrite(0),
            "evidence_repair_count": Overwrite(0),
            "tool_call_limit": limits.max_tool_calls,
            "evidence_repair_limit": _evidence_repair_limit(),
            "work_budget_exhausted": False,
            "work_budget_detail": "",
            "evidence_feedback": "",
            "pending_interrupt": None,
            "answer_draft": "",
            "answer_final": "",
            "status": "running",
            "error_code": None,
        }
        output = await self._invoke_graph(
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
        output = await self._invoke_graph(
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
        output = await self._invoke_graph(graph, None, conversation_id=conversation_id, context=context)
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
                stage_history=events.stage_history,
                interrupted=True,
                pending_interrupt=pending,
                error_code=None,
            )
        return GraphRunResult(
            status=str(state.get("status") or "failed"),
            final_text=str(state.get("answer_final") or state.get("answer_draft") or ""),
            state=state,
            stage_history=events.stage_history,
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
                return {"interrupt_id": str(getattr(item, "id", "")), **value}
        return None

    async def get_state(self, conversation_id: str) -> dict[str, Any]:
        graph = self._require_graph()
        snapshot = await graph.aget_state(self.graph_config(conversation_id))
        return dict(snapshot.values or {})

    async def has_checkpoint(self, conversation_id: str, *, run_id: str | None = None) -> bool:
        graph = self._require_graph()
        snapshot = await graph.aget_state(self.graph_config(conversation_id))
        state = dict(snapshot.values or {})
        if not snapshot.config or not state:
            return False
        if run_id is not None:
            return str(state.get("run_id") or "") == str(run_id)
        return True

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
