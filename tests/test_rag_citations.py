from __future__ import annotations

from src.rag.citations import (
    has_current_knowledge_base_search,
    repair_pdf_citations_from_exact_text,
    validate_pdf_page_references,
)


def test_eager_pre_search_does_not_satisfy_current_turn_search_gate() -> None:
    assert not has_current_knowledge_base_search(
        [
            {
                "tool_name": "search_knowledge_base",
                "action_id": "run:knowledge-base:pre-search",
                "success": True,
            }
        ]
    )
    assert has_current_knowledge_base_search(
        [
            {
                "tool_name": "search_knowledge_base",
                "model_tool_call_id": "call-1",
                "success": False,
            }
        ]
    )


def test_pdf_page_references_must_exist_in_the_cited_search_result() -> None:
    evidence = [
        {
            "evidence_id": "ev-wrong-page",
            "tool_name": "search_knowledge_base",
            "result": {"results": [{"page_start": 419, "page_end": 419}]},
        },
        {
            "evidence_id": "ev-right-pages",
            "tool_name": "search_knowledge_base",
            "result": {
                "results": [
                    {"page_start": 14, "page_end": 14},
                    {"page_start": 15, "page_end": 15},
                ]
            },
        },
    ]

    issues = validate_pdf_page_references(
        [
            {"content": "Level 0 定义见第 14 页。", "evidence_ids": ["ev-wrong-page"]},
            {"content": "章节内容见第 14–15 页。", "evidence_ids": ["ev-right-pages"]},
            {"content": "参考第 419 页。", "evidence_ids": ["ev-right-pages"]},
        ],
        evidence,
    )

    assert issues == [
        {
            "block_index": 0,
            "referenced_pages": [14],
            "available_pages": [419],
            "missing_pages": [14],
            "evidence_ids": ["ev-wrong-page"],
        },
        {
            "block_index": 2,
            "referenced_pages": [419],
            "available_pages": [14, 15],
            "missing_pages": [419],
            "evidence_ids": ["ev-right-pages"],
        },
    ]


def test_pdf_chunk_citation_cannot_borrow_pages_from_other_search_hits() -> None:
    evidence = [{
        "evidence_id": "ev-search-action",
        "tool_name": "search_knowledge_base",
        "action_id": "call-search",
        "success": True,
        "result": {
            "success": True,
            "results": [
                {
                    "evidence_id": "ev_kb_page_14",
                    "page_start": 14,
                    "page_end": 14,
                    "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=14",
                    "snippet": "Level 0 definition.",
                },
                {
                    "evidence_id": "ev_kb_page_85",
                    "page_start": 85,
                    "page_end": 85,
                    "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=85",
                    "snippet": "Unrelated code example.",
                },
            ],
        },
    }]

    issues = validate_pdf_page_references(
        [
            {"content": "Level 0 定义见第 14 页。", "evidence_ids": ["ev_kb_page_14"]},
            {"content": "代码示例见第 14 页。", "evidence_ids": ["ev_kb_page_85"]},
        ],
        evidence,
    )

    assert issues == [{
        "block_index": 1,
        "referenced_pages": [14],
        "available_pages": [85],
        "missing_pages": [14],
        "evidence_ids": ["ev_kb_page_85"],
    }]


def test_exact_pdf_quote_rebinds_wrong_hit_and_page_only_block_to_matching_page() -> None:
    quote = (
        "In a 'Level 0' configuration, the LLM operates without tools, memory, or "
        "environment interaction, responding solely based on its pretrained knowledge. "
        "The trade-off for this powerful internal reasoning is a complete lack of "
        "current-event awareness."
    )
    evidence = [{
        "evidence_id": "ev_search_action",
        "action_id": "call-pdf-search",
        "tool_name": "search_knowledge_base",
        "success": True,
        "has_data": True,
        "evidence_eligible": True,
        "effect": "read",
        "entities": {"query": "Level 0 limitation"},
        "data_time_applicable": False,
        "source_refs": [
            "/api/v1/knowledge-bases/documents/doc-1/content#page=340",
            "/api/v1/knowledge-bases/documents/doc-1/content#page=14",
        ],
        "result": {
            "success": True,
            "results": [
                {
                    "evidence_id": "ev_kb_wrong_page",
                    "page_start": 340,
                    "page_end": 340,
                    "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=340",
                    "snippet": "Weaknesses, Originality, Quality, Clarity, and Significance.",
                },
                {
                    "evidence_id": "ev_kb_level_zero",
                    "page_start": 14,
                    "page_end": 14,
                    "url": "/api/v1/knowledge-bases/documents/doc-1/content#page=14",
                    "snippet": quote,
                },
            ],
        },
    }]
    blocks = [
        {
            "section": "Level 0 的主要局限",
            "content": "第 14 页说明，Level 0 的 trade-off 是 a complete lack of current-event awareness。",
            "source_ids": [1],
            "evidence_ids": ["ev_kb_wrong_page"],
        },
        {
            "section": "最能支持结论的原文",
            "content": f"“{quote}”",
            "source_ids": [1],
            "evidence_ids": ["ev_kb_wrong_page"],
        },
        {
            "section": "页码",
            "content": "上述原文位于第 14 页。",
            "source_ids": [1],
            "evidence_ids": ["ev_kb_wrong_page"],
        },
    ]

    repaired, remapped_count = repair_pdf_citations_from_exact_text(blocks, evidence)

    assert remapped_count == 3
    assert all(block["evidence_ids"] == ["ev_kb_level_zero"] for block in repaired)
    assert all("source_ids" not in block for block in repaired)
    assert validate_pdf_page_references(repaired, evidence) == []
