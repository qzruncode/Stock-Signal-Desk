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


def _response(function_name: str, payload: Any) -> SimpleNamespace:
    function = SimpleNamespace(
        name=function_name,
        arguments=(
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        ),
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
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False)
    )
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
            result_selection=selection or ResultSelectionSpec(
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
    return [{
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
    }]


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


def test_v2_role_ids_accept_domain_terms_that_begin_with_a_digit() -> None:
    outline = IndustryBenefitOutlineV2.model_validate({
        **_outline(),
        "roles": [{
            "role_id": "3d_vision_camera",
            "label": "3D视觉相机",
            "benefit_mechanism": "为机器人提供深度环境感知",
            "tier": 1,
        }],
    })
    selection = DomainCatalogSelectionV2.model_validate({
        "items": [{
            "board_id": "BK0998",
            "role_id": outline.roles[0].role_id,
            "tier": 1,
        }],
    })

    assert selection.items[0].role_id == "3d_vision_camera"


def test_v2_uses_same_source_schemas_and_repairs_unknown_board_id() -> None:
    calls: list[str] = []

    async def completion(**kwargs):
        function_name = _function_name(kwargs)
        calls.append(function_name)
        request = json.loads(kwargs["messages"][1]["content"])
        if function_name == "submit_industry_benefit_outline_v2":
            assert "tools" not in kwargs
            assert request["exact_output_schema"] == (
                IndustryBenefitOutlineV2.model_json_schema()
            )
            return _response_for(kwargs, _outline())

        assert "tools" not in kwargs
        assert request["exact_output_schema"] == (
            DomainCatalogSelectionV2.model_json_schema()
        )
        assert kwargs["max_tokens"] == 32_000
        item_schema = request["exact_output_schema"][
            "$defs"
        ]["DomainCatalogSelectionItemV2"]["properties"]
        assert set(item_schema) == {"board_id", "role_id", "tier"}
        assert len(request["project_boards"]) == 3
        if "targeted_repair" not in request:
            return _response_for(kwargs, {
                "items": [
                    {"board_id": "BK1234", "role_id": "actuator", "tier": 1},
                    {"board_id": "BK0566", "role_id": "reducer", "tier": 1},
                    {"board_id": "BK9999", "role_id": "reducer", "tier": 1},
                ],
            })
        repair = request["targeted_repair"]
        assert repair["invalid_payload"]["items"][-1]["board_id"] == "BK9999"
        assert repair["issues"] == [{
            "pointer": "/items/2/board_id",
            "code": "unknown_board_id",
            "expected": "one of project_boards[].board_id",
            "allowed": [],
            "message": "unknown board_id: BK9999",
        }]
        return _response_for(kwargs, {
            "items": [
                {"board_id": "BK1234", "role_id": "actuator", "tier": 1},
                {"board_id": "BK0566", "role_id": "reducer", "tier": 1},
            ],
        })

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(),
        _catalog([
            {"sector_code": "BK1184", "name": "人形机器人"},
            {"sector_code": "BK1234", "name": "机器人执行器"},
            {"sector_code": "BK0566", "name": "减速器"},
        ]),
        {"model": "test"},
        completion=completion,
    ))

    assert calls == [
        "submit_industry_benefit_outline_v2",
        "submit_domain_catalog_selection_v2",
        "submit_domain_catalog_selection_v2",
    ]
    assert result["success"] is True
    assert result["repairs"][0]["succeeded"] is True
    assert [item["board_code"] for item in result["items"]] == [
        "BK1234",
        "BK0566",
    ]
    resource = DomainCollectionV2.model_validate(
        result["semantic_artifacts"][0]
    )
    assert resource.coverage.binding_complete is True
    assert resource.coverage.catalog_total == 3
    assert [item.board_name for item in resource.boards] == [
        "机器人执行器",
        "减速器",
    ]
    assert "运动控制价值量" in resource.boards[0].rationale
    assert result["resource_outputs"]["domain_collection"][0] == {
        "label": "机器人执行器",
        "board_queries": ["机器人执行器"],
        "mapping_type": "catalog_binding",
        "rationale": (
            "机器人执行器对应“机器人执行器”环节；"
            "执行机构直接承接运动控制价值量"
        ),
        "unresolved_parts": [],
    }


def test_v2_supplies_the_complete_504_board_catalog_once() -> None:
    calls: list[dict[str, Any]] = []
    progress: list[tuple[str, str]] = []
    boards = [
        {"sector_code": f"BK{index:04}", "name": f"板块{index}"}
        for index in range(504)
    ]

    async def completion(**kwargs):
        function_name = _function_name(kwargs)
        request = json.loads(kwargs["messages"][1]["content"])
        calls.append({"function": function_name, "request": request})
        if function_name == "submit_industry_benefit_outline_v2":
            return _response_for(kwargs, _outline())
        assert request["project_boards"] == [
            {"board_id": f"BK{index:04}", "name": f"板块{index}"}
            for index in range(504)
        ]
        return _response_for(kwargs, {
            "items": [{
                "board_id": "BK0200",
                "role_id": "actuator",
                "tier": 1,
            }],
        })

    async def observe(_completed: int, _total: int, detail: dict[str, Any]):
        progress.append((
            str(detail.get("stage")),
            str(detail.get("status")),
        ))

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(),
        _catalog(boards),
        {"model": "test"},
        completion=completion,
        progress=observe,
    ))

    assert [item["function"] for item in calls] == [
        "submit_industry_benefit_outline_v2",
        "submit_domain_catalog_selection_v2",
    ]
    assert result["coverage"] == {
        "catalog_total": 504,
        "catalog_supplied": 504,
        "selected_count": 1,
        "binding_complete": True,
    }
    assert progress == [
        ("catalog_loading", "succeeded"),
        ("benefit_outline", "started"),
        ("benefit_outline", "succeeded"),
        ("catalog_mapping", "started"),
        ("catalog_mapping", "succeeded"),
        ("result_validation", "started"),
        ("result_validation", "succeeded"),
        ("resource_published", "succeeded"),
    ]


def test_v2_duplicate_board_id_repair_points_to_the_exact_item() -> None:
    mapping_calls = 0

    async def completion(**kwargs):
        nonlocal mapping_calls
        function_name = _function_name(kwargs)
        if function_name == "submit_industry_benefit_outline_v2":
            return _response_for(kwargs, _outline())
        mapping_calls += 1
        request = json.loads(kwargs["messages"][1]["content"])
        if mapping_calls == 1:
            return _response_for(kwargs, {
                "items": [
                    {"board_id": "BK0566", "role_id": "reducer", "tier": 1},
                    {"board_id": "BK0566", "role_id": "actuator", "tier": 1},
                ],
            })
        assert request["targeted_repair"]["issues"] == [{
            "pointer": "/items/1/board_id",
            "code": "duplicate_board_id",
            "expected": (
                "delete the entire /items/1 object and keep /items/0; "
                "do not invent a replacement board_id"
            ),
            "allowed": [],
            "message": (
                "remove /items/1 because board_id BK0566 duplicates "
                "/items/0/board_id"
            ),
        }]
        return _response_for(kwargs, {
            "items": [
                {"board_id": "BK0566", "role_id": "reducer", "tier": 1},
            ],
        })

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(),
        _catalog([
            {"sector_code": "BK0566", "name": "减速器"},
            {"sector_code": "BK1234", "name": "机器人执行器"},
        ]),
        {"model": "test"},
        completion=completion,
    ))

    assert mapping_calls == 2
    assert result["success"] is True
    assert result["repairs"][0]["succeeded"] is True
    assert [item["board_code"] for item in result["items"]] == ["BK0566"]


def test_v2_schema_failure_after_one_repair_does_not_publish_resource() -> None:
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        function_name = _function_name(kwargs)
        if function_name == "submit_industry_benefit_outline_v2":
            return _response_for(kwargs, _outline())
        request = json.loads(kwargs["messages"][1]["content"])
        assert request["exact_output_schema"] == (
            DomainCatalogSelectionV2.model_json_schema()
        )
        return _content_response('{"items":[')

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(),
        _catalog([{"sector_code": "BK0001", "name": "测试板块"}]),
        {"model": "test"},
        completion=completion,
    ))

    assert calls == 3
    assert result["success"] is False
    assert result["error_code"] == "planner_schema_invalid"
    assert result["repairs"][0]["succeeded"] is False
    assert result["resource_outputs"] == {}
    assert result["items"] == []


def test_v2_repairs_raw_prose_through_exact_json_content_transport() -> None:
    mapping_calls = 0
    invalid_payload = (
        "Let me analyze every catalog item before I submit the mapping. "
        * 400
    )

    async def completion(**kwargs):
        nonlocal mapping_calls
        function_name = _function_name(kwargs)
        if function_name == "submit_industry_benefit_outline_v2":
            return _response_for(kwargs, _outline())
        mapping_calls += 1
        assert "tools" not in kwargs
        request = json.loads(kwargs["messages"][1]["content"])
        if mapping_calls == 1:
            assert "targeted_repair" not in request
            return _content_response(invalid_payload)
        assert request["targeted_repair"]["invalid_payload"] == invalid_payload
        assert request["targeted_repair"]["issues"] == [{
            "pointer": "/",
            "code": "RawProviderPayloadError",
            "expected": "valid JSON matching the supplied schema",
            "allowed": [],
            "message": "Expecting value: line 1 column 1 (char 0)",
        }]
        assert request["exact_output_schema"] == (
            DomainCatalogSelectionV2.model_json_schema()
        )
        assert request["output_transport"]["type"] == "json_content"
        return _content_response(json.dumps({
            "items": [{
                "board_id": "BK0566",
                "role_id": "reducer",
                "tier": 1,
            }],
        }, ensure_ascii=False))

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(),
        _catalog([
            {"sector_code": "BK0566", "name": "减速器"},
            {"sector_code": "BK1234", "name": "机器人执行器"},
        ]),
        {"model": "test"},
        completion=completion,
    ))

    assert mapping_calls == 2
    assert result["success"] is True
    assert result["repairs"][0]["succeeded"] is True
    assert [item["board_code"] for item in result["items"]] == ["BK0566"]


def test_v2_provider_timeout_does_not_stack_transport_and_schema_retries() -> None:
    calls = 0

    async def completion(**_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("504 Gateway Time-out")

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(),
        _catalog([{"sector_code": "BK0001", "name": "测试板块"}]),
        {"model": "test"},
        completion=completion,
    ))

    assert calls == 1
    assert result["success"] is False
    assert result["error_code"] == "synthesis_failed"
    assert result["coverage"] == {
        "catalog_total": 1,
        "catalog_supplied": 0,
        "selected_count": 0,
        "binding_complete": False,
    }
    assert result["repairs"] == []
    assert result["resource_outputs"] == {}


def test_v2_best_one_is_preserved_without_inventing_an_assumption() -> None:
    selection = ResultSelectionSpec(
        mode=ResultSelectionMode.BEST_ONE,
        max_items=1,
    )

    async def completion(**kwargs):
        function_name = _function_name(kwargs)
        if function_name == "submit_industry_benefit_outline_v2":
            return _response_for(kwargs, _outline())
        return _response_for(kwargs, {
            "items": [{
                "board_id": "BK0566",
                "role_id": "reducer",
                "tier": 1,
            }],
        })

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(selection=selection),
        _catalog([
            {"sector_code": "BK0566", "name": "减速器"},
            {"sector_code": "BK1234", "name": "机器人执行器"},
        ]),
        {"model": "test"},
        completion=completion,
    ))

    resource = DomainCollectionV2.model_validate(
        result["semantic_artifacts"][0]
    )
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
        return _response_for(kwargs, {
            "items": [{
                "board_id": "BK0566",
                "role_id": "reducer",
                "tier": 1,
            }],
        })

    evidence = _catalog([{"sector_code": "BK0566", "name": "减速器"}])
    with patch(
        "src.storage.manager.DatabaseManager.get_instance",
        return_value=FakeDatabase(),
    ):
        first = asyncio.run(process_task_result(
            "ranked_domain_selection",
            _task(),
            evidence,
            {"model": "test", "api_base": "https://example.invalid"},
            completion=completion,
        ))
        second = asyncio.run(process_task_result(
            "ranked_domain_selection",
            _task(),
            evidence,
            {"model": "test", "api_base": "https://example.invalid"},
            completion=completion,
        ))

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

    assert domain_board_catalog_snapshot_id(first) == (
        domain_board_catalog_snapshot_id(reordered)
    )
    assert domain_board_catalog_snapshot_id(first) != (
        domain_board_catalog_snapshot_id(changed)
    )
