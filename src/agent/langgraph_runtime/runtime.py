"""Lifecycle and invocation facade for the application-hosted Agent graph."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from langchain_core.messages import AIMessage, AIMessageChunk, convert_to_messages
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command, Overwrite

from src.agent.model_runtime import (
    ModelProviderReportedTimeoutError,
    ModelProviderUnavailableError,
)
from src.agent.progress import is_non_answer_agent_message
from src.agent.run_registry import active_run_registry
from src.agent.runtime_safety import get_agent_runtime_limits
from src.tools.registry import ToolRegistry

from .catalog import ToolCatalog
from .claim_evidence import (
    build_claim_evidence_ledger,
    build_structured_claim_evidence_ledger,
)
from .content_access import (
    DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT,
    build_content_access_targets,
    canonical_url,
    cited_reference_access_status,
    required_content_access_targets,
    successful_content_read_urls,
)
from .events import GraphEventBridge
from .executor import AtomicToolExecutor
from .evidence_identity import prepare_answer_for_client
from .answer_contract import (
    finalize_terminal_answer,
    render_structured_answer,
    structured_answer_mapping,
)
from .graph import DEFAULT_RESPONSE_FORMAT, build_agent_graph
from .model import LiteLLMChatModel, LiteLLMGateway
from .state import AgentGraphInput, GraphContext
from src.tools.base import evidence_record_is_eligible


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


def _checkpoint_id(config: Mapping[str, Any] | None) -> str | None:
    """Return a checkpoint id without exposing the rest of a runnable config."""
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    value = str(configurable.get("checkpoint_id") or "").strip()
    return value or None


def _checkpoint_task_summary(task: Any) -> dict[str, Any]:
    """Project Pregel tasks to safe lifecycle hints for diagnostics."""
    interrupts = getattr(task, "interrupts", ()) or ()
    error = getattr(task, "error", None)
    return {
        "id": str(getattr(task, "id", "") or "") or None,
        "name": str(getattr(task, "name", "") or "") or None,
        "interrupt_count": len(interrupts),
        "has_error": error is not None,
        "error_type": type(error).__name__ if error is not None else None,
    }


def _checkpoint_summary(snapshot: Any) -> dict[str, Any]:
    """Build a bounded, read-only summary of one LangGraph checkpoint.

    The full values remain in the native saver.  The diagnostic endpoint only
    needs enough information to identify a graph revision and understand where
    it stopped; returning raw messages/tool payloads here would duplicate the
    conversation and could expose sensitive tool arguments.
    """
    values = getattr(snapshot, "values", {})
    values = dict(values) if isinstance(values, Mapping) else {}
    metadata = getattr(snapshot, "metadata", {})
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    writes = metadata.get("writes")
    write_keys = (
        sorted(str(key) for key in writes)
        if isinstance(writes, Mapping)
        else []
    )
    messages = values.get("messages")
    tool_results = values.get("tool_results")
    evidence = values.get("evidence")
    tasks = getattr(snapshot, "tasks", ()) or ()
    created_at = getattr(snapshot, "created_at", None)
    return {
        "checkpoint_id": _checkpoint_id(getattr(snapshot, "config", None)),
        "parent_checkpoint_id": _checkpoint_id(getattr(snapshot, "parent_config", None)),
        "created_at": str(created_at) if created_at is not None else None,
        "metadata": {
            "source": str(metadata.get("source") or "") or None,
            "step": metadata.get("step"),
            "write_keys": write_keys,
        },
        "next": [str(item) for item in (getattr(snapshot, "next", ()) or ())],
        "tasks": [_checkpoint_task_summary(task) for task in tasks],
        "state": {
            "run_id": str(values.get("run_id") or "") or None,
            "conversation_id": str(values.get("conversation_id") or "") or None,
            "status": str(values.get("status") or "") or None,
            "message_count": len(messages) if isinstance(messages, (list, tuple)) else 0,
            "tool_result_count": len(tool_results) if isinstance(tool_results, (list, tuple)) else 0,
            "evidence_count": len(evidence) if isinstance(evidence, (list, tuple)) else 0,
            "model_turn_count": int(values.get("model_turn_count") or 0),
            "tool_call_count": int(values.get("tool_call_count") or 0),
            "evidence_repair_count": int(values.get("evidence_repair_count") or 0),
            "content_access_repair_count": int(values.get("content_access_repair_count") or 0),
            "response_repair_count": int(values.get("response_repair_count") or 0),
            "response_repair_limit": int(values.get("response_repair_limit") or 0),
            "fallback_repair_count": int(values.get("fallback_repair_count") or 0),
            "fallback_repair_limit": int(values.get("fallback_repair_limit") or 0),
            "required_content_read_count": len(values.get("required_content_reads") or []),
            "pending_content_read_count": len(values.get("pending_content_reads") or []),
            "has_pending_interrupt": isinstance(values.get("pending_interrupt"), Mapping),
            "has_answer": bool(str(values.get("answer_final") or values.get("answer_draft") or "").strip()),
        },
    }


def _evidence_repair_limit() -> int:
    try:
        return max(0, min(8, int(str(os.getenv("AGENT_EVIDENCE_REPAIR_LIMIT") or "2").strip())))
    except ValueError:
        return 2


def _content_access_repair_limit() -> int:
    try:
        return max(
            0,
            min(
                8,
                int(
                    str(os.getenv("AGENT_CONTENT_ACCESS_REPAIR_LIMIT") or DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT)
                    .strip()
                ),
            ),
        )
    except ValueError:
        return DEFAULT_CONTENT_ACCESS_REPAIR_LIMIT


def _response_repair_limit() -> int:
    """Bound provider attempts to satisfy the application answer contract."""
    try:
        return max(
            0,
            min(8, int(str(os.getenv("AGENT_RESPONSE_REPAIR_LIMIT") or "1").strip())),
        )
    except ValueError:
        return 1


def _fallback_repair_limit() -> int:
    """Bound recovery requests for unsupported final answers, not ongoing tools."""
    try:
        return max(
            0,
            min(4, int(str(os.getenv("AGENT_SOURCE_FALLBACK_REPAIR_LIMIT") or "1").strip())),
        )
    except ValueError:
        return 1


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
        response_format: Any | None = DEFAULT_RESPONSE_FORMAT,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.catalog = ToolCatalog(self.registry)
        self.compact_result = compact_result
        self.attach_fallback = attach_fallback
        self.response_format = response_format
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
        self.graph = build_agent_graph(
            checkpointer=self.checkpointer,
            registry=self.registry,
            response_format=self.response_format,
        )

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
            from src.agent.usage import PersistedUsageCallback

            model_client: Any = LiteLLMChatModel(
                gateway=gateway, llm_config=dict(llm_config),
                callbacks=[PersistedUsageCallback(database, run_id)],
            )
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
            last_update: Mapping[str, Any] | None = None
            async for chunk in graph.astream(
                graph_input,
                config,
                context=context,
                stream_mode=["messages", "updates"],
                version="v2",
            ):
                if not isinstance(chunk, Mapping):
                    continue
                chunk_type = str(chunk.get("type") or "")
                data = chunk.get("data")
                if chunk_type == "messages" and isinstance(data, (list, tuple)) and data:
                    message = data[0]
                    metadata = data[1] if len(data) > 1 and isinstance(data[1], Mapping) else {}
                    if metadata.get("lc_source") == "summarization":
                        continue
                    if isinstance(message, (AIMessageChunk, AIMessage)):
                        context.events.model_message(message)
                elif chunk_type == "updates" and isinstance(data, Mapping):
                    last_update = data
            return last_update
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

    async def get_state_history(
        self,
        conversation_id: str,
        *,
        limit: int = 20,
        before_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """Read native LangGraph checkpoint history without mutating a thread.

        ``agent_runs`` remains the authority for request ownership and worker
        lifecycle.  This method deliberately reads the graph thread directly
        so diagnostics can distinguish a durable Run state from the latest
        graph revision.  It does not call ``update_state`` or execute tools.
        """
        graph = self._require_graph()
        safe_limit = max(1, min(100, int(limit)))
        config = self.graph_config(conversation_id)
        before_config: Mapping[str, Any] | None = None
        normalized_before = str(before_checkpoint_id or "").strip()
        if normalized_before:
            before_config = self.graph_config(conversation_id)
            before_config["configurable"]["checkpoint_id"] = normalized_before

        snapshots: list[dict[str, Any]] = []
        async for snapshot in graph.aget_state_history(
            config,
            before=before_config,
            limit=safe_limit + 1,
        ):
            snapshots.append(_checkpoint_summary(snapshot))

        has_more = len(snapshots) > safe_limit
        items = snapshots[:safe_limit]
        current = await graph.aget_state(config)
        return {
            "conversation_id": conversation_id,
            "thread_id": self.thread_id(conversation_id),
            "current_checkpoint_id": _checkpoint_id(getattr(current, "config", None)),
            "checkpoint_authority": "langgraph_checkpointer",
            "run_lifecycle_authority": "agent_runs",
            "read_only": True,
            "items": items,
            "has_more": has_more,
            "next_before_checkpoint_id": (
                items[-1].get("checkpoint_id") if has_more and items else None
            ),
        }

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
        structured_answer = structured_answer_mapping(state.get("structured_answer"))
        answer = str(state.get("answer_final") or state.get("answer_draft") or "").strip()
        factual_evidence = [
            item
            for item in state.get("evidence") or []
            if evidence_record_is_eligible(item)
            and str(item.get("effect") or "read") != "side_effect"
        ]
        if not answer and structured_answer:
            answer = render_structured_answer(
                structured_answer,
                factual_evidence,
            )
        if is_non_answer_agent_message(answer):
            answer = ""
        answer, _ = prepare_answer_for_client(answer, factual_evidence)
        content_targets, pending_content_reads = build_content_access_targets(
            tool_results=state.get("tool_results") or [],
            existing_targets=state.get("content_access_targets") or [],
        )
        required_content_reads = required_content_access_targets(
            answer=answer,
            evidence=state.get("evidence") or [],
            tool_results=state.get("tool_results") or [],
            targets=content_targets,
        )
        cited_access = cited_reference_access_status(
            answer=answer,
            evidence=state.get("evidence") or [],
            tool_results=state.get("tool_results") or [],
            targets=content_targets,
        )
        selection_required = [
            access
            for access in cited_access.values()
            if access.get("selection_required") is True
        ]
        target_urls = {canonical_url(item.get("url")) for item in content_targets}
        successful_urls = successful_content_read_urls(state.get("tool_results") or []) & target_urls
        required_pending = [
            item
            for item in required_content_reads
            if canonical_url(item.get("url")) not in successful_urls
        ]
        pending_urls = {canonical_url(item.get("url")) for item in required_pending}
        pending_content_reads = [
            *required_pending,
            *[
                item
                for item in pending_content_reads
                if canonical_url(item.get("url")) not in pending_urls
            ],
        ]
        terminal_error_code = error_code
        terminal_detail = message
        if pending_content_reads or selection_required:
            terminal_error_code = "content_access_incomplete"
            terminal_detail = (
                "正文取证未完成："
                + message
                + "以上结论仅基于来源链接、标题/摘要或结构化字段，正文未核验"
            )
            answer = finalize_terminal_answer(
                answer or "已获取来源索引，但本轮未完成正文读取。",
                status="partial",
                error_code=terminal_error_code,
                detail=terminal_detail,
            )
        else:
            answer = finalize_terminal_answer(
                answer,
                status="partial",
                error_code=terminal_error_code,
                detail=terminal_detail,
            )
        claim_evidence = (
            build_claim_evidence_ledger(
                answer,
                factual_evidence,
                [
                    item
                    for item in state.get("tool_results") or []
                    if isinstance(item, Mapping)
                ],
            ).get("claims")
            if factual_evidence
            else []
        )
        update = {
            "answer_final": answer,
            "status": "partial",
            "error_code": terminal_error_code,
            "terminal_detail": terminal_detail,
        }
        if structured_answer:
            update["structured_answer"] = structured_answer
        if factual_evidence:
            if structured_answer:
                update["claim_evidence"] = list(
                    build_structured_claim_evidence_ledger(
                        structured_answer.get("blocks") or [],
                        factual_evidence,
                        [
                            item
                            for item in state.get("tool_results") or []
                            if isinstance(item, Mapping)
                        ],
                    ).get("claims")
                    or []
                )
            else:
                update["claim_evidence"] = list(claim_evidence or [])
        if content_targets or pending_content_reads:
            update.update(
                {
                    "content_access_targets": content_targets,
                    "required_content_reads": required_content_reads,
                    "pending_content_reads": pending_content_reads,
                    "content_access_feedback": "",
                }
            )
        try:
            await graph.aupdate_state(config, update)
        except Exception:
            pass
        context.events.close_open_stages(
            status="failed",
            reason=terminal_detail,
            error_code=terminal_error_code,
        )
        context.events.stage(
            "publish",
            "failed",
            terminal_detail,
            error_code=terminal_error_code,
        )
        if answer:
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
            "content_access_repair_count": Overwrite(0),
            "response_repair_count": Overwrite(0),
            "fallback_repair_count": Overwrite(0),
            "tool_call_limit": limits.max_tool_calls,
            "evidence_repair_limit": _evidence_repair_limit(),
            "content_access_repair_limit": _content_access_repair_limit(),
            "response_repair_limit": _response_repair_limit(),
            "fallback_repair_limit": _fallback_repair_limit(),
            "work_budget_exhausted": False,
            "work_budget_detail": "",
            "evidence_feedback": "",
            "fallback_feedback": "",
            "content_access_targets": [],
            "required_content_reads": [],
            "pending_content_reads": [],
            "content_access_feedback": "",
            "response_format_feedback": "",
            "pending_interrupt": None,
            "structured_answer": None,
            "structured_answer_call_id": "",
            "structured_output_required": self.response_format is not None,
            "answer_draft": "",
            "answer_final": "",
            "terminal_detail": "",
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
        if not interrupts:
            # ``astream(version="v2")`` may expose an interrupt inside an
            # updates chunk rather than as the return value of ``ainvoke``.
            # The checkpointer is the authoritative source for both forms.
            for task in getattr(snapshot, "tasks", ()) or ():
                interrupts.extend(getattr(task, "interrupts", ()) or ())
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
