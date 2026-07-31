from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from src.agent.result_contracts import (
    DomainCatalogSelectionV2,
    DomainCollectionV2,
    IndustryBenefitOutlineV2,
)
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
from src.services.domain_board_catalog import domain_board_catalog_snapshot_id



"""Shared fixtures for the focused test slices."""

def _response(function_name: str, payload: Any) -> SimpleNamespace:
    function = SimpleNamespace(
        name=function_name,
        arguments=(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)),
    )
    message = SimpleNamespace(
        tool_calls=[SimpleNamespace(function=function)],
        content=None,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

def _content_response(payload: str) -> SimpleNamespace:
    message = SimpleNamespace(
        tool_calls=[],
        content=payload,
        reasoning_content=None,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

def _function_name(kwargs: dict[str, Any]) -> str:
    if "tool_choice" in kwargs:
        return str(kwargs["tool_choice"]["function"]["name"])
    request = json.loads(kwargs["messages"][1]["content"])
    return str(request["output_contract_name"])

def _response_for(kwargs: dict[str, Any], payload: Any) -> SimpleNamespace:
    if "tool_choice" in kwargs:
        return _response(_function_name(kwargs), payload)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return _content_response(text)

def _task(
    *,
    selection: ResultSelectionSpec | None = None,
    assumptions: list[dict[str, Any]] | None = None,
) -> ResolvedTask:
    return ResolvedTask(
        candidate=StandardTask(
            task_id="industry",
            kind=StandardTaskKind.INDUSTRY_RESEARCH,
            objective="判断人形机器人产业最受益的领域",
            entity_scope=EntityScope.NONE,
            entities=[],
            parameters={
                "query": "人形机器人哪些领域最受益",
                "domains": [{"label": "人形机器人"}],
                "_assumptions": assumptions or [],
            },
            depends_on=[],
            result_selection=selection
            or ResultSelectionSpec(
                mode=ResultSelectionMode.ALL_RELEVANT,
                max_items=None,
            ),
            output_requirements=[],
            confirmation=ConfirmationState.NOT_REQUIRED,
            confidence=1.0,
        ),
        symbols=(),
    )

def _catalog(boards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tool": "get_domain_board_catalog",
            "arguments": {},
            "result": {
                "success": True,
                "partial": False,
                "errors": [],
                "warnings": [],
                "source": "测试实时板块目录",
                "data_time": "2026-07-29T09:30:00+08:00",
                "boards": boards,
            },
        }
    ]

def _outline() -> dict[str, Any]:
    return {
        "topic": "人形机器人",
        "roles": [
            {
                "role_id": "actuator",
                "label": "机器人执行器",
                "benefit_mechanism": "执行机构直接承接运动控制价值量",
                "tier": 1,
            },
            {
                "role_id": "reducer",
                "label": "减速器",
                "benefit_mechanism": "关节传动需要高精度减速部件",
                "tier": 1,
            },
        ],
        "selection_objective": "选择产业链中受益最直接的实时概念板块",
    }
