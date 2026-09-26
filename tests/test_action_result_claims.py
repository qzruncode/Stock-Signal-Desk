from __future__ import annotations

from src.agent.claim_validation import claim_checks_pass
from src.agent.langgraph_runtime.answer_contract import resolve_structured_answer_references
from src.agent.langgraph_runtime.claim_evidence import build_structured_claim_evidence_ledger
from src.agent.langgraph_runtime.middleware import _verified_action_result_fallback


def _action(*, success: bool = True, effect: str = "side_effect") -> dict:
    return {
        "id": "action-1",
        "action_id": "action-1",
        "tool_name": "import_company_financial_report",
        "effect": effect,
        "success": success,
        "result": {"success": success, "message": "官方财报 PDF 原件已保存，索引任务已提交。"},
    }


def _answer(action_source_ids: list[int]) -> dict:
    return {
        "profile": "research",
        "blocks": [
            {
                "section": "导入结果",
                "kind": "action_result",
                "content": "官方财报 PDF 原件已保存，索引任务已提交。",
                "action_source_ids": action_source_ids,
            }
        ],
    }


def _ledger(answer: dict, action: dict) -> dict:
    resolved = resolve_structured_answer_references(
        answer,
        evidence=[],
        tool_results=[action],
    )
    return build_structured_claim_evidence_ledger(
        resolved["blocks"],
        evidence=[],
        tool_results=[action],
        profile=resolved["profile"],
    )


def test_action_result_is_supported_by_the_observed_side_effect_not_pdf_evidence() -> None:
    ledger = _ledger(_answer([1]), _action())

    assert ledger["issues"] == []
    assert ledger["claims"][0]["requires_evidence"] is False
    assert ledger["claims"][0]["action_ids"] == ["action-1"]
    assert ledger["claims"][0]["checks"]["action_reference"] is True
    assert claim_checks_pass(ledger["claims"][0])


def test_action_result_cannot_cite_a_read_observation_as_an_executed_action() -> None:
    ledger = _ledger(_answer([1]), _action(effect="read"))

    assert ledger["claims"][0]["checks"]["action_reference"] is False
    assert ledger["issues"]
    assert not claim_checks_pass(ledger["claims"][0])


def test_action_result_requires_a_real_action_reference_even_when_the_action_failed() -> None:
    failed_action = _action(success=False)
    referenced = _ledger(_answer([1]), failed_action)
    unreferenced = _ledger(_answer([]), failed_action)

    assert referenced["issues"] == []
    assert claim_checks_pass(referenced["claims"][0])
    assert unreferenced["claims"][0]["checks"]["action_reference"] is False
    assert not claim_checks_pass(unreferenced["claims"][0])


def test_evidence_failure_fallback_keeps_only_server_verified_action_message() -> None:
    action = _action()
    structured = resolve_structured_answer_references(
        {
            "profile": "research",
            "blocks": [
                {
                    "section": "导入结果",
                    "kind": "action_result",
                    "content": "模型生成的动作描述可能含有其他结论。",
                    "action_source_ids": [1],
                },
                {
                    "section": "财务结论",
                    "kind": "answer",
                    "content": "未受证据支持的报告判断。",
                    "evidence_ids": ["ev-missing"],
                },
            ],
        },
        evidence=[],
        tool_results=[action],
    )

    safe = _verified_action_result_fallback(
        structured["blocks"],
        evidence=[],
        tool_results=[action],
    )

    assert len(safe) == 1
    assert safe[0]["kind"] == "action_result"
    assert safe[0]["content"] == action["result"]["message"]
    assert "模型生成" not in safe[0]["content"]
    assert safe[0]["action_refs"][0]["action_id"] == "action-1"


def test_evidence_failure_fallback_drops_unverified_action_claims() -> None:
    action = _action()
    block = {
        "section": "导入结果",
        "kind": "action_result",
        "content": "报告显示收入大幅增长。",
        "action_refs": [],
    }

    assert _verified_action_result_fallback([block], [], [action]) == []
