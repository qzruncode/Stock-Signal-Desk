from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

from src.agent.conversation_context import (
    ConversationContext,
    build_turn_reference,
    recover_context_from_thread_state,
)
from src.agent.task_executor import CallOutcome, PlanExecutionResult, TaskExecutionResult
from src.agent.task_planner import resolve_plan_entities
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResultSelectionMode,
    ResultSelectionSpec,
    ResolvedTask,
    SecurityEntity,
    StandardTask,
    StandardTaskKind,
    TaskPlan,
)
from src.services.chat_session_service import ChatSessionService
from src.storage.migrations import ensure_compatible_schema



"""Shared fixtures for the focused test slices."""

def _quote_task() -> StandardTask:
    return StandardTask(
        task_id="quotes",
        kind=StandardTaskKind.REALTIME_QUOTE,
        objective="查看两只股票当前行情",
        entity_scope=EntityScope.CURRENT_MESSAGE,
        entities=["贵州茅台", "五粮液"],
        parameters={},
        depends_on=[],
        output_requirements=[],
        confirmation=ConfirmationState.NOT_REQUIRED,
        confidence=0.98,
    )

def _legacy_rich_thread_state() -> dict:
    return {
        "messages": [
            {
                "message": {
                    "id": "user-domain",
                    "role": "user",
                    "content": [{"type": "text", "text": "按这些领域找A股公司"}],
                },
            },
            {
                "message": {
                    "id": "assistant-domain",
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "这里是渲染后的候选名单，不参与迁移。"},
                        {
                            "type": "tool-call",
                            "toolCallId": "call-domain",
                            "toolName": "get_domain_stock_candidates",
                            "args": {
                                "domains": [{"label": "机器人执行器"}],
                            },
                            "result": {
                                "success": True,
                                "domainResults": [
                                    {
                                        "domain": "机器人执行器",
                                        "items": [
                                            {"symbol": "000001", "name": "甲公司"},
                                            {"symbol": "000002", "name": "乙公司"},
                                        ],
                                    },
                                ],
                            },
                        },
                    ],
                },
            },
            {
                "message": {
                    "id": "user-filter",
                    "role": "user",
                    "content": [{"type": "text", "text": "剔除不符合条件的公司"}],
                },
            },
            {
                "message": {
                    "id": "assistant-error",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "规划服务超时。"}],
                },
            },
        ],
    }
