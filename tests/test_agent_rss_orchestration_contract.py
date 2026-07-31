from __future__ import annotations

import pytest

from src.agent.rss_renderers import build_rss_resource_answer
from src.agent.orchestrator_v2.contracts import (
    Capability,
    InputReferenceV2,
    IntentOutlineV2,
    RendererMode,
    ResourceType,
)
from src.agent.orchestrator_v2.intents import FinancialArticleReadIntent
from src.agent.orchestrator_v2.planner import (
    ExactContractValidationError,
    _bind_outline_resources,
    _validate_outline_capability_contracts,
)
from src.agent.orchestrator_v2.registry import capability_for, normalize_capability_intent
from src.agent.task_workflows import (
    EntityScope,
    ResolvedTask,
    StandardTask,
    StandardTaskKind,
    compile_task,
)


ROUTE = "/szse/disclosure/listed/notice/:query?"


def _rss_outline() -> IntentOutlineV2:
    return IntentOutlineV2.model_validate(
        {
            "goal": {
                "objective": "读取指定深交所 RSSHub 路由及最新公告原文件",
                "question_type": "factual",
                "uncertainty_mode": "exact",
                "deliverables": ["最近五条公告", "最新公告全文和 PDF 原文件"],
                "claims": [
                    {
                        "claim_id": "rss_material",
                        "question": "指定路由的最新公告及原文件是什么",
                        "required_dimensions": ["feed_content"],
                    }
                ],
                "source_requirements": [
                    {
                        "kind": "rsshub",
                        "mode": "exclusive",
                        "route_path": ROUTE,
                    }
                ],
                "required_output_resources": [
                    "rss_source_collection",
                    "rss_item_collection",
                    "text_document_collection",
                    "evidence_collection",
                ],
            },
            "nodes": [
                {
                    "node_id": "inspect_source",
                    "capability": "financial_source_discovery",
                    "objective": "检查用户指定的 RSSHub 路由",
                },
                {
                    "node_id": "read_feed",
                    "capability": "financial_feed_read",
                    "objective": "读取指定路由最近五条",
                },
                {
                    "node_id": "read_latest_item",
                    "capability": "financial_article_read",
                    "objective": "读取最新一条全文和 PDF 原文件",
                },
            ],
        }
    )


def test_explicit_rss_route_cannot_be_substituted_by_announcement_analysis() -> None:
    invalid = IntentOutlineV2.model_validate(
        {
            "goal": {
                "objective": "读取指定 RSSHub 路由",
                "question_type": "factual",
                "uncertainty_mode": "exact",
                "deliverables": ["公告"],
                "claims": [
                    {
                        "claim_id": "announcement",
                        "question": "公告是什么",
                        "required_dimensions": ["announcements"],
                    }
                ],
                "source_requirements": [
                    {
                        "kind": "rsshub",
                        "mode": "exclusive",
                        "route_path": ROUTE,
                    }
                ],
                "required_output_resources": [
                    "rss_item_collection",
                    "text_document_collection",
                ],
            },
            "nodes": [
                {
                    "node_id": "announcement",
                    "capability": "announcement_analysis",
                    "objective": "查询公告",
                }
            ],
        }
    )

    with pytest.raises(ExactContractValidationError) as exc:
        _validate_outline_capability_contracts(invalid)

    issue_codes = {item.code for item in exc.value.issues}
    assert "explicit_rss_route_substituted" in issue_codes
    assert "exclusive_rss_scope_violated" in issue_codes
    assert "goal_output_resource_coverage_incomplete" in issue_codes


def test_explicit_rss_article_graph_requires_source_and_feed_stages() -> None:
    invalid = _rss_outline().model_copy(
        update={
            "nodes": tuple(
                node
                for node in _rss_outline().nodes
                if node.capability != Capability.FINANCIAL_SOURCE_DISCOVERY
            )
        }
    )

    with pytest.raises(ExactContractValidationError) as exc:
        _validate_outline_capability_contracts(invalid)

    issue_codes = {item.code for item in exc.value.issues}
    assert "rss_source_resolution_missing" in issue_codes


def test_rss_article_resource_dependency_accepts_feed_item_without_document() -> None:
    bound = _bind_outline_resources(
        _rss_outline(),
        available_artifacts={},
    )

    refs = {
        node.node_id: {
            (ref.node_id, ref.resource_type)
            for ref in node.input_refs
        }
        for node in bound.nodes
    }
    assert refs["read_feed"] == {
        ("inspect_source", ResourceType.RSS_SOURCE_COLLECTION)
    }
    assert refs["read_latest_item"] == {
        ("read_feed", ResourceType.RSS_ITEM_COLLECTION)
    }


def test_latest_article_selection_uses_published_time_and_stable_item_ref() -> None:
    older = {
        "title": "旧公告",
        "published": "2026-07-30T09:00:00+08:00",
        "item_ref": {
            "route_path": ROUTE,
            "item_id": "older",
            "content_hash": "a" * 64,
        },
    }
    latest = {
        "title": "最新公告",
        "published": "2026-07-31T09:00:00+08:00",
        "item_ref": {
            "route_path": ROUTE,
            "item_id": "latest",
            "content_hash": "b" * 64,
        },
    }
    task = StandardTask(
        task_id="read_latest_item",
        kind=StandardTaskKind.FINANCIAL_ARTICLE_READ,
        objective="读取最新一条全文和 PDF 原文件",
        entity_scope=EntityScope.NONE,
        execution_parameters={
            "items": [older, latest],
            "selection": "latest",
            "response_mode": "resource_delivery",
            "include_documents": True,
        },
    )

    calls = compile_task(ResolvedTask(candidate=task))

    assert [call.tool_name for call in calls] == ["read_rss_item"]
    assert calls[0].arguments["item_ref"]["item_id"] == "latest"
    assert calls[0].arguments["item"]["title"] == "最新公告"
    assert calls[0].arguments["include_documents"] is True


def test_bound_document_artifact_is_dereferenced_and_selected_by_mime_type() -> None:
    intent = FinancialArticleReadIntent(
        resource_id="artifact_collection_wrapper",
        document_mime_type="application/pdf",
        response_mode="answer_question",
        query="委托理财额度是多少",
        reading_mode="complete",
    )
    normalized = normalize_capability_intent(
        capability=Capability.FINANCIAL_ARTICLE_READ,
        node_id="read_pdf",
        objective="回答已绑定文件中的问题",
        intent=intent,
        input_refs=(
            InputReferenceV2(
                source="artifact",
                artifact_id="artifact_collection_wrapper",
                resource_type=ResourceType.TEXT_DOCUMENT_COLLECTION,
            ),
        ),
        result_selection=None,
        current_year=2026,
    )
    assert "resource_id" not in normalized.execution_parameters

    task = StandardTask(
        task_id="read_pdf",
        kind=StandardTaskKind.FINANCIAL_ARTICLE_READ,
        objective="回答已绑定文件中的问题",
        entity_scope=EntityScope.NONE,
        execution_parameters={
            **dict(normalized.execution_parameters),
            "resources": [
                {
                    "resource_id": "textdoc_html",
                    "mime_type": "text/html",
                },
                {
                    "resource_id": "textdoc_pdf",
                    "mime_type": "application/pdf",
                },
            ],
        },
    )

    calls = compile_task(ResolvedTask(candidate=task))

    assert calls[0].tool_name == "read_text_document"
    assert calls[0].arguments["resource_id"] == "textdoc_pdf"
    assert "document_mime_type" not in calls[0].arguments


def test_article_question_uses_evidence_synthesis_renderer() -> None:
    assert (
        capability_for(Capability.FINANCIAL_ARTICLE_READ).renderer
        == RendererMode.EVIDENCE_SYNTHESIS
    )


def test_rss_resource_answer_is_deterministic_when_writer_returns_empty() -> None:
    answer = build_rss_resource_answer(
        [
            {
                "tool": "read_rss_feed",
                "result": {
                    "route_path": ROUTE,
                    "items": [{}, {}, {}, {}, {}],
                },
            },
            {
                "tool": "read_rss_item",
                "result": {
                    "item_ref": {"route_path": ROUTE},
                    "title": "惠科股份公告",
                    "content_length": 1200,
                    "content_chunks": [{}, {}, {}],
                    "resources": [
                        {
                            "filename": "惠科股份公告.PDF",
                            "extraction_status": "extracted",
                            "chunk_count": 4,
                        }
                    ],
                    "errors": [],
                },
            },
        ]
    )

    assert answer is not None
    assert "已读取 5 条" in answer
    assert "惠科股份公告.PDF" in answer
    assert "未按标题重新搜索" in answer
