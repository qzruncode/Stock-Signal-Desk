"""Focused checks for the generic final-answer claim/evidence ledger."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from src.agent.langgraph_runtime.answer_contract import (
    StructuredAgentAnswer, evidence_source_catalog, resolve_answer_sources,
)
from src.agent.langgraph_runtime.state import merge_records
from src.agent.langgraph_runtime.claim_evidence import build_claim_evidence_ledger
from src.agent.langgraph_runtime.evidence_identity import canonicalize_evidence_markers
from src.agent.langgraph_runtime.claim_evidence import build_structured_claim_evidence_ledger
from src.agent.claim_validation import claim_checks_pass


def test_numbered_sources_remain_stable_across_replay_append_and_ineligible_results() -> None:
    records = [
        _evidence(id="ev_robot"),
        _evidence(id="ev_failed", evidence_id="ev_failed", success=False),
        _evidence(id="ev_write", evidence_id="ev_write", effect="side_effect"),
        _evidence(id="ev_other", evidence_id="ev_other"),
    ]
    catalog = evidence_source_catalog(records)
    assert [(item["source_id"], item["evidence_id"]) for item in catalog] == [(1, "ev_robot"), (4, "ev_other")]
    replay = merge_records(records, [records[0], _evidence(id="ev_new", evidence_id="ev_new")])
    assert evidence_source_catalog(replay)[:2] == catalog
    assert evidence_source_catalog(replay)[-1]["source_id"] == 5
    answer = resolve_answer_sources({"blocks": [{
        "kind": "fact", "content": "来源提供了观察。", "source_ids": [1, 4, 2, 3, 99],
    }]}, records)
    assert answer["blocks"][0]["evidence_ids"] == ["ev_robot", "ev_other", "source:2", "source:3", "source:99"]
    ledger = build_structured_claim_evidence_ledger(answer["blocks"], records, [_tool_result()])
    assert not claim_checks_pass(ledger["claims"][0])
    assert set(ledger["unresolved_evidence_ids"]) == {"source:2", "source:3", "source:99"}


def test_pdf_source_slots_and_claims_resolve_to_one_retrieved_chunk() -> None:
    page_14 = "/api/v1/knowledge-bases/documents/doc-1/content#page=14"
    page_85 = "/api/v1/knowledge-bases/documents/doc-1/content#page=85"
    evidence = [{
        "evidence_id": "ev_search_action",
        "action_id": "call-pdf-search",
        "tool_name": "search_knowledge_base",
        "success": True,
        "has_data": True,
        "evidence_eligible": True,
        "entities": {"query": "Level 0 Agent"},
        "data_time_applicable": False,
        "source_refs": [page_14, page_85],
        "result": {
            "success": True,
            "results": [
                {
                    "citation_id": "kb_level_zero",
                    "page_start": 14,
                    "page_end": 14,
                    "filename": "Agentic_Design_Patterns_Complete.pdf",
                    "snippet": "Level 0 operates without tools, memory, or environment interaction.",
                    "url": page_14,
                },
                {
                    "citation_id": "kb_unrelated",
                    "page_start": 85,
                    "page_end": 85,
                    "filename": "Agentic_Design_Patterns_Complete.pdf",
                    "snippet": "Unrelated code example.",
                    "url": page_85,
                },
            ],
        },
    }]

    catalog = evidence_source_catalog(evidence)
    assert [(item["source_id"], item["evidence_id"]) for item in catalog] == [
        (1, "ev_kb_level_zero"),
        (2, "ev_kb_unrelated"),
    ]
    assert catalog[0]["source_refs"] == [page_14]
    assert catalog[0]["title"].endswith("第 14 页")
    assert catalog[0]["excerpt"].startswith("Level 0 operates")

    normalized, unresolved = canonicalize_evidence_markers(
        "聚合检索记录不应成为结论引用。【证据 ev_search_action】",
        evidence,
    )
    assert normalized == "聚合检索记录不应成为结论引用。"
    assert unresolved == ["ev_search_action"]
    normalized, unresolved = canonicalize_evidence_markers(
        "Level 0 原文。【证据 ev_kb_level_zero】",
        evidence,
    )
    assert normalized == "Level 0 原文。【证据 ev_kb_level_zero】"
    assert unresolved == []

    answer = resolve_answer_sources({"blocks": [{
        "kind": "fact",
        "content": "原文片段已核验。",
        "source_ids": [1],
    }]}, evidence)
    assert answer["blocks"][0]["evidence_ids"] == ["ev_kb_level_zero"]
    ledger = build_structured_claim_evidence_ledger(
        answer["blocks"], evidence, [{
            "action_id": "call-pdf-search",
            "tool_name": "search_knowledge_base",
            "success": True,
        }],
    )
    assert claim_checks_pass(ledger["claims"][0])
    assert ledger["claims"][0]["evidence"] == [{
        "evidence_id": "ev_kb_level_zero",
        "tool_name": "search_knowledge_base",
        "data_time": None,
        "source_refs": [page_14],
    }]


@pytest.mark.parametrize("source_id", ["ev_typo", "1", 0, -1, 1.5, True])
def test_native_source_schema_rejects_hashes_and_invalid_source_numbers(source_id) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(StructuredAgentAnswer).validate_python({"blocks": [{
            "content": "来源提供了观察。", "source_ids": [source_id],
        }]})


def test_old_canonical_answer_can_still_be_read_without_remapping() -> None:
    answer = {"blocks": [{"content": "已核实。", "evidence_ids": ["ev_robot"]}]}
    assert resolve_answer_sources(answer, [_evidence()]) == answer


def test_structured_validation_reports_invalid_reference_on_its_block_only() -> None:
    ledger = build_structured_claim_evidence_ledger(
        [
            {"kind": "risk", "content": "产业链仍存在不确定性。", "evidence_ids": ["ev_robot", "ev_missing"]},
            {"kind": "disclaimer", "content": "截至2026-09-04，本分析仅供研究参考，不构成投资建议。", "evidence_ids": []},
            {"kind": "context", "content": "本次研究对象为300850，截至今日。", "evidence_ids": []},
        ], [_evidence()], [_tool_result()],
    )
    risk, disclaimer, context = ledger["claims"]
    assert not claim_checks_pass(risk)
    assert risk["checks"]["reference_integrity"] is False
    assert risk["issues"] == ledger["issues"]
    assert claim_checks_pass(disclaimer)
    assert claim_checks_pass(context)
    assert disclaimer["issues"] == context["issues"] == []


def test_cited_disclaimer_still_checks_time_and_legacy_claim_checks_include_invalid_ids() -> None:
    ledger = build_structured_claim_evidence_ledger(
        [{"kind": "disclaimer", "content": "截至2027-01-01，仅供参考。", "evidence_ids": ["ev_robot"]}],
        [_evidence()], [_tool_result()],
    )
    assert not claim_checks_pass(ledger["claims"][0])
    assert ledger["issues"]
    assert not claim_checks_pass({
        "checks": {"source": True, "time": True},
        "evidence_ids": ["ev_robot"], "unresolved_evidence_ids": ["ev_missing"],
    })


def _evidence(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "evidence_id": "ev_robot",
        "action_id": "call-robot",
        "tool_name": "search_web_source",
        "success": True,
        "entities": {"query": "人形机器人产业链", "source_id": "public-web"},
        "data_time": "2026-08-08",
        "data_time_note": "来源页面发布日期 2026-08-08",
        "freshness_unknown": False,
        "is_stale": False,
        "source_refs": ["https://example.com/robotics"],
        "result": {"headline": "人形机器人产业链进展"},
    }
    record.update(overrides)
    return record


def _tool_result(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "action_id": "call-robot",
        "tool_name": "search_web_source",
        "success": True,
    }
    record.update(overrides)
    return record


def test_claim_ledger_persists_fact_mapping_and_all_generic_checks() -> None:
    ledger = build_claim_evidence_ledger(
        "截至2026-08-08，人形机器人产业链有新的公开进展。【证据 ev_robot】",
        [_evidence()],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["fact_claim_count"] == 1
    claim = ledger["claims"][0]
    assert claim["evidence_ids"] == ["ev_robot"]
    assert claim["time_references"] == ["2026-08-08"]
    assert claim["checks"] == {
        "reference_integrity": True,
        "tool_success": True,
        "source": True,
        "entity_scope": True,
        "time": True,
    }


def test_claim_ledger_resolves_a_unique_long_evidence_prefix() -> None:
    canonical_id = "ev_abcdefghi1234567890"
    ledger = build_claim_evidence_ledger(
        "人形机器人产业链有新的公开进展。【证据 ev_abcdefghi】",
        [_evidence(evidence_id=canonical_id)],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["cited_evidence_ids"] == [canonical_id]
    assert ledger["claims"][0]["evidence_ids"] == [canonical_id]


def test_claim_ledger_keeps_an_ambiguous_prefix_unresolved() -> None:
    ledger = build_claim_evidence_ledger(
        "资料显示产业链有新的公开进展。【证据 ev_abcdefgh】",
        [
            _evidence(evidence_id="ev_abcdefgh11111111"),
            _evidence(evidence_id="ev_abcdefgh22222222", action_id="call-other"),
        ],
        [_tool_result(), _tool_result(action_id="call-other")],
    )

    assert any("不存在或失败的 evidence_id: ev_abcdefgh" in issue for issue in ledger["issues"])
    assert ledger["claims"][0]["evidence_ids"] == []
    assert ledger["claims"][0]["unresolved_evidence_ids"] == ["ev_abcdefgh"]
    assert ledger["unresolved_evidence_ids"] == ["ev_abcdefgh"]


def test_canonicalize_evidence_markers_removes_an_invalid_marker() -> None:
    normalized, unresolved = canonicalize_evidence_markers(
        "结论【证据 ev_估值历史】【证据 ev_abcdefghi】",
        [{"evidence_id": "ev_abcdefghi1234567890"}],
    )

    assert normalized == "结论【证据 ev_abcdefghi1234567890】"
    assert unresolved == ["ev_估值历史"]


def test_claim_ledger_rejects_missing_real_source_and_failed_tool_record() -> None:
    ledger = build_claim_evidence_ledger(
        "人形机器人产业链已进入新阶段。【证据 ev_robot】",
        [_evidence(source_refs=["tool:search_web_source"])],
        [_tool_result(success=False)],
    )

    assert any("缺少真实来源" in issue for issue in ledger["issues"])
    assert any("未关联到成功工具结果" in issue for issue in ledger["issues"])
    assert ledger["claims"][0]["checks"]["source"] is False
    assert ledger["claims"][0]["checks"]["tool_success"] is False


def test_claim_ledger_rejects_unmatched_identifier_and_time() -> None:
    ledger = build_claim_evidence_ledger(
        "截至2026-08-09，代码 000001 已发布新进展。【证据 ev_robot】",
        [_evidence()],
        [_tool_result()],
    )

    assert any("显式标识" in issue and "000001" in issue for issue in ledger["issues"])
    assert any("时间无法" in issue and "2026-08-09" in issue for issue in ledger["issues"])
    claim = ledger["claims"][0]
    assert claim["checks"]["entity_scope"] is False
    assert claim["checks"]["time"] is False


def test_claim_ledger_resolves_prior_year_from_the_same_report_table_hit() -> None:
    evidence = _evidence(
        tool_name="search_knowledge_base",
        data_time=None,
        data_time_applicable=False,
        freshness_unknown=True,
        is_stale=False,
        result={
            "filename": "新强联_2026年半年度报告.pdf",
            "snippet": "| 本报告期 | 上年同期 | 本报告期比上年同期增减 |",
        },
    )

    ledger = build_claim_evidence_ledger(
        "2025年半年度为上年同期。【证据 ev_robot】",
        [evidence],
        [_tool_result(tool_name="search_knowledge_base")],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["checks"]["time"] is True


@pytest.mark.parametrize(
    ("filename", "claim_year"),
    [
        ("新强联_2026年半年度报告.pdf", "2024"),
        ("新强联_半年度报告.pdf", "2025"),
    ],
)
def test_claim_ledger_does_not_guess_a_prior_year_without_matching_report_metadata(
    filename: str,
    claim_year: str,
) -> None:
    evidence = _evidence(
        tool_name="search_knowledge_base",
        data_time=None,
        data_time_applicable=False,
        freshness_unknown=True,
        is_stale=False,
        result={
            "filename": filename,
            "snippet": "| 本报告期 | 上年同期 | 本报告期比上年同期增减 |",
        },
    )

    ledger = build_claim_evidence_ledger(
        f"{claim_year}年半年度为上年同期。【证据 ev_robot】",
        [evidence],
        [_tool_result(tool_name="search_knowledge_base")],
    )

    assert any("时间无法" in issue and claim_year in issue for issue in ledger["issues"])
    assert ledger["claims"][0]["checks"]["time"] is False


def test_claim_ledger_does_not_extract_decimal_prefixes_as_entity_identifiers() -> None:
    ledger = build_claim_evidence_ledger(
        "ROE-15.17%，EPS-1.14元，其他指标ABC-12.34【证据 ev_robot】",
        [_evidence(result={"ROE": -15.17, "EPS": -1.14, "ABC": -12.34})],
        [_tool_result()],
    )
    assert ledger["issues"] == []

    unmatched = build_claim_evidence_ledger(
        "代码 ABC-15 和 600438 的新公告【证据 ev_robot】",
        [_evidence()],
        [_tool_result()],
    )
    assert any("ABC-15" in issue and "600438" in issue for issue in unmatched["issues"])


def test_claim_ledger_requires_source_data_time_for_latest_answer_scope() -> None:
    ledger = build_claim_evidence_ledger(
        "基于最新公开资料，人形机器人产业链出现新进展。【证据 ev_robot】",
        [_evidence(data_time=None, freshness_unknown=True, is_stale=None)],
        [_tool_result()],
    )

    assert any("当前/最新时间口径" in issue for issue in ledger["issues"])


def test_claim_ledger_does_not_treat_current_event_awareness_as_a_freshness_claim() -> None:
    source_text = (
        "The trade-off is a complete lack of current-event awareness."
    )
    evidence = _evidence(
        tool_name="search_knowledge_base",
        data_time=None,
        freshness_unknown=True,
        is_stale=False,
        result={"text": source_text},
    )
    tool_result = _tool_result(tool_name="search_knowledge_base")

    ledger = build_structured_claim_evidence_ledger(
        [{
            "kind": "fact",
            "content": "The trade-off is a complete lack of current-event awareness. 【证据 ev_robot】",
            "evidence_ids": ["ev_robot"],
        }],
        [evidence],
        [tool_result],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["checks"]["time"] is True


def test_claim_ledger_uses_document_source_time_applicability_for_capability_text() -> None:
    source_text = "The agent can search for current information and synthesize the results."
    evidence = _evidence(
        tool_name="search_knowledge_base",
        data_time=None,
        data_time_applicable=False,
        freshness_unknown=True,
        is_stale=False,
        result={"text": source_text},
    )

    ledger = build_structured_claim_evidence_ledger(
        [{
            "kind": "fact",
            "content": "The book describes Level 1 as able to find current information through tools. 【证据 ev_robot】",
            "evidence_ids": ["ev_robot"],
        }],
        [evidence],
        [_tool_result(tool_name="search_knowledge_base")],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["checks"]["time"] is True


def test_claim_ledger_does_not_let_an_uncited_latest_intro_borrow_later_evidence() -> None:
    ledger = build_claim_evidence_ledger(
        "基于最新公开资料，以下为概括。\n\n"
        "主来源已返回可用材料【证据 ev_robot】",
        [_evidence()],
        [_tool_result()],
    )

    assert any("紧邻的 evidence_id" in issue for issue in ledger["issues"])
    assert ledger["claims"][0]["uses_relative_time"] is True
    assert ledger["claims"][0]["evidence_ids"] == []


def test_claim_ledger_does_not_treat_ordinary_quoted_prose_as_an_identifier() -> None:
    ledger = build_claim_evidence_ledger(
        '来源将该环节概括为“卖铲人”【证据 ev_robot】',
        [_evidence()],
        [_tool_result()],
    )

    assert not any("显式标识" in issue for issue in ledger["issues"])


def test_claim_ledger_binds_a_source_note_to_the_preceding_markdown_table() -> None:
    ledger = build_claim_evidence_ledger(
        "| 日期 | 事项 |\n"
        "|---|---|\n"
        "| 2026-07-13 | 股份质押 |\n"
        "| 2026-07-03 | 2025 年度再融资文件 |\n\n"
        "> 来源：公司公告【证据 ev_robot】",
        [
            _evidence(
                result={
                    "items": [
                        {"date": "2026-07-13", "title": "股份质押"},
                        {"date": "2026-07-03", "title": "2025 年度再融资文件"},
                    ]
                }
            )
        ],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["evidence_ids"] == ["ev_robot"]
    assert ledger["claims"][0]["time_references"] == ["2026-07-13", "2026-07-03", "2025"]


def test_claim_ledger_requires_local_evidence_for_table_and_material_sections() -> None:
    ledger = build_claim_evidence_ledger(
        "### 财务数据\n"
        "| 报告期 | 营收同比 |\n"
        "|---|---|\n"
        "| 2026H1 | -6.2% |\n\n"
        "**核心观察**：营收增长放缓。【证据 ev_robot】\n\n"
        "### 综合判断\n\n"
        "**结论：当前估值处于低位，但需注意风险。**\n\n"
        "**操作建议：**\n"
        "- 分批关注后续数据，不一次性重仓",
        [_evidence(result={"summary": "财务数据与估值观察"})],
        [_tool_result()],
    )

    assert len(ledger["claims"]) == 4
    assert ledger["claims"][0]["evidence_ids"] == []
    assert ledger["claims"][1]["evidence_ids"] == ["ev_robot"]
    assert ledger["claims"][2]["evidence_ids"] == []
    assert ledger["claims"][3]["evidence_ids"] == []
    missing = [
        issue
        for issue in ledger["issues"]
        if issue.startswith("回答片段没有关联有效 evidence_id:")
    ]
    assert len(missing) == 3


def test_claim_ledger_does_not_scope_a_following_list_from_cited_prose() -> None:
    ledger = build_claim_evidence_ledger(
        "已核对来源中的主体数据【证据 ev_robot】\n\n"
        "- 2026H1 的营收同比为 -6.2%",
        [_evidence(result={"summary": "主体数据"})],
        [_tool_result()],
    )

    assert len(ledger["claims"]) == 2
    assert ledger["claims"][0]["evidence_ids"] == ["ev_robot"]
    assert ledger["claims"][1]["evidence_ids"] == []


def test_claim_ledger_binds_a_bold_citation_only_line_to_the_preceding_table() -> None:
    ledger = build_claim_evidence_ledger(
        "| 年度 | 收入 |\n"
        "|---|---|\n"
        "| 2025 | 100 |\n\n"
        "**【ev_robot】**",
        [_evidence(result={"items": [{"year": "2025", "revenue": 100}]})],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["evidence_ids"] == ["ev_robot"]


def test_claim_ledger_binds_a_cited_section_intro_to_the_following_table() -> None:
    ledger = build_claim_evidence_ledger(
        "### 产品维度（2025 年全年）【证据 ev_robot】\n\n"
        "| 产品 | 收入占比 |\n"
        "|---|---|\n"
        "| 核心业务 | 54.3% |",
        [_evidence(result={"items": [{"period": "2025 年全年", "share": "54.3%"}]})],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["evidence_ids"] == ["ev_robot"]


def test_claim_ledger_does_not_treat_common_period_or_indicator_labels_as_entities() -> None:
    ledger = build_claim_evidence_ledger(
        "2025Q4 的 MA20 与 RSI14 均已列入说明【证据 ev_robot】",
        [_evidence(result={"summary": "周期与指标说明"})],
        [_tool_result()],
    )

    assert not any("显式标识" in issue for issue in ledger["issues"])


def test_claim_ledger_ignores_a_heading_only_current_label() -> None:
    ledger = build_claim_evidence_ledger(
        "## 当前市场主线\n\n后续内容待补充。",
        [_evidence()],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["claims"] == []


def test_claim_ledger_accepts_a_cited_freshness_disclaimer_without_source_time() -> None:
    ledger = build_claim_evidence_ledger(
        "### 当前估值快照\n\n"
        "报价为 12.3 元；该来源未返回交易时间戳，时效性无法确认。"
        "【证据 ev_robot】",
        [_evidence(data_time=None, freshness_unknown=True, is_stale=None)],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["uses_relative_time"] is False


def test_claim_ledger_does_not_require_freshness_for_a_negative_latest_warning() -> None:
    ledger = build_claim_evidence_ledger(
        '该来源未返回交易时间，不宜直接表述为“最新价”。【证据 ev_robot】',
        [_evidence(data_time=None, freshness_unknown=True, is_stale=None)],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][0]["uses_relative_time"] is False


def test_claim_ledger_reuses_an_earlier_visible_citation_for_a_repeated_date() -> None:
    ledger = build_claim_evidence_ledger(
        "截至2026-08-08，主来源已返回可用材料【证据 ev_robot】\n\n"
        "补充说明：这组材料对应 2026-08-08。",
        [_evidence()],
        [_tool_result()],
    )

    assert ledger["issues"] == []
    assert ledger["claims"][1]["citation_mode"] == "inherited"
    assert ledger["claims"][1]["evidence_ids"] == ["ev_robot"]


def test_claim_ledger_does_not_use_transport_fetch_time_as_source_date() -> None:
    ledger = build_claim_evidence_ledger(
        "机构共识认为2026Q1业绩会改善。",
        [
            _evidence(
                data_time=None,
                freshness_unknown=True,
                is_stale=None,
                result={
                    "profile": "公司基础档案",
                    "_fetched_at": "2026-08-15T20:22:29+08:00",
                    "source_refs": ["https://example.test/report/2026-08-15"],
                },
            )
        ],
        [_tool_result()],
    )

    assert ledger["claims"][0]["evidence_ids"] == []
    assert ledger["claims"][0]["citation_mode"] == "missing"
    assert any("紧邻的 evidence_id" in issue for issue in ledger["issues"])


def test_claim_ledger_does_not_inherit_a_bare_year_or_quarter() -> None:
    ledger = build_claim_evidence_ledger(
        "截至2026-08-08，主来源已返回可用材料【证据 ev_robot】\n\n"
        "机构共识认为2026Q1业绩会改善。",
        [_evidence()],
        [_tool_result()],
    )

    assert ledger["claims"][1]["evidence_ids"] == []
    assert ledger["claims"][1]["citation_mode"] == "missing"
