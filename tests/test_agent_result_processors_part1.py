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



"""Focused test slice 1; shared fixtures remain local to this slice."""

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
def test_ranked_domain_processor_does_not_fallback_without_project_catalog() -> None:
    async def completion(**_kwargs):
        raise AssertionError("missing catalog must not invoke a public ranking model")

    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {
                    "query": "人形机器人哪些领域最受益",
                    "domains": [
                        {
                            "label": "火星机器人",
                            "board_queries": [],
                            "mapping_type": "unresolved",
                            "rationale": "项目目录无覆盖",
                            "unresolved_parts": ["火星机器人"],
                        }
                    ],
                },
            ),
            [
                {
                    "tool": "search_financial_news",
                    "arguments": {"subjects": ["人形机器人"]},
                    "result": {
                        "success": True,
                        "retrieved_at": "2026-07-25T10:00:00",
                        "items": [
                            {
                                "title": "人形机器人核心零部件",
                                "summary": (
                                    "灵巧手是人形机器人实现精细操作的核心部件。" "六维力传感器提供腕部多轴力控反馈。"
                                ),
                                "url": "https://example.com/humanoid-components",
                                "source": "测试财经",
                                "published": "2026-07-24",
                            }
                        ],
                    },
                }
            ],
            {"model": "test"},
            completion=completion,
        )
    )

    assert result["success"] is False
    assert result["source_scope"] == "project_live_board_catalog"
    assert result["resource_outputs"] == {}
    assert "未改用公开来源" in result["errors"][0]

def test_public_domain_processor_reports_failed_batches_without_pending_state() -> None:
    async def completion(**_kwargs):
        raise RuntimeError("semantic provider unavailable")

    result = asyncio.run(
        result_processors_module._rank_public_industry_domains(
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {"query": "人形机器人哪些领域最受益", "domains": ["人形机器人"]},
            ),
            [
                {
                    "tool": "search_financial_news",
                    "result": {
                        "items": [
                            {
                                "title": "人形机器人核心零部件",
                                "summary": "灵巧手是人形机器人实现精细操作的核心部件。",
                                "url": "https://example.com/humanoid-components",
                            }
                        ]
                    },
                }
            ],
            {"model": "test"},
            completion,
        )
    )

    assert result["success"] is False
    assert result["errors"] == ["来源中没有通过原文校验的受益领域排序。 1 个语义批次未完成。"]

@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_ranked_domain_processor_selects_only_live_project_boards() -> None:
    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        assert set(request["project_boards"]) == {
            "人形机器人",
            "机器人执行器",
            "减速器",
        }
        return _response(
            "submit_ranked_project_boards",
            {
                "boards": [
                    {
                        "board_name": "机器人执行器",
                        "tier": 1,
                        "rationale": "执行机构直接承接运动控制价值量",
                        "confidence": 0.96,
                    },
                    {
                        "board_name": "减速器",
                        "tier": 1,
                        "rationale": "关节传动核心部件",
                        "confidence": 0.94,
                    },
                    {
                        "board_name": "模型自造板块",
                        "tier": 1,
                        "rationale": "不应通过目录校验",
                        "confidence": 0.99,
                    },
                ],
            },
        )

    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {
                    "query": "人形机器人哪些领域最受益",
                    "domains": [
                        {
                            "label": "人形机器人",
                            "board_queries": ["人形机器人"],
                            "mapping_type": "catalog_binding",
                            "rationale": "精确对应",
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
                        "data_time": "2026-07-25T10:00:00+08:00",
                        "boards": [
                            {"sector_code": "BK1184", "name": "人形机器人"},
                            {"sector_code": "BK1234", "name": "机器人执行器"},
                            {"sector_code": "BK0566", "name": "减速器"},
                        ],
                    },
                }
            ],
            {"model": "test"},
            completion=completion,
        )
    )

    assert result["success"] is True
    assert result["source_scope"] == "project_live_board_catalog"
    assert [item["label"] for item in result["items"]] == [
        "机器人执行器",
        "减速器",
    ]
    assert result["resource_outputs"]["domain_collection"] == [
        {
            "label": "机器人执行器",
            "board_queries": ["机器人执行器"],
            "mapping_type": "catalog_binding",
            "rationale": "执行机构直接承接运动控制价值量",
            "unresolved_parts": [],
        },
        {
            "label": "减速器",
            "board_queries": ["减速器"],
            "mapping_type": "catalog_binding",
            "rationale": "关节传动核心部件",
            "unresolved_parts": [],
        },
    ]

@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_enforces_best_one_after_model_ranking() -> None:
    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        assert request["result_selection"] == {
            "mode": "best_one",
            "max_items": 1,
        }
        return _response(
            "submit_ranked_project_boards",
            {
                "boards": [
                    {
                        "board_name": "机器人执行器",
                        "tier": 1,
                        "rationale": "关节驱动直接承接价值量",
                        "confidence": 0.97,
                    },
                    {
                        "board_name": "减速器",
                        "tier": 1,
                        "rationale": "关节传动核心部件",
                        "confidence": 0.94,
                    },
                    {
                        "board_name": "传感器",
                        "tier": 2,
                        "rationale": "提供感知能力",
                        "confidence": 0.92,
                    },
                ],
            },
        )

    result = asyncio.run(
        process_task_result(
            "ranked_domain_selection",
            _task(
                StandardTaskKind.INDUSTRY_RESEARCH,
                {
                    "query": "人形机器人领域最受益的一个方向",
                    "domains": [
                        {
                            "label": "人形机器人",
                            "board_queries": ["人形机器人"],
                            "mapping_type": "catalog_binding",
                            "rationale": "精确对应",
                            "unresolved_parts": [],
                        }
                    ],
                },
                result_selection=ResultSelectionSpec(
                    mode=ResultSelectionMode.BEST_ONE,
                    max_items=1,
                ),
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
                        "data_time": "2026-07-25T10:00:00+08:00",
                        "boards": [
                            {"sector_code": "BK1234", "name": "机器人执行器"},
                            {"sector_code": "BK0566", "name": "减速器"},
                            {"sector_code": "BK0905", "name": "传感器"},
                        ],
                    },
                }
            ],
            {"model": "test"},
            completion=completion,
        )
    )

    assert result["result_selection"] == {
        "mode": "best_one",
        "max_items": 1,
    }
    assert [item["label"] for item in result["items"]] == ["机器人执行器"]
    assert result["resource_outputs"]["domain_collection"] == [
        {
            "label": "机器人执行器",
            "board_queries": ["机器人执行器"],
            "mapping_type": "catalog_binding",
            "rationale": "关节驱动直接承接价值量",
            "unresolved_parts": [],
        }
    ]
    assert result["semantic_artifacts"][0]["groups"] == [
        {
            "tier": 1,
            "domains": [
                {
                    "label": "机器人执行器",
                    "tier": 1,
                    "board_queries": ["机器人执行器"],
                    "mapping_type": "catalog_binding",
                    "rationale": "关节驱动直接承接价值量",
                    "unresolved_parts": [],
                }
            ],
        }
    ]

@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_uses_one_global_call_for_current_catalog_size() -> None:
    calls: list[dict] = []

    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        calls.append(request)
        assert len(request["project_boards"]) == 495
        assert request["project_boards"] == [f"板块{index}" for index in range(495)]
        return _response(
            "submit_ranked_project_boards",
            {
                "boards": [
                    {
                        "board_name": "板块200",
                        "tier": 1,
                        "rationale": "在完整目录中完成全局比较后的核心方向",
                        "confidence": 0.96,
                    }
                ],
            },
        )

    boards = [{"sector_code": f"BK{index:04}", "name": f"板块{index}"} for index in range(495)]
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

    assert len(calls) == 1
    assert result["success"] is True
    assert result["coverage_complete"] is True
    assert result["ranking_complete"] is True
    assert result["batch_completed"] == result["batch_total"] == 1
