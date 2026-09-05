"""Unit checks for the native structured final-answer contract."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from src.agent.langgraph_runtime.answer_contract import (
    StructuredAgentAnswer,
    finalize_terminal_answer,
    render_structured_answer,
)
from src.agent.langgraph_runtime.claim_evidence import (
    build_structured_claim_evidence_ledger,
)


def _evidence() -> tuple[dict[str, object], ...]:
    return (
        {
            "evidence_id": "ev_source-1",
            "action_id": "source-1",
            "tool_name": "search_source",
            "success": True,
            "effect": "read",
            "source_refs": ["https://source.example/1"],
            "entities": {"symbol": "600519"},
            "data_time": "2026-08-08",
        },
    )


def test_typed_answer_schema_rejects_extra_fields_and_empty_blocks() -> None:
    adapter = TypeAdapter(StructuredAgentAnswer)

    parsed = adapter.validate_python({"blocks": [{"content": "事实"}]})
    assert parsed["title"] == ""
    assert parsed["blocks"][0]["kind"] == "fact"

    with pytest.raises(ValidationError):
        adapter.validate_python({"blocks": [], "unexpected": "not allowed"})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"blocks": [{"content": "事实", "unexpected": "not allowed"}]}
        )


def test_renderer_uses_only_typed_block_evidence_ids() -> None:
    answer = {
        "title": "结论",
        "blocks": [
            {
                "section": "事实",
                "kind": "fact",
                "content": "来源已返回材料【证据 ev_invented】。",
                "evidence_ids": ["ev_source-1"],
            }
        ],
    }

    rendered = render_structured_answer(answer, _evidence())

    assert "ev_source-1" in rendered
    assert "ev_invented" not in rendered


def test_structured_ledger_keeps_table_or_recommendation_as_one_explicit_claim() -> None:
    blocks = [
        {
            "section": "财务数据",
            "kind": "fact",
            "content": "| 指标 | 结果 |\n|---|---|\n| 营收 | -6.2% |",
            "evidence_ids": ["ev_source-1"],
        },
        {
            "section": "操作建议",
            "kind": "recommendation",
            "content": "继续观察后续数据，不一次性重仓。",
            "evidence_ids": ["ev_source-1"],
        },
    ]

    ledger = build_structured_claim_evidence_ledger(
        blocks,
        _evidence(),
        [
            {
                "action_id": "source-1",
                "tool_name": "search_source",
                "success": True,
            }
        ],
    )

    assert len(ledger["claims"]) == 2
    assert all(all(claim["checks"].values()) for claim in ledger["claims"])
    assert ledger["claims"][0]["evidence_ids"] == ["ev_source-1"]


def test_structured_ledger_requires_evidence_for_material_blocks_even_without_tool_results() -> None:
    ledger = build_structured_claim_evidence_ledger(
        [
            {
                "section": "说明",
                "kind": "context",
                "content": "这是回答范围说明。",
                "evidence_ids": [],
            },
            {
                "section": "结论",
                "kind": "inference",
                "content": "这是需要外部依据的判断。",
                "evidence_ids": [],
            },
        ],
        [],
        [],
    )

    assert ledger["claims"][0]["checks"] == {
        "tool_success": True,
        "source": True,
        "entity_scope": True,
        "time": True,
    }
    assert ledger["claims"][1]["checks"]["tool_success"] is False
    assert any("没有关联有效 evidence_id" in issue for issue in ledger["issues"])


def test_terminal_finalizer_is_idempotent_and_preserves_empty_provider_failure() -> None:
    partial = finalize_terminal_answer(
        "候选结果",
        status="partial",
        error_code="evidence_link_incomplete",
    )

    assert finalize_terminal_answer(
        partial,
        status="partial",
        error_code="evidence_link_incomplete",
    ) == partial
    assert finalize_terminal_answer(
        "",
        status="failed",
        error_code="model_provider_timeout",
    ) == ""
