from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import src.agent.result_processors as result_processors_module
from src.agent.result_processors import process_task_result
from src.agent.task_workflows import (
    ConfirmationState,
    EntityScope,
    ResultSelectionMode,
    ResultSelectionSpec,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
)



"""Shared fixtures for the focused test slices."""

def _response(function_name: str, payload: dict) -> SimpleNamespace:
    function = SimpleNamespace(
        name=function_name,
        arguments=json.dumps(payload, ensure_ascii=False),
    )
    message = SimpleNamespace(
        tool_calls=[SimpleNamespace(function=function)],
        content=None,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

def _response_content(content: str) -> SimpleNamespace:
    message = SimpleNamespace(tool_calls=[], content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

def _response_without_payload() -> SimpleNamespace:
    message = SimpleNamespace(tool_calls=[], content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

def _task(
    kind: StandardTaskKind,
    parameters: dict,
    *,
    symbols: tuple[str, ...] = (),
    result_selection: ResultSelectionSpec | None = None,
) -> ResolvedTask:
    normalized_parameters = dict(parameters)
    if kind == StandardTaskKind.THEME_BUSINESS_EVIDENCE:
        normalized_parameters.setdefault(
            "evidence_context",
            {
                "target_topics": ["人形机器人"],
                "domain_theses": [
                    {
                        "label": str(domain.get("label") or ""),
                        "rationale": "该板块直接服务于人形机器人产品",
                        "tier": 1,
                    }
                    for domain in normalized_parameters.get("domains") or []
                    if isinstance(domain, dict) and domain.get("label")
                ],
            },
        )
    return ResolvedTask(
        candidate=StandardTask(
            task_id="research",
            kind=kind,
            objective="核验人形机器人受益领域及相关公司",
            entity_scope=EntityScope.NONE,
            entities=[],
            parameters=normalized_parameters,
            depends_on=[],
            result_selection=(
                result_selection
                or ResultSelectionSpec(
                    mode=ResultSelectionMode.ALL_RELEVANT,
                    max_items=None,
                )
                if kind == StandardTaskKind.INDUSTRY_RESEARCH
                else None
            ),
            output_requirements=[],
            confirmation=ConfirmationState.NOT_REQUIRED,
            confidence=0.98,
        ),
        symbols=symbols,
    )
