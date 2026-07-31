# -*- coding: utf-8 -*-
"""
Agent Chat endpoint — policy-validated standard-task pipeline with
assistant-stream DataStreamResponse.

POST /api/v1/agent/chat

Body: { "messages": [ { "role": "user", "content": "贵州茅台行情" } ] }
Response: DataStreamResponse (line-delimited type-code:json chunks)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional

import litellm
from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.tools import (
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from src.agent.run_registry import (
    ActiveRun,
    RunBroadcaster,
    RunCapacityExceeded,
    active_run_registry,
)
from src.agent.run_streaming import (
    durable_subscriber_stream,
    subscriber_stream,
)
from src.agent.terminal_publisher import AgentTerminalPublisher
from src.agent.tool_dispatch import ToolDispatcher, ToolDispatchRequest
from src.agent.resource_scheduler import (
    ResourceCapacityExceeded,
    agent_resource_lease,
)
from src.agent.model_runtime import GuardedModelRuntime
from src.agent.evidence_security import build_untrusted_evidence_envelope
from src.agent.runtime_safety import (
    AgentRequestValidationError,
    agent_request_rate_limiter,
    get_agent_runtime_limits,
    is_production_environment,
    validate_chat_request_body,
)
from src.agent.progress import strip_agent_progress
from src.agent.message_normalization import (
    join_text_parts as _join_text_parts,
    normalize_incoming_messages as _normalize_incoming_messages,
    slim_tool_content as _slim_tool_content,
)
from src.agent.conversation_compaction import (
    compact_history_if_needed,
    estimate_messages_tokens,
    summarize_messages,
)
from api.v1.endpoints.agent.chat_context_helpers import (
    build_synthesis_messages as _build_synthesis_messages_impl,
    compact_history_if_needed as _compact_history_if_needed_impl,
    estimate_messages_tokens as _estimate_messages_tokens_impl,
    flush_substreams as _flush_substreams_impl,
    last_user_message_id as _last_user_message_id_impl,
    last_user_text as _last_user_text_impl,
    strip_progress_markers as _strip_progress_markers_impl,
    summarize_for_compaction as _summarize_for_compaction_impl,
    terminal_run_status as _terminal_run_status_impl,
)
from src.agent.domain_renderers import (
    build_domain_candidate_answer as _build_domain_candidate_answer,
    build_per_security_theme_answer as _build_per_security_theme_answer,
    build_ranked_domain_answer as _build_ranked_domain_answer,
    build_theme_business_evidence_answer as _build_theme_business_evidence_answer,
    processor_result as _processor_result,
)
from src.agent.collection_financial_renderers import (
    build_collection_financial_filter_answer as _build_collection_financial_filter_answer,
)
from src.agent.workflow_call_runner import run_workflow_call as _run_workflow_call_impl
from src.agent.pipeline_observers import (
    build_result_processor_runner,
    build_workflow_outcome_observer,
)
from src.agent.pipeline_planning import plan_standard_task
from src.agent.pipeline_execution import execute_standard_tasks
from src.agent.pipeline_finalization import finalize_standard_task
from src.agent.chat_task_results import (
    _blocked_task_answer,
    _exact_result_contract_answer,
    _standard_task_answer_issues,
    _task_status_evidence,
)
from api.v1.endpoints.agent.chat_background_runner import (
    _execute_background_agent_run as _execute_background_agent_run_impl,
)
from api.v1.endpoints.agent.chat_recovery import (
    recover_interrupted_agent_runs as _recover_interrupted_agent_runs_impl,
)
from api.v1.endpoints.agent.chat_route_start import agent_chat_impl
from api.v1.endpoints.agent.chat_route_resume import agent_chat_resume_impl
from src.agent.conversation_context import ConversationContext
from src.agent.orchestrator_v2.artifacts import (
    attach_artifact_refs_v2,
    build_execution_artifacts_v2,
)
from src.agent.orchestrator_v2.cache import (
    execution_cache_key_v2,
    load_execution_cache_v2,
    save_execution_cache_v2,
)
from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    AgentStage,
    AgentStageEventV2,
    EffectLevel,
    GoalBudgetV2,
    GoalContractV2,
    GoalDisposition,
    IntentOutlineV2,
    OrchestratorV2Error,
    PlanningTraceV2,
    QuestionType,
    RendererMode,
    StageStatus,
    stable_fingerprint,
)
from src.agent.orchestrator_v2.outcomes import execution_outcomes_v2
from src.agent.orchestrator_v2.goal_state import evaluate_goal_v2
from src.agent.orchestrator_v2.planner import (
    PlannedIntentGraphV2,
    plan_intent_graph_v2,
)
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.orchestrator_v2.runtime import (
    CompiledIntentGraphV2,
    compile_intent_graph_v2,
    compile_workflow_call_v2,
    restore_compiled_intent_graph_v2,
    serialize_compiled_intent_graph_v2,
)
from src.agent.orchestrator_v2.state import (
    ConversationContextV2,
    migrate_legacy_context,
)
from src.agent.result_contracts import (
    AnalysisPlaybook,
    CollectionFinancialFilterSpec,
    FinancialFilterCondition,
    INDUSTRY_CHAIN,
    INVESTMENT_DECISION,
    MARKET_OUTLOOK,
    STOCK_DEEP_RESEARCH,
    THEME_COMPANY_MAPPING,
)
from src.agent.result_processors import process_task_result
from src.agent.task_executor import PlanExecutionResult, WorkflowExecutor
from src.agent.task_planner import (
    TaskPlanValidationError,
)
from src.agent.task_workflows import (
    StandardTaskKind,
    TaskPlan,
    WorkflowCall,
    workflow_for,
)
from src.tools.registry import ToolRegistry
from src.tools.base import (
    ToolProgressUpdate,
)
from src.tools.process_runner import (
    execute_tool_isolated,
)
from src.llm.anthropic_gateway import (
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.services.chat_session_service import ChatSessionService
from src.services.buy_criteria.professional_analysis import DIMENSION_DEFINITIONS
from src.tools.evaluate_multi_stock_buy_criteria import public_buy_analysis_error
from src.storage import DatabaseManager
from src.auth import get_client_ip
from src.tools.symbols import (
    find_securities_in_text,
    normalize_tool_security_arguments,
)

logger = logging.getLogger(__name__)

MODEL_STREAM_HEARTBEAT_SECONDS = 5.0

# controller 产出层:当前生产路径走自建后台运行时的 RunBroadcaster (方法名与
# assistant-stream 的 RunController 对齐:append_text/add_tool_call/add_data/
# append_reasoning,以及 _stream_tasks 属性),不再依赖 create_run 的单连接生命周期。
ControllerLike = RunBroadcaster

_registry = ToolRegistry()

SYSTEM_PROMPT = """\
你是可靠的通用 AI 助手和 A 股研究写作助手。任务拆分、工具权限、执行顺序、并发、
确认与交易安全均由程序控制；你只负责根据已经执行完成的标准任务及其证据撰写最终答案，
不得重新规划、选择工具或声称执行了证据中没有的步骤。

## 回答原则
1. 普通知识、写作、解释和计算按用户原意回答；股票问题只覆盖用户本轮明确要求的范围。
2. 价格、行情、财务、估值、预测、资金流、新闻、公告、宏观数字和证券身份，只能使用本轮
   success=true 的证据。没有证据就明确写“缺失”，不得依靠记忆补数字、代码或公司事实。
3. 区分事实、计算结果、机构预测、媒体报道和推断；说明日期、报告期、来源、数据陈旧或降级。
4. 同时呈现支持证据与反证/风险。投资分析给出成立条件、失效条件和跟踪指标，不输出虚假精确目标价。
5. 多任务结果按依赖关系合并；失败或被阻止的任务明确写出，不得把部分成功包装成全部完成。
6. 使用中文和紧凑 Markdown，先给直接结论，再给关键证据和必要风险。不要复述内部任务计划、
   工具名、调用过程或“接下来我会”等过程旁白。
"""


_strip_progress_markers = _strip_progress_markers_impl
_last_user_text = _last_user_text_impl
_terminal_run_status = _terminal_run_status_impl
_last_user_message_id = _last_user_message_id_impl


# Shared gateway assembly remains a thin local compatibility alias.
_get_llm_config = resolve_anthropic_gateway_config


def _build_llm_kwargs(llm_cfg: Dict[str, Any], *, stream: bool, **extra: Any) -> Dict[str, Any]:
    return build_litellm_kwargs(llm_cfg, stream=stream, **extra)


def _estimate_messages_tokens(messages: List[Dict[str, Any]], model: str) -> int:
    return _estimate_messages_tokens_impl(messages, model, token_counter=litellm.token_counter)


_summarize_for_compaction = _summarize_for_compaction_impl


async def _compact_history_if_needed(
    full_messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    *,
    completion: Optional[Callable[..., Any]] = None,
) -> List[Dict[str, Any]]:
    return await _compact_history_if_needed_impl(
        full_messages,
        llm_cfg,
        completion=completion or litellm.acompletion,
        token_counter=litellm.token_counter,
    )


_flush_substreams = _flush_substreams_impl


def _build_synthesis_messages(
    messages: List[Dict[str, Any]],
    evidence: Optional[List[Dict[str, Any]]] = None,
    playbook: Optional[AnalysisPlaybook] = None,
) -> List[Dict[str, Any]]:
    return _build_synthesis_messages_impl(
        messages,
        evidence,
        playbook,
        system_prompt=SYSTEM_PROMPT,
    )


from api.v1.endpoints.agent.chat_decision_renderers import (
    _build_professional_buy_decision_answer,
    _build_professional_decision_fallback,
    _PROFESSIONAL_BUY_DIMENSION_IDS,
)

from api.v1.endpoints.agent.chat_research_renderers import (
    _build_catalyst_analysis_answer,
    _build_staged_news_search_answer,
    _build_workflow_evidence_fallback,
)

from api.v1.endpoints.agent.chat_evidence_renderers import (
    _build_quantitative_screen_answer,
    _build_realtime_quote_answer,
    _build_verified_evidence_fallback,
)

from api.v1.endpoints.agent.chat_answer_contracts import (
    _final_answer_contract_issues,
    _generic_answer_contract_issues,
    _playbook_answer_contract_issues,
    _prepare_playbook_answer,
    _professional_answer_contract_issues,
    _sanitize_mapping_answer,
    _unsupported_final_claims,
)

from api.v1.endpoints.agent.chat_reasoning import (
    _BufferedReasoningEmitter,
    _STAGE_TRACE_LABELS,
    _STAGE_TRACE_STATUS,
    _VISIBLE_REASONING_LANGUAGE_INSTRUCTION,
    _agent_stage_reasoning_line,
    _append_model_reasoning,
    _append_process_reasoning,
    _extract_model_reasoning_delta,
    _response_field,
    _trace_json_preview,
    _visible_reasoning_uses_chinese,
    _with_chinese_visible_reasoning,
)

from api.v1.endpoints.agent.chat_streaming import (
    _await_model_stream_step,
    _collect_streamed_model_answer,
    _stream_final_answer_without_tools as _stream_final_answer_without_tools_impl,
    _stream_structured_model_completion as _stream_structured_model_completion_impl,
)


async def _stream_structured_model_completion(
    controller: ControllerLike,
    completion: Callable[..., Any],
    **kwargs: Any,
) -> Any:
    return await _stream_structured_model_completion_impl(
        controller,
        completion,
        heartbeat_seconds=MODEL_STREAM_HEARTBEAT_SECONDS,
        **kwargs,
    )


async def _stream_final_answer_without_tools(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    *,
    state: Optional[Dict[str, Any]] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
    playbook: Optional[AnalysisPlaybook] = None,
    answer_validator: Optional[Callable[[str, Optional[List[Dict[str, Any]]]], List[str]]] = None,
    completion: Optional[Callable[..., Any]] = None,
) -> str:
    return await _stream_final_answer_without_tools_impl(
        controller,
        messages,
        llm_cfg,
        state=state,
        evidence=evidence,
        playbook=playbook,
        answer_validator=answer_validator,
        completion=completion or litellm.acompletion,
        synthesis_builder=_build_synthesis_messages,
        contract_checker=_final_answer_contract_issues,
        prepare_answer=_prepare_playbook_answer,
        fallback_builder=_build_verified_evidence_fallback,
        llm_builder=_build_llm_kwargs,
        heartbeat_seconds=MODEL_STREAM_HEARTBEAT_SECONDS,
    )

from src.agent.chat_task_results import (
    _blocked_task_answer,
    _exact_result_contract_answer,
    _standard_task_answer_issues,
    _task_status_evidence,
)

async def _run_standard_task_pipeline(
    controller: ControllerLike,
    messages: List[Dict[str, Any]],
    llm_cfg: Dict[str, Any],
    system_prompt: str = "",
    on_progress=None,
    *,
    state: Optional[Dict[str, Any]] = None,
    conversation_context: Optional[Dict[str, Any]] = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    run_attempt: int = 1,
    recovery_checkpoint: Mapping[str, Any] | None = None,
    db_manager: DatabaseManager | None = None,
    model_completion: Optional[Callable[..., Any]] = None,
) -> str:
    """Planner → fixed Workflow → policy validator → executor → aggregator."""
    planning_result = await plan_standard_task(
        controller,
        messages,
        llm_cfg,
        SYSTEM_PROMPT,
        state=state,
        conversation_context=conversation_context,
        conversation_id=conversation_id,
        run_id=run_id,
        run_attempt=run_attempt,
        recovery_checkpoint=recovery_checkpoint,
        db_manager=db_manager,
        registry=_registry,
        final_streamer=_stream_final_answer_without_tools,
        structured_streamer=_stream_structured_model_completion,
        answer_validator=_standard_task_answer_issues,
        planner=plan_intent_graph_v2,
        compiler=compile_intent_graph_v2,
        model_completion=model_completion or litellm.acompletion,
    )
    if isinstance(planning_result, str):
        return planning_result
    active_run_id = planning_result.active_run_id
    latest_user_text = planning_result.latest_user_text
    request_fingerprint = planning_result.request_fingerprint
    v2_stage_durations_ms = planning_result.stage_durations_ms
    current_entities = planning_result.current_entities
    context_v2 = planning_result.context_v2
    compiled_v2 = planning_result.compiled_v2
    graph_v2 = planning_result.graph_v2
    planning_trace = planning_result.planning_trace
    artifact_map = planning_result.artifact_map
    plan = planning_result.plan
    resolved_tasks = planning_result.resolved_tasks
    planned_goal = planning_result.planned_goal
    emit_v2_stage = planning_result.emit_v2_stage
    guarded_model_completion = planning_result.guarded_model_completion
    stream_structured_completion = planning_result.stream_structured_completion
    execution_phase = await execute_standard_tasks(
        controller=controller,
        active_run_id=active_run_id,
        conversation_id=conversation_id,
        db_manager=db_manager,
        llm_cfg=llm_cfg,
        plan=plan,
        resolved_tasks=resolved_tasks,
        compiled_v2=compiled_v2,
        planning_trace=planning_trace,
        context_v2=context_v2,
        artifact_map=artifact_map,
        planned_goal=planned_goal,
        current_entities=current_entities,
        latest_user_text=latest_user_text,
        request_fingerprint=request_fingerprint,
        messages=messages,
        run_attempt=run_attempt,
        emit_v2_stage=emit_v2_stage,
        guarded_model_completion=guarded_model_completion,
        stream_structured_completion=stream_structured_completion,
        registry=_registry,
        heartbeat_seconds=MODEL_STREAM_HEARTBEAT_SECONDS,
        isolated_executor=execute_tool_isolated,
        compact_result=_compact_tool_result,
        attach_fallback=_maybe_attach_search_fallback,
        status_evidence_builder=_task_status_evidence,
        flush_substreams=_flush_substreams,
    )
    execution = execution_phase.execution
    evidence = execution_phase.evidence
    compiled_v2 = execution_phase.compiled_v2
    resolved_tasks = execution_phase.resolved_tasks
    plan = execution_phase.plan
    planning_trace = execution_phase.planning_trace
    outcomes_v2 = execution_phase.outcomes_v2
    goal_state_v2 = execution_phase.goal_state_v2
    return await finalize_standard_task(
        controller=controller,
        active_run_id=active_run_id,
        conversation_id=conversation_id,
        db_manager=db_manager,
        state=state,
        compiled_v2=compiled_v2,
        outcomes_v2=outcomes_v2,
        plan=plan,
        execution=execution,
        evidence=evidence,
        planning_trace=planning_trace,
        goal_state_v2=goal_state_v2,
        context_v2=context_v2,
        latest_user_text=latest_user_text,
        messages=messages,
        llm_cfg=llm_cfg,
        system_prompt=system_prompt,
        base_system_prompt=SYSTEM_PROMPT,
        planned_goal=planned_goal,
        guarded_model_completion=guarded_model_completion,
        emit_v2_stage=emit_v2_stage,
        v2_stage_durations_ms=v2_stage_durations_ms,
        blocked_answer_builder=_blocked_task_answer,
        exact_answer_builder=_exact_result_contract_answer,
        final_streamer=_stream_final_answer_without_tools,
        answer_validator=_standard_task_answer_issues,
        last_user_message_id=_last_user_message_id,
        normalize_messages=_normalize_incoming_messages,
        compact_history=_compact_history_if_needed,
    )


from api.v1.endpoints.agent.chat_background_runner import _execute_background_agent_run as _execute_background_agent_run_impl


async def _execute_background_agent_run(
    *,
    controller: RunBroadcaster,
    run: ActiveRun,
    messages: List[Dict[str, Any]],
    body: Mapping[str, Any],
    llm_cfg: Mapping[str, Any],
    agent_context: Mapping[str, Any] | None,
    conversation_id: str,
    db_manager: DatabaseManager,
    session_service: ChatSessionService,
    recovery_checkpoint: Mapping[str, Any] | None = None,
    explicit_memories: List[Mapping[str, Any]] | None = None,
) -> None:
    async def pipeline_runner(*args: Any, **kwargs: Any) -> str:
        kwargs["model_completion"] = litellm.acompletion
        return await _run_standard_task_pipeline(*args, **kwargs)

    return await _execute_background_agent_run_impl(
        controller=controller,
        run=run,
        messages=messages,
        body=body,
        llm_cfg=llm_cfg,
        agent_context=agent_context,
        conversation_id=conversation_id,
        db_manager=db_manager,
        session_service=session_service,
        recovery_checkpoint=recovery_checkpoint,
        pipeline_runner=pipeline_runner,
        terminal_status_mapper=_terminal_run_status,
        explicit_memories=explicit_memories,
    )


from api.v1.endpoints.agent.chat_recovery import recover_interrupted_agent_runs as _recover_interrupted_agent_runs_impl


async def recover_interrupted_agent_runs(
    db_manager: DatabaseManager,
    *,
    limit: int = 20,
) -> int:
    return await _recover_interrupted_agent_runs_impl(
        db_manager,
        limit=limit,
        config_loader=_get_llm_config,
        background_runner=_execute_background_agent_run,
    )


@router.post("/agent/chat")
async def agent_chat(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    return await agent_chat_impl(
        request,
        db_manager,
        background_runner=_execute_background_agent_run,
        config_loader=_get_llm_config,
    )


@router.post("/agent/chat/resume")
async def agent_chat_resume(
    request: Request,
    db_manager: DatabaseManager = Depends(get_database_manager),
):
    return await agent_chat_resume_impl(request, db_manager)
