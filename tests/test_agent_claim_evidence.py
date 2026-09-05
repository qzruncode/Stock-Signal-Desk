"""Focused checks for the generic final-answer claim/evidence ledger."""

from __future__ import annotations

from typing import Any

from src.agent.langgraph_runtime.claim_evidence import build_claim_evidence_ledger
from src.agent.langgraph_runtime.evidence_identity import canonicalize_evidence_markers


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


def test_claim_ledger_requires_source_data_time_for_latest_answer_scope() -> None:
    ledger = build_claim_evidence_ledger(
        "基于最新公开资料，人形机器人产业链出现新进展。【证据 ev_robot】",
        [_evidence(data_time=None, freshness_unknown=True, is_stale=None)],
        [_tool_result()],
    )

    assert any("当前/最新时间口径" in issue for issue in ledger["issues"])


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
