"""Focused checks for the generic final-answer claim/evidence ledger."""

from __future__ import annotations

from typing import Any

from src.agent.langgraph_runtime.claim_evidence import build_claim_evidence_ledger


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
