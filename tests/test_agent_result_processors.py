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
        normalized_parameters.setdefault("evidence_context", {
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
        })
    return ResolvedTask(candidate=StandardTask(
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
    ), symbols=symbols)


def test_ranked_domain_processor_does_not_fallback_without_project_catalog() -> None:
    async def completion(**_kwargs):
        raise AssertionError("missing catalog must not invoke a public ranking model")

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(StandardTaskKind.INDUSTRY_RESEARCH, {
            "query": "人形机器人哪些领域最受益",
            "domains": [{
                "label": "火星机器人",
                "board_queries": [],
                "mapping_type": "unresolved",
                "rationale": "项目目录无覆盖",
                "unresolved_parts": ["火星机器人"],
            }],
        }),
        [{
            "tool": "search_financial_news",
            "arguments": {"subjects": ["人形机器人"]},
            "result": {
                "success": True,
                "retrieved_at": "2026-07-25T10:00:00",
                "items": [{
                    "title": "人形机器人核心零部件",
                    "summary": (
                        "灵巧手是人形机器人实现精细操作的核心部件。"
                        "六维力传感器提供腕部多轴力控反馈。"
                    ),
                    "url": "https://example.com/humanoid-components",
                    "source": "测试财经",
                    "published": "2026-07-24",
                }],
            },
        }],
        {"model": "test"},
        completion=completion,
    ))

    assert result["success"] is False
    assert result["source_scope"] == "project_live_board_catalog"
    assert result["resource_outputs"] == {}
    assert "未改用公开来源" in result["errors"][0]


@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_ranked_domain_processor_selects_only_live_project_boards() -> None:
    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        assert set(request["project_boards"]) == {
            "人形机器人",
            "机器人执行器",
            "减速器",
        }
        return _response("submit_ranked_project_boards", {
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
        })

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(StandardTaskKind.INDUSTRY_RESEARCH, {
            "query": "人形机器人哪些领域最受益",
            "domains": [{
                "label": "人形机器人",
                "board_queries": ["人形机器人"],
                "mapping_type": "catalog_binding",
                "rationale": "精确对应",
                "unresolved_parts": [],
            }],
        }),
        [{
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
        }],
        {"model": "test"},
        completion=completion,
    ))

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
        return _response("submit_ranked_project_boards", {
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
        })

    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(
            StandardTaskKind.INDUSTRY_RESEARCH,
            {
                "query": "人形机器人领域最受益的一个方向",
                "domains": [{
                    "label": "人形机器人",
                    "board_queries": ["人形机器人"],
                    "mapping_type": "catalog_binding",
                    "rationale": "精确对应",
                    "unresolved_parts": [],
                }],
            },
            result_selection=ResultSelectionSpec(
                mode=ResultSelectionMode.BEST_ONE,
                max_items=1,
            ),
        ),
        [{
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
        }],
        {"model": "test"},
        completion=completion,
    ))

    assert result["result_selection"] == {
        "mode": "best_one",
        "max_items": 1,
    }
    assert [item["label"] for item in result["items"]] == ["机器人执行器"]
    assert result["resource_outputs"]["domain_collection"] == [{
        "label": "机器人执行器",
        "board_queries": ["机器人执行器"],
        "mapping_type": "catalog_binding",
        "rationale": "关节驱动直接承接价值量",
        "unresolved_parts": [],
    }]
    assert result["semantic_artifacts"][0]["groups"] == [{
        "tier": 1,
        "domains": [{
            "label": "机器人执行器",
            "tier": 1,
            "board_queries": ["机器人执行器"],
            "mapping_type": "catalog_binding",
            "rationale": "关节驱动直接承接价值量",
            "unresolved_parts": [],
        }],
    }]


@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_uses_one_global_call_for_current_catalog_size() -> None:
    calls: list[dict] = []

    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        calls.append(request)
        assert len(request["project_boards"]) == 495
        assert request["project_boards"] == [
            f"板块{index}"
            for index in range(495)
        ]
        return _response("submit_ranked_project_boards", {
            "boards": [{
                "board_name": "板块200",
                "tier": 1,
                "rationale": "在完整目录中完成全局比较后的核心方向",
                "confidence": 0.96,
            }],
        })

    boards = [
        {"sector_code": f"BK{index:04}", "name": f"板块{index}"}
        for index in range(495)
    ]
    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(StandardTaskKind.INDUSTRY_RESEARCH, {
            "query": "测试产业哪些领域最受益",
            "domains": ["测试产业"],
        }),
        [{
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
        }],
        {"model": "test"},
        completion=completion,
    ))

    assert len(calls) == 1
    assert result["success"] is True
    assert result["coverage_complete"] is True
    assert result["ranking_complete"] is True
    assert result["batch_completed"] == result["batch_total"] == 1


@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_sends_large_catalog_directly_to_model() -> None:
    calls: list[dict[str, Any]] = []
    progress_updates: list[tuple[int, int]] = []

    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        calls.append(request)
        assert len(request["project_boards"]) == 481
        return _response("submit_ranked_project_boards", {
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
        })

    async def progress(
        completed: int,
        total: int,
        _batch_result: dict,
    ) -> None:
        progress_updates.append((completed, total))

    boards = [
        {"sector_code": f"BK{index:04}", "name": f"板块{index}"}
        for index in range(481)
    ]
    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(StandardTaskKind.INDUSTRY_RESEARCH, {
            "query": "测试产业哪些领域最受益",
            "domains": [{
                "label": "测试产业",
                "board_queries": ["板块0"],
                "mapping_type": "catalog_binding",
                "rationale": "测试绑定",
                "unresolved_parts": [],
            }],
        }),
        [{
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
        }],
        {"model": "test"},
        completion=completion,
        progress=progress,
    ))

    assert len(calls) == 1
    assert calls[0]["project_boards"] == [
        f"板块{index}"
        for index in range(481)
    ]
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

    boards = [
        {"sector_code": f"BK{index:04}", "name": f"板块{index}"}
        for index in range(101)
    ]
    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(StandardTaskKind.INDUSTRY_RESEARCH, {
            "query": "测试产业哪些领域最受益",
            "domains": [{
                "label": "测试产业",
                "board_queries": ["板块0"],
                "mapping_type": "catalog_binding",
                "rationale": "测试绑定",
                "unresolved_parts": [],
            }],
        }),
        [{
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
        }],
        {"model": "test"},
        completion=completion,
    ))

    assert result["success"] is False
    assert result["partial"] is False
    assert result["items"] == []
    assert result["coverage_complete"] is False
    assert result["batch_completed"] == 0
    assert result["batch_total"] == 1
    assert result["resource_outputs"] == {}
    assert len(calls) == 1
    assert result["failed_batches"] == [{
        "batch": 1,
        "error_code": "semantic_invalid_response",
        "error_type": "ValueError",
    }]


@patch.dict(os.environ, {"AGENT_INDUSTRY_CATALOG_MAPPING_MODE": "v1"})
def test_project_board_processor_waits_for_one_provider_response_without_local_timeout() -> None:
    calls = 0

    async def completion(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return _response("submit_ranked_project_boards", {
            "boards": [{
                "board_name": "板块100",
                "tier": 1,
                "rationale": "完整目录全局判断后的核心方向",
                "confidence": 0.96,
            }],
        })

    boards = [
        {"sector_code": f"BK{index:04}", "name": f"板块{index}"}
        for index in range(201)
    ]
    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(StandardTaskKind.INDUSTRY_RESEARCH, {
            "query": "测试产业哪些领域最受益",
            "domains": ["测试产业"],
        }),
        [{
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
        }],
        {"model": "test"},
        completion=completion,
    ))

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
            return _response("submit_ranked_project_boards", {
                "boards": [{
                    "board_name": selected,
                    "tier": 1,
                    "rationale": "完整目录中的有效候选",
                    "confidence": 0.9,
                }],
            })
        finally:
            active -= 1

    boards = [
        {"sector_code": f"BK{index:04}", "name": f"板块{index}"}
        for index in range(301)
    ]
    result = asyncio.run(process_task_result(
        "ranked_domain_selection",
        _task(StandardTaskKind.INDUSTRY_RESEARCH, {
            "query": "测试产业哪些领域最受益",
            "domains": ["测试产业"],
        }),
        [{
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
        }],
        {"model": "test"},
        completion=completion,
    ))

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
        result = asyncio.run(process_task_result(
            "ranked_domain_selection",
            _task(StandardTaskKind.INDUSTRY_RESEARCH, {
                "query": "人型机器人哪些领域最核心最受益",
                "domains": ["人型机器人"],
            }),
            [{
                "tool": "get_domain_board_catalog",
                "arguments": {},
                "result": {
                    "success": True,
                    "partial": False,
                    "errors": [],
                    "warnings": [],
                    "source": "测试实时板块目录",
                    "data_time": "2026-07-28",
                    "boards": [{
                        "sector_code": "BK1184",
                        "name": "人形机器人",
                    }],
                },
            }],
            {
                "model": "test",
                "api_base": "https://example.invalid",
            },
            completion=completion,
        ))

    assert result["success"] is True
    assert result["cache_hit"] is True
    assert result["coverage_complete"] is True
    assert result["resource_outputs"]["domain_collection"] == [{
        "label": "人形机器人",
    }]


def test_company_evidence_processor_analyzes_each_candidate_independently() -> None:
    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        symbol = request["requested_company"]["symbol"]
        assert {
            document["text"].split(" ", 1)[0]
            for document in request["evidence_documents"]
        } == {symbol}
        domain = "六维力传感器" if symbol == "300007" else "谐波减速器"
        return _response("submit_company_theme_analysis", {
            "symbol": symbol,
            "company_name": symbol,
            "verdict": "pass",
            "theme_fit": "exact",
            "development_level": "mass_production",
            "matched_domains": [domain],
            "reason": "已形成目标产品批量交付",
            "evidence": [{
                "source_id": "s1",
                "domain": domain,
            }],
            "confidence": 0.96,
        })

    evidence = [
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": symbol},
            "result": {
                "success": True,
                "evidence_documents": [{
                    "source_id": "s1",
                    "source_type": "company_news",
                    "title": "业务进展",
                    "text": text,
                    "source_name": "测试财经",
                    "source_url": f"https://example.com/{symbol}",
                    "source_date": "2026-07-24",
                }],
                "source_status": {},
            },
        }
        for symbol, text in (
            ("300007", "300007 汉威科技六维力传感器已向人形机器人厂商批量供货"),
            ("301368", "301368 丰立智能谐波减速器已具备批量交付能力"),
        )
    ]

    result = asyncio.run(process_task_result(
        "company_evidence_binding",
        _task(StandardTaskKind.THEME_BUSINESS_EVIDENCE, {
            "domains": [
                {"label": "六维力传感器"},
                {"label": "谐波减速器"},
            ],
            "candidate_scope": "candidate_collection",
            "query": "有哪些公司正在大力发展",
        }, symbols=("300007", "301368")),
        evidence,
        {"model": "test"},
        completion=completion,
    ))

    assert result["success"] is True
    assert [item["symbol"] for item in result["items"]] == [
        "300007",
        "301368",
    ]
    assert {
        item["symbol"]
        for item in result["resource_outputs"]["security_collection"]
    } == {"300007", "301368"}
    assert result["candidate_count"] == 2
    assert result["analyzed_candidate_count"] == 2
    assert result["candidate_coverage_complete"] is True
    assert result["screening_mode"] == "per_security_full_analysis"
    assert result["verdict_counts"] == {
        "pass": 2,
        "fail": 0,
        "insufficient": 0,
        "error": 0,
    }


def test_company_evidence_repairs_missing_function_payload_once() -> None:
    calls: list[dict] = []
    valid_payload = {
        "symbol": "301368",
        "company_name": "301368",
        "verdict": "pass",
        "theme_fit": "exact",
        "development_level": "mass_production",
        "matched_domains": ["减速器"],
        "reason": "谐波减速器已具备批量交付能力",
        "evidence": [{
            "source_id": "s1",
            "domain": "减速器",
        }],
        "confidence": 0.96,
    }

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            schema = kwargs["tools"][0]["function"]["parameters"]
            assert schema == (
                result_processors_module.CompanyThemeAnalysisCandidate
                .model_json_schema()
            )
            return _response_without_payload()
        assert "tools" not in kwargs
        repair_request = json.loads(kwargs["messages"][1]["content"])
        assert repair_request["targeted_repair"]["invalid_payload"] is None
        assert repair_request["targeted_repair"]["issues"][0]["pointer"] == "/"
        return _response_content(json.dumps(valid_payload, ensure_ascii=False))

    evidence = [{
        "tool": "get_company_theme_evidence",
        "arguments": {"symbol": "301368"},
        "result": {
            "success": True,
            "evidence_documents": [{
                "source_id": "s1",
                "source_type": "company_news",
                "title": "业务进展",
                "text": "丰立智能谐波减速器已具备批量交付能力",
                "source_name": "测试财经",
            }],
            "source_status": {},
        },
    }]

    result = asyncio.run(process_task_result(
        "company_evidence_binding",
        _task(
            StandardTaskKind.THEME_BUSINESS_EVIDENCE,
            {
                "domains": [{"label": "减速器"}],
                "candidate_scope": "candidate_collection",
            },
            symbols=("301368",),
        ),
        evidence,
        {"model": "test"},
        completion=completion,
    ))

    assert len(calls) == 2
    assert result["verdict_counts"]["pass"] == 1
    assert result["verdict_counts"]["error"] == 0
    assert result["company_results"][0]["model_repair"]["succeeded"] is True


def test_company_evidence_repairs_truncated_function_json_once() -> None:
    calls: list[dict] = []
    valid_payload = {
        "symbol": "301368",
        "company_name": "301368",
        "verdict": "fail",
        "theme_fit": "none",
        "development_level": "none",
        "matched_domains": [],
        "reason": "现有证据证明业务不属于目标减速器方向",
        "evidence": [],
        "confidence": 0.9,
    }

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            function = SimpleNamespace(
                name="submit_company_theme_analysis",
                arguments='{"symbol":"301368","reason":"未结束',
            )
            message = SimpleNamespace(
                tool_calls=[SimpleNamespace(function=function)],
                content=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)]
            )
        repair_request = json.loads(kwargs["messages"][1]["content"])
        invalid_payload = repair_request["targeted_repair"]["invalid_payload"]
        assert invalid_payload == '{"symbol":"301368","reason":"未结束'
        assert "tools" not in kwargs
        return _response_content(json.dumps(valid_payload, ensure_ascii=False))

    evidence = [{
        "tool": "get_company_theme_evidence",
        "arguments": {"symbol": "301368"},
        "result": {
            "success": True,
            "evidence_documents": [{
                "source_id": "s1",
                "source_type": "company_news",
                "text": "丰立智能主营精密传动部件。",
                "source_name": "测试财经",
            }],
            "source_status": {},
        },
    }]

    result = asyncio.run(process_task_result(
        "company_evidence_binding",
        _task(
            StandardTaskKind.THEME_BUSINESS_EVIDENCE,
            {
                "domains": [{"label": "减速器"}],
                "candidate_scope": "candidate_collection",
            },
            symbols=("301368",),
        ),
        evidence,
        {"model": "test"},
        completion=completion,
    ))

    assert len(calls) == 2
    assert result["verdict_counts"]["fail"] == 1
    assert result["verdict_counts"]["error"] == 0


def test_company_evidence_repairs_unknown_evidence_source_once() -> None:
    calls: list[dict] = []
    source_quote = "丰立智能谐波减速器已具备批量交付能力"

    async def completion(**kwargs):
        calls.append(kwargs)
        payload = {
            "symbol": "301368",
            "company_name": "301368",
            "verdict": "pass",
            "theme_fit": "exact",
            "development_level": "mass_production",
            "matched_domains": ["减速器"],
            "reason": "谐波减速器已具备批量交付能力",
            "evidence": [{
                "source_id": "unknown" if len(calls) == 1 else "s1",
                "domain": "减速器",
            }],
            "confidence": 0.96,
        }
        if len(calls) == 2:
            repair_request = json.loads(kwargs["messages"][1]["content"])
            issue_paths = {
                issue["pointer"]
                for issue in repair_request["targeted_repair"]["issues"]
            }
            assert "/evidence/0/source_id" in issue_paths
            assert "/evidence" in issue_paths
        return _response("submit_company_theme_analysis", payload)

    evidence = [{
        "tool": "get_company_theme_evidence",
        "arguments": {"symbol": "301368"},
        "result": {
            "success": True,
            "evidence_documents": [{
                "source_id": "s1",
                "source_type": "company_news",
                "text": source_quote,
                "source_name": "测试财经",
            }],
            "source_status": {},
        },
    }]

    result = asyncio.run(process_task_result(
        "company_evidence_binding",
        _task(
            StandardTaskKind.THEME_BUSINESS_EVIDENCE,
            {
                "domains": [{"label": "减速器"}],
                "candidate_scope": "candidate_collection",
            },
            symbols=("301368",),
        ),
        evidence,
        {"model": "test"},
        completion=completion,
    ))

    assert len(calls) == 2
    assert result["verdict_counts"]["pass"] == 1
    assert result["company_results"][0]["model_repair"]["succeeded"] is True


def test_company_evidence_gives_all_488_candidates_an_independent_terminal_state() -> None:
    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        symbol = request["requested_company"]["symbol"]
        passed = symbol == "301368"
        domain = "减速器" if passed else "传感器"
        quote = request["evidence_documents"][0]["text"]
        return _response("submit_company_theme_analysis", {
            "symbol": symbol,
            "company_name": symbol,
            "verdict": "pass" if passed else "fail",
            "theme_fit": "exact" if passed else "adjacent",
            "development_level": "layout" if passed else "order",
            "matched_domains": [domain] if passed else [],
            "reason": (
                "正在推进人形机器人减速器产品"
                if passed
                else "证据只涉及其他应用场景"
            ),
            "evidence": [{
                "source_id": "s1",
                "domain": domain,
            }],
            "confidence": 0.96,
        })

    evidence = [
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": "688728"},
            "result": {
                "success": True,
                "evidence_documents": [{
                    "source_id": "s1",
                    "text": "688728 格科微图像传感器产品获得手机品牌客户订单",
                    "source_type": "company_news",
                    "source_name": "测试财经",
                }],
                "source_status": {},
            },
        },
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": "301368"},
            "result": {
                "success": True,
                "evidence_documents": [{
                    "source_id": "s1",
                    "text": "301368 丰立智能正推进人形机器人谐波减速器产品开发",
                    "source_type": "company_news",
                    "source_name": "测试财经",
                }],
                "source_status": {},
            },
        },
    ]
    filler_symbols = tuple(f"{100000 + index:06d}" for index in range(486))
    evidence.extend({
        "tool": "get_company_theme_evidence",
        "arguments": {"symbol": symbol},
        "result": {
            "success": True,
            "evidence_documents": [],
            "source_status": {},
        },
    } for symbol in filler_symbols)
    task = _task(
        StandardTaskKind.THEME_BUSINESS_EVIDENCE,
        {
            "domains": [{"label": "传感器"}, {"label": "减速器"}],
            "candidate_scope": "candidate_collection",
            "evidence_context": {
                "target_topics": ["人形机器人"],
                "domain_theses": [
                    {
                        "label": "传感器",
                        "rationale": "力觉、触觉和视觉反馈服务于人形机器人感知",
                        "tier": 1,
                    },
                    {
                        "label": "减速器",
                        "rationale": "关节传动核心部件",
                        "tier": 1,
                    },
                ],
            },
        },
        symbols=("688728", "301368", *filler_symbols),
    )
    result = asyncio.run(process_task_result(
        "company_evidence_binding",
        task,
        evidence,
        {"model": "test"},
        completion=completion,
    ))

    assert result["candidate_count"] == 488
    assert result["analyzed_candidate_count"] == 488
    assert result["candidate_coverage_complete"] is True
    assert result["partial"] is True
    assert [item["symbol"] for item in result["items"]] == ["301368"]
    assert result["verdict_counts"] == {
        "pass": 1,
        "fail": 1,
        "insufficient": 486,
        "error": 0,
    }
    assert len(result["company_results"]) == 488
