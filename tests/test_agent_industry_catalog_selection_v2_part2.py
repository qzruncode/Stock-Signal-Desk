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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
def test_v2_best_one_is_preserved_without_inventing_an_assumption() -> None:
    selection = ResultSelectionSpec(
        mode=ResultSelectionMode.BEST_ONE,
        max_items=1,
    )

    async def completion(**kwargs):
        function_name = _function_name(kwargs)
        if function_name == "submit_industry_benefit_outline_v2":
            return _response_for(kwargs, _outline())
        return _response_for(
            kwargs,
            {
                "items": [
                    {
                        "board_id": "BK0566",
                        "role_id": "reducer",
                        "tier": 1,
                    }
                ],
            },
        )

    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(selection=selection),
            _catalog(
                [
                    {"sector_code": "BK0566", "name": "减速器"},
                    {"sector_code": "BK1234", "name": "机器人执行器"},
                ]
            ),
            {"model": "test"},
            completion=completion,
        )
    )

    resource = DomainCollectionV2.model_validate(result["semantic_artifacts"][0])
    assert resource.result_selection.mode == "best_one"
    assert resource.boards[0].board_id == "BK0566"
    assert resource.assumptions == ()

def test_v2_reuses_only_a_complete_typed_snapshot_cache() -> None:
    cached: dict[str, bytes] = {}
    model_calls = 0

    class FakeDatabase:
        def get_tool_cache(self, key: str):
            payload = cached.get(key)
            return {"payload": payload} if payload is not None else None

        def save_tool_cache(self, key: str, payload: bytes):
            cached[key] = payload

    async def completion(**kwargs):
        nonlocal model_calls
        model_calls += 1
        function_name = _function_name(kwargs)
        if function_name == "submit_industry_benefit_outline_v2":
            return _response_for(kwargs, _outline())
        return _response_for(
            kwargs,
            {
                "items": [
                    {
                        "board_id": "BK0566",
                        "role_id": "reducer",
                        "tier": 1,
                    }
                ],
            },
        )

    evidence = _catalog([{"sector_code": "BK0566", "name": "减速器"}])
    with patch(
        "src.storage.manager.DatabaseManager.get_instance",
        return_value=FakeDatabase(),
    ):
        first = asyncio.run(
            process_task_result(
                "ranked_domain_selection",
                _task(),
                evidence,
                {"model": "test", "api_base": "https://example.invalid"},
                completion=completion,
            )
        )
        second = asyncio.run(
            process_task_result(
                "ranked_domain_selection",
                _task(),
                evidence,
                {"model": "test", "api_base": "https://example.invalid"},
                completion=completion,
            )
        )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert model_calls == 2
    DomainCollectionV2.model_validate(second["semantic_artifacts"][0])

def test_catalog_snapshot_ignores_order_and_market_fields_but_not_membership() -> None:
    first = [
        {
            "sector_code": "BK0001",
            "name": "板块一",
            "pct_chg": 1.0,
        },
        {
            "sector_code": "BK0002",
            "name": "板块二",
            "main_net_inflow": 100,
        },
    ]
    reordered = [
        {
            "sector_code": "BK0002",
            "name": "板块二",
            "main_net_inflow": -200,
        },
        {
            "sector_code": "BK0001",
            "name": "板块一",
            "pct_chg": -3.0,
        },
    ]
    changed = [
        *first,
        {"sector_code": "BK0003", "name": "板块三"},
    ]

    assert domain_board_catalog_snapshot_id(first) == (domain_board_catalog_snapshot_id(reordered))
    assert domain_board_catalog_snapshot_id(first) != (domain_board_catalog_snapshot_id(changed))
