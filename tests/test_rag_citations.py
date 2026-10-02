from __future__ import annotations

import pytest

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


def test_exact_pdf_quote_across_pages_cites_each_matching_page_hit() -> None:
    quote = "2025年度营业收入为13.8亿元，同比增长12.4%；归母净利润为1.62亿元。"
    evidence = [{
        "evidence_id": "ev_search_action",
        "action_id": "call-pdf-search",
        "tool_name": "search_knowledge_base",
        "success": True,
        "has_data": True,
        "evidence_eligible": True,
        "effect": "read",
        "entities": {"query": "年度经营数据"},
        "data_time_applicable": False,
        "result": {
            "success": True,
            "results": [
                {
                    "evidence_id": "ev_kb_page_7",
                    "page_start": 7,
                    "page_end": 7,
                    "url": "/documents/report/content#page=7",
                    "snippet": quote,
                },
                {
                    "evidence_id": "ev_kb_page_19",
                    "page_start": 19,
                    "page_end": 19,
                    "url": "/documents/report/content#page=19",
                    "snippet": quote,
                },
            ],
        },
    }]
    blocks = [{
        "section": "经营数据",
        "content": f"根据报告第7页和第19页，{quote}",
        "source_ids": [1],
        "evidence_ids": ["ev_kb_page_19"],
    }]

    repaired, remapped_count = repair_pdf_citations_from_exact_text(blocks, evidence)

    assert remapped_count == 1
    assert repaired[0]["evidence_ids"] == ["ev_kb_page_7", "ev_kb_page_19"]
    assert "source_ids" not in repaired[0]
    assert validate_pdf_page_references(repaired, evidence) == []


def test_page_scoped_financial_values_bind_each_explicitly_referenced_page() -> None:
    evidence = [{
        "evidence_id": "ev_search_action",
        "action_id": "call-pdf-search",
        "tool_name": "search_knowledge_base",
        "success": True,
        "has_data": True,
        "evidence_eligible": True,
        "effect": "read",
        "result": {
            "success": True,
            "results": [
                {
                    "evidence_id": "ev_kb_page_7",
                    "page_start": 7,
                    "page_end": 7,
                    "url": "/documents/report/content#page=7",
                    "snippet": "营业收入 2,073,339,945.39 元，同比 -6.17%。",
                },
                {
                    "evidence_id": "ev_kb_page_19",
                    "page_start": 19,
                    "page_end": 19,
                    "url": "/documents/report/content#page=19",
                    "snippet": "主要财务数据同比变动情况：营业收入 2,073,339,945.39 元，同比 -6.17%。",
                },
            ],
        },
    }]
    blocks = [{
        "section": "营业收入",
        "content": (
            "第19页“主要财务数据同比变动情况”表列示营业收入本报告期 "
            "2,073,339,945.39 元、同比 -6.17%，与第7页数值一致。"
        ),
        "source_ids": [1],
        "evidence_ids": ["ev_kb_page_19"],
    }]

    repaired, remapped_count = repair_pdf_citations_from_exact_text(blocks, evidence)

    assert remapped_count == 1
    assert repaired[0]["evidence_ids"] == ["ev_kb_page_7", "ev_kb_page_19"]
    assert "source_ids" not in repaired[0]
    assert validate_pdf_page_references(repaired, evidence) == []


def test_page_scoped_numeric_rebinding_requires_exact_values_and_label() -> None:
    evidence = [{
        "evidence_id": "ev_search_action",
        "tool_name": "search_knowledge_base",
        "success": True,
        "result": {
            "results": [{
                "evidence_id": "ev_kb_page_7",
                "page_start": 7,
                "page_end": 7,
                "url": "/documents/report/content#page=7",
                "snippet": "营业收入 2,073,339,945.39 元，同比 -6.17%。",
            }],
        },
    }]
    blocks = [{
        "section": "净利润",
        "content": "第7页归母净利润为 411,334,427.09 元，同比增长 2.93%。",
        "evidence_ids": [],
    }]

    repaired, remapped_count = repair_pdf_citations_from_exact_text(blocks, evidence)

    assert remapped_count == 0
    assert repaired[0]["evidence_ids"] == []


@pytest.mark.parametrize("page_text", ["第10、19页", "第 10，19 页", "第10及19页", "第10-11、19页"])
def test_parallel_page_references_cannot_borrow_an_uncited_page(page_text: str) -> None:
    evidence = [{
        "evidence_id": "ev_kb_page_19",
        "tool_name": "search_knowledge_base",
        "result": {"page_start": 19, "page_end": 19},
    }]
    issues = validate_pdf_page_references(
        [{"content": f"业务与毛利率见报告{page_text}。", "evidence_ids": ["ev_kb_page_19"]}],
        evidence,
    )
    assert len(issues) == 1
    assert issues[0]["missing_pages"] == ([10, 11] if "10-11" in page_text else [10])


def test_citation_repair_preserves_independent_sources_in_a_combined_risk_block() -> None:
    passages = {
        24: "公司风电类产品占主营业务收入比例较高，风电行业政策对风电市场规模和电价具有引导和调控作用。",
        26: "2024年度由于风电行业内卷，公司风电类产品价格下降，毛利率出现下滑。",
        158: "截至2026年6月30日，浮动利率变动50个基点，净利润变动4,576,671.16元。",
    }
    evidence = [{
        "evidence_id": "ev_search_action", "tool_name": "search_knowledge_base", "success": True,
        "result": {"results": [
            {"evidence_id": f"ev_kb_page_{page}", "page_start": page, "page_end": page,
             "url": f"/documents/report/content#page={page}", "snippet": text}
            for page, text in passages.items()
        ]},
    }]
    blocks = [{
        "kind": "risk", "section": "风险提示", "content": " ".join(passages.values()),
        "source_ids": [1, 2, 3],
        "evidence_ids": [f"ev_kb_page_{page}" for page in passages],
    }]
    repaired, count = repair_pdf_citations_from_exact_text(blocks, evidence)
    assert repaired == blocks
    assert count == 0


def test_matching_citation_does_not_discard_its_stable_source_slot() -> None:
    text = "营业收入2,073,339,945.39元，同比下降6.17%，详见报告第7页。"
    evidence = [{
        "evidence_id": "ev_search_action", "tool_name": "search_knowledge_base", "success": True,
        "result": {"results": [{
            "evidence_id": "ev_kb_page_7", "page_start": 7, "page_end": 7,
            "url": "/documents/report/content#page=7", "snippet": text,
        }]},
    }]
    blocks = [{"content": text, "source_ids": [1], "evidence_ids": ["ev_kb_page_7"]}]
    repaired, count = repair_pdf_citations_from_exact_text(blocks, evidence)
    assert repaired == blocks
    assert count == 0
