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



"""Focused test slice 3; shared fixtures remain local to this slice."""

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
def test_company_evidence_processor_analyzes_each_candidate_independently() -> None:
    async def completion(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        symbol = request["requested_company"]["symbol"]
        assert {document["text"].split(" ", 1)[0] for document in request["evidence_documents"]} == {symbol}
        domain = "六维力传感器" if symbol == "300007" else "谐波减速器"
        return _response(
            "submit_company_theme_analysis",
            {
                "symbol": symbol,
                "company_name": symbol,
                "verdict": "pass",
                "theme_fit": "exact",
                "development_level": "mass_production",
                "matched_domains": [domain],
                "reason": "已形成目标产品批量交付",
                "evidence": [
                    {
                        "source_id": "s1",
                        "domain": domain,
                    }
                ],
                "confidence": 0.96,
            },
        )

    evidence = [
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": symbol},
            "result": {
                "success": True,
                "evidence_documents": [
                    {
                        "source_id": "s1",
                        "source_type": "company_news",
                        "title": "业务进展",
                        "text": text,
                        "source_name": "测试财经",
                        "source_url": f"https://example.com/{symbol}",
                        "source_date": "2026-07-24",
                    }
                ],
                "source_status": {},
            },
        }
        for symbol, text in (
            ("300007", "300007 汉威科技六维力传感器已向人形机器人厂商批量供货"),
            ("301368", "301368 丰立智能谐波减速器已具备批量交付能力"),
        )
    ]

    result = asyncio.run(
        process_task_result(
            "company_evidence_binding",
            _task(
                StandardTaskKind.THEME_BUSINESS_EVIDENCE,
                {
                    "domains": [
                        {"label": "六维力传感器"},
                        {"label": "谐波减速器"},
                    ],
                    "candidate_scope": "candidate_collection",
                    "query": "有哪些公司正在大力发展",
                },
                symbols=("300007", "301368"),
            ),
            evidence,
            {"model": "test"},
            completion=completion,
        )
    )

    assert result["success"] is True
    assert [item["symbol"] for item in result["items"]] == [
        "300007",
        "301368",
    ]
    assert {item["symbol"] for item in result["resource_outputs"]["security_collection"]} == {"300007", "301368"}
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
        "evidence": [
            {
                "source_id": "s1",
                "domain": "减速器",
            }
        ],
        "confidence": 0.96,
    }

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            schema = kwargs["tools"][0]["function"]["parameters"]
            assert schema == (result_processors_module.CompanyThemeAnalysisCandidate.model_json_schema())
            return _response_without_payload()
        assert "tools" not in kwargs
        repair_request = json.loads(kwargs["messages"][1]["content"])
        assert repair_request["targeted_repair"]["invalid_payload"] is None
        assert repair_request["targeted_repair"]["issues"][0]["pointer"] == "/"
        return _response_content(json.dumps(valid_payload, ensure_ascii=False))

    evidence = [
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": "301368"},
            "result": {
                "success": True,
                "evidence_documents": [
                    {
                        "source_id": "s1",
                        "source_type": "company_news",
                        "title": "业务进展",
                        "text": "丰立智能谐波减速器已具备批量交付能力",
                        "source_name": "测试财经",
                    }
                ],
                "source_status": {},
            },
        }
    ]

    result = asyncio.run(
        process_task_result(
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
        )
    )

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
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
        repair_request = json.loads(kwargs["messages"][1]["content"])
        invalid_payload = repair_request["targeted_repair"]["invalid_payload"]
        assert invalid_payload == '{"symbol":"301368","reason":"未结束'
        assert "tools" not in kwargs
        return _response_content(json.dumps(valid_payload, ensure_ascii=False))

    evidence = [
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": "301368"},
            "result": {
                "success": True,
                "evidence_documents": [
                    {
                        "source_id": "s1",
                        "source_type": "company_news",
                        "text": "丰立智能主营精密传动部件。",
                        "source_name": "测试财经",
                    }
                ],
                "source_status": {},
            },
        }
    ]

    result = asyncio.run(
        process_task_result(
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
        )
    )

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
            "evidence": [
                {
                    "source_id": "unknown" if len(calls) == 1 else "s1",
                    "domain": "减速器",
                }
            ],
            "confidence": 0.96,
        }
        if len(calls) == 2:
            repair_request = json.loads(kwargs["messages"][1]["content"])
            issue_paths = {issue["pointer"] for issue in repair_request["targeted_repair"]["issues"]}
            assert "/evidence/0/source_id" in issue_paths
            assert "/evidence" in issue_paths
        return _response("submit_company_theme_analysis", payload)

    evidence = [
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": "301368"},
            "result": {
                "success": True,
                "evidence_documents": [
                    {
                        "source_id": "s1",
                        "source_type": "company_news",
                        "text": source_quote,
                        "source_name": "测试财经",
                    }
                ],
                "source_status": {},
            },
        }
    ]

    result = asyncio.run(
        process_task_result(
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
        )
    )

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
        return _response(
            "submit_company_theme_analysis",
            {
                "symbol": symbol,
                "company_name": symbol,
                "verdict": "pass" if passed else "fail",
                "theme_fit": "exact" if passed else "adjacent",
                "development_level": "layout" if passed else "order",
                "matched_domains": [domain] if passed else [],
                "reason": ("正在推进人形机器人减速器产品" if passed else "证据只涉及其他应用场景"),
                "evidence": [
                    {
                        "source_id": "s1",
                        "domain": domain,
                    }
                ],
                "confidence": 0.96,
            },
        )

    evidence = [
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": "688728"},
            "result": {
                "success": True,
                "evidence_documents": [
                    {
                        "source_id": "s1",
                        "text": "688728 格科微图像传感器产品获得手机品牌客户订单",
                        "source_type": "company_news",
                        "source_name": "测试财经",
                    }
                ],
                "source_status": {},
            },
        },
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": "301368"},
            "result": {
                "success": True,
                "evidence_documents": [
                    {
                        "source_id": "s1",
                        "text": "301368 丰立智能正推进人形机器人谐波减速器产品开发",
                        "source_type": "company_news",
                        "source_name": "测试财经",
                    }
                ],
                "source_status": {},
            },
        },
    ]
    filler_symbols = tuple(f"{100000 + index:06d}" for index in range(486))
    evidence.extend(
        {
            "tool": "get_company_theme_evidence",
            "arguments": {"symbol": symbol},
            "result": {
                "success": True,
                "evidence_documents": [],
                "source_status": {},
            },
        }
        for symbol in filler_symbols
    )
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
    result = asyncio.run(
        process_task_result(
            "company_evidence_binding",
            task,
            evidence,
            {"model": "test"},
            completion=completion,
        )
    )

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
