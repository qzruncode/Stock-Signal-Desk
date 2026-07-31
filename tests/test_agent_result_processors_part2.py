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



"""Focused test slice 2; shared fixtures remain local to this slice."""

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
@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_sends_large_catalog_directly_to_model() -> None:
    calls: list[dict[str, Any]] = []
    progress_updates: list[tuple[int, int]] = []

    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        calls.append(request)
        assert len(request["project_boards"]) == 481
        return _response(
            "submit_ranked_project_boards",
            {
                "boards": [
                    {
                        "board_name": "板块200",
                        "tier": 1,
                        "rationale": "完整目录全局判断后的最直接受益板块",
                        "confidence": 0.97,
                    },
                    {
                        "board_name": "板块100",
                        "tier": 2,
                        "rationale": "完整目录全局判断后的次直接受益板块",
                        "confidence": 0.92,
                    },
                ],
            },
        )

    async def progress(
        completed: int,
        total: int,
        _batch_result: dict,
    ) -> None:
        progress_updates.append((completed, total))

    boards = [{"sector_code": f"BK{index:04}", "name": f"板块{index}"} for index in range(481)]
    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {
                    "query": "测试产业哪些领域最受益",
                    "domains": [
                        {
                            "label": "测试产业",
                            "board_queries": ["板块0"],
                            "mapping_type": "catalog_binding",
                            "rationale": "测试绑定",
                            "unresolved_parts": [],
                        }
                    ],
                },
            ),
            [
                {
                    "tool": "get_domain_board_catalog",
                    "arguments": {},
                    "result": {
                        "success": True,
                        "partial": False,
                        "errors": [],
                        "warnings": [],
                        "source": "测试实时板块目录",
                        "data_time": "2026-07-28",
                        "boards": boards,
                    },
                }
            ],
            {"model": "test"},
            completion=completion,
            progress=progress,
        )
    )

    assert len(calls) == 1
    assert calls[0]["project_boards"] == [f"板块{index}" for index in range(481)]
    assert [item["label"] for item in result["items"]] == [
        "板块200",
        "板块100",
    ]
    assert result["partial"] is False
    assert result["coverage_complete"] is True
    assert result["ranking_complete"] is True
    assert result["batch_completed"] == result["batch_total"] == 1
    assert progress_updates == [(1, 1)]

@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_does_not_publish_invalid_full_catalog_result() -> None:
    calls: list[dict] = []

    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        calls.append(request)
        assert len(request["project_boards"]) == 101
        return _response_content("")

    boards = [{"sector_code": f"BK{index:04}", "name": f"板块{index}"} for index in range(101)]
    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {
                    "query": "测试产业哪些领域最受益",
                    "domains": [
                        {
                            "label": "测试产业",
                            "board_queries": ["板块0"],
                            "mapping_type": "catalog_binding",
                            "rationale": "测试绑定",
                            "unresolved_parts": [],
                        }
                    ],
                },
            ),
            [
                {
                    "tool": "get_domain_board_catalog",
                    "arguments": {},
                    "result": {
                        "success": True,
                        "partial": False,
                        "errors": [],
                        "warnings": [],
                        "source": "测试实时板块目录",
                        "data_time": "2026-07-28",
                        "boards": boards,
                    },
                }
            ],
            {"model": "test"},
            completion=completion,
        )
    )

    assert result["success"] is False
    assert result["partial"] is False
    assert result["items"] == []
    assert result["coverage_complete"] is False
    assert result["batch_completed"] == 0
    assert result["batch_total"] == 1
    assert result["resource_outputs"] == {}
    assert len(calls) == 1
    assert result["failed_batches"] == [
        {
            "batch": 1,
            "error_code": "semantic_invalid_response",
            "error_type": "ValueError",
        }
    ]

@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_waits_for_one_provider_response_without_local_timeout() -> None:
    calls = 0

    async def completion(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return _response(
            "submit_ranked_project_boards",
            {
                "boards": [
                    {
                        "board_name": "板块100",
                        "tier": 1,
                        "rationale": "完整目录全局判断后的核心方向",
                        "confidence": 0.96,
                    }
                ],
            },
        )

    boards = [{"sector_code": f"BK{index:04}", "name": f"板块{index}"} for index in range(201)]
    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {
                    "query": "测试产业哪些领域最受益",
                    "domains": ["测试产业"],
                },
            ),
            [
                {
                    "tool": "get_domain_board_catalog",
                    "arguments": {},
                    "result": {
                        "success": True,
                        "partial": False,
                        "errors": [],
                        "warnings": [],
                        "source": "测试实时板块目录",
                        "data_time": "2026-07-28",
                        "boards": boards,
                    },
                }
            ],
            {"model": "test"},
            completion=completion,
        )
    )

    assert calls == 1
    assert result["success"] is True
    assert result["coverage_complete"] is True
    assert result["batch_total"] == 1
    assert result["batch_completed"] == 1
    assert result["resource_outputs"]["domain_collection"][0]["label"] == "板块100"

@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_does_not_split_the_complete_catalog() -> None:
    active = 0
    max_active = 0

    async def completion(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.015)
            request = json.loads(kwargs["messages"][1]["content"])
            assert len(request["project_boards"]) == 301
            selected = request["project_boards"][0]
            return _response(
                "submit_ranked_project_boards",
                {
                    "boards": [
                        {
                            "board_name": selected,
                            "tier": 1,
                            "rationale": "完整目录中的有效候选",
                            "confidence": 0.9,
                        }
                    ],
                },
            )
        finally:
            active -= 1

    boards = [{"sector_code": f"BK{index:04}", "name": f"板块{index}"} for index in range(301)]
    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {
                    "query": "测试产业哪些领域最受益",
                    "domains": ["测试产业"],
                },
            ),
            [
                {
                    "tool": "get_domain_board_catalog",
                    "arguments": {},
                    "result": {
                        "success": True,
                        "partial": False,
                        "errors": [],
                        "warnings": [],
                        "source": "测试实时板块目录",
                        "data_time": "2026-07-28",
                        "boards": boards,
                    },
                }
            ],
            {"model": "test"},
            completion=completion,
        )
    )

    assert result["success"] is True
    assert result["coverage_complete"] is True
    assert result["batch_completed"] == result["batch_total"] == 1
    assert max_active == 1

@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_reuses_only_complete_snapshot_cache() -> None:
    cached_result = {
        "success": True,
        "partial": False,
        "errors": [],
        "warnings": [],
        "items": [{"label": "人形机器人"}],
        "semantic_artifacts": [],
        "resource_outputs": {
            "domain_collection": [{"label": "人形机器人"}],
        },
        "coverage_complete": True,
        "ranking_complete": True,
        "catalog_snapshot_id": (
            result_processors_module._project_board_snapshot_id(
                [{"sector_code": "BK1184", "name": "人形机器人"}],
                {
                    "source": "测试实时板块目录",
                    "data_time": "2026-07-28",
                },
            )
        ),
    }
    fake_db = SimpleNamespace(
        get_tool_cache=lambda _key: {
            "payload": json.dumps(
                cached_result,
                ensure_ascii=False,
            ).encode("utf-8"),
            "updated_at": None,
        },
    )

    async def completion(**_kwargs):
        raise AssertionError("complete snapshot cache must bypass the model")

    with patch(
        "src.storage.manager.DatabaseManager.get_instance",
        return_value=fake_db,
    ):
        result = asyncio.run(
            process_task_result(
                "ranked_domain_selection",
                _task(
                    StandardTaskKind.INDUSTRY_RESEARCH,
                    {
                        "query": "人型机器人哪些领域最核心最受益",
                        "domains": ["人型机器人"],
                    },
                ),
                [
                    {
                        "tool": "get_domain_board_catalog",
                        "arguments": {},
                        "result": {
                            "success": True,
                            "partial": False,
                            "errors": [],
                            "warnings": [],
                            "source": "测试实时板块目录",
                            "data_time": "2026-07-28",
                            "boards": [
                                {
                                    "sector_code": "BK1184",
                                    "name": "人形机器人",
                                }
                            ],
                        },
                    }
                ],
                {
                    "model": "test",
                    "api_base": "https://example.invalid",
                },
                completion=completion,
            )
        )

    assert result["success"] is True
    assert result["cache_hit"] is True
    assert result["coverage_complete"] is True
    assert result["resource_outputs"]["domain_collection"] == [
        {
            "label": "人形机器人",
        }
    ]
