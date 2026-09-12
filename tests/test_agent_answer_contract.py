"""Unit checks for the native structured final-answer contract."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from src.agent.langgraph_runtime.answer_contract import (
    StructuredAgentAnswer,
    finalize_terminal_answer,
    output_reference_catalog_for_model,
    project_structured_answer,
    render_structured_answer,
    resolve_structured_answer_references,
    structured_answer_contract_issues,
    structured_answer_profile,
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
    assert parsed["profile"] == "research"
    assert parsed["title"] == ""
    assert parsed["blocks"][0]["kind"] == "fact"
    assert parsed["blocks"][0]["presentation_type"] == "markdown"
    assert parsed["blocks"][0]["language"] == ""
    assert parsed["blocks"][0]["chart_series_keys"] == []
    assert parsed["blocks"][0]["chart_title"] == ""
    assert parsed["blocks"][0]["source_ids"] == []

    with pytest.raises(ValidationError):
        adapter.validate_python({"blocks": [], "unexpected": "not allowed"})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"blocks": [{"content": "事实", "unexpected": "not allowed"}]}
        )


def test_general_profile_allows_an_explanation_without_external_evidence() -> None:
    answer = TypeAdapter(StructuredAgentAnswer).validate_python(
        {
            "profile": "general",
            "blocks": [{"kind": "answer", "content": "这是一个不需要外部取证的解释。"}],
        }
    )

    ledger = build_structured_claim_evidence_ledger(
        answer["blocks"], [], [], profile=structured_answer_profile(answer)
    )

    assert structured_answer_contract_issues(answer) == []
    assert ledger["issues"] == []
    assert ledger["claims"][0]["requires_evidence"] is False


def test_general_answer_still_requires_sources_after_external_evidence_is_read() -> None:
    answer = TypeAdapter(StructuredAgentAnswer).validate_python(
        {
            "profile": "general",
            "blocks": [{"kind": "answer", "content": "这是基于外部资料的解释。"}],
        }
    )

    ledger = build_structured_claim_evidence_ledger(
        answer["blocks"],
        list(_evidence()),
        [{"action_id": "source-1", "success": True}],
        profile=structured_answer_profile(answer),
    )

    assert ledger["claims"][0]["requires_evidence"] is True
    assert any("没有关联有效 evidence_id" in issue for issue in ledger["issues"])


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


def test_renderer_keeps_semantics_separate_from_code_json_and_quote_presentation() -> None:
    answer = {
        "profile": "general",
        "blocks": [
            {
                "kind": "answer",
                "presentation_type": "code",
                "language": "python",
                "content": "print('hello')",
            },
            {
                "kind": "fact",
                "presentation_type": "json",
                "content": '{"name":"demo","items":[1,2]}',
            },
            {
                "kind": "context",
                "presentation_type": "quote",
                "content": "原始说明\n第二行",
            },
        ],
    }

    rendered = render_structured_answer(answer)

    assert "```python\nprint('hello')\n```" in rendered
    assert '```json\n{\n  "name": "demo",\n  "items": [\n    1,\n    2\n  ]\n}\n```' in rendered
    assert "> 原始说明\n> 第二行" in rendered


def test_invalid_json_presentation_is_a_repairable_contract_issue() -> None:
    answer = {
        "profile": "general",
        "blocks": [
            {
                "kind": "answer",
                "presentation_type": "json",
                "content": "{not-json}",
            }
        ],
    }

    assert structured_answer_contract_issues(answer) == [
        "第 1 个 json 区块的 content 必须是有效 JSON"
    ]


def test_output_references_are_resolved_from_trusted_tool_results() -> None:
    tool_results = [
        {
            "action_id": "screen-1",
            "tool_name": "screen_atr_volatility_stocks",
            "effect": "read",
            "success": True,
            "result": {
                "file_id": "stock-screen-financial-20260912-120000-abcdef12.csv",
                "filename": "/private/exports/筛选结果.csv",
                "items": [
                    {"date": "2026-09-11", "close": 10.2, "volume": 100},
                    {"date": "2026-09-12", "close": 10.6, "volume": 130},
                ],
            },
        },
        {
            "action_id": "failed-1",
            "tool_name": "read_web_source",
            "effect": "read",
            "success": False,
            "result": {
                "file_id": "stock-screen-20260912-120000-deadbeef.csv",
            },
        },
    ]
    answer = {
        "profile": "research",
        "blocks": [
            {
                "kind": "fact",
                "content": "筛选结果",
                "artifact_source_ids": [1],
                "chart_source_ids": [1],
                "action_source_ids": [1, 2],
            }
        ],
    }

    model_catalog = output_reference_catalog_for_model(tool_results)
    assert model_catalog["artifacts"] == [
        {
            "source_id": 1,
            "artifact_type": "file",
            "title": "筛选结果.csv",
            "mime_type": "text/csv",
            "action_id": "screen-1",
        }
    ]
    assert "stock-screen-financial-20260912-120000-abcdef12.csv" not in str(model_catalog)
    assert "/private/exports" not in str(model_catalog)
    assert model_catalog["actions"][1]["status"] == "failed"

    resolved = resolve_structured_answer_references(
        answer,
        tool_results=tool_results,
    )
    block = resolved["blocks"][0]
    assert block["artifact_refs"][0]["download_url"] == (
        "/api/v1/agent/exports/stock-screen-financial-20260912-120000-abcdef12.csv"
    )
    assert block["chart_refs"][0]["series"] == [
        {"key": "close", "label": "close"},
        {"key": "volume", "label": "volume"},
    ]
    assert len(block["chart_refs"][0]["data"]) == 2
    assert [item["action_id"] for item in block["action_refs"]] == ["screen-1", "failed-1"]

    projection = project_structured_answer(answer, tool_results=tool_results)
    assert projection["blocks"][0]["artifact_refs"][0]["artifact_type"] == "file"
    assert projection["blocks"][0]["chart_refs"][0]["chart_id"] == "chart-screen-1-1"
    rendered = render_structured_answer(answer, tool_results=tool_results)
    assert "下载文件：筛选结果.csv" in rendered
    assert "仅展示，不会再次执行" in rendered


def test_output_reference_resolution_rejects_model_authored_paths_and_urls() -> None:
    answer = {
        "profile": "general",
        "blocks": [
            {
                "kind": "answer",
                "content": "说明",
                "artifact_source_ids": [1],
                "artifact_refs": [
                    {
                        "artifact_id": "../../secrets.txt",
                        "title": "秘密",
                        "download_url": "https://evil.example/download",
                    }
                ],
            }
        ],
    }

    projection = project_structured_answer(answer)
    assert "artifact_refs" not in projection["blocks"][0]


def test_chart_reference_resolution_keeps_model_selected_series_only() -> None:
    tool_results = [
        {
            "action_id": "capital-flow-1",
            "tool_name": "read_stock_capital_flow_history_eastmoney",
            "effect": "read",
            "success": True,
            "result": {
                "items": [
                    {
                        "date": "2026-09-10",
                        "close": 1400.0,
                        "main_net_inflow": 320000000,
                        "small_net_inflow": -80000000,
                    },
                    {
                        "date": "2026-09-11",
                        "close": 1410.0,
                        "main_net_inflow": 280000000,
                        "small_net_inflow": -60000000,
                    },
                ],
            },
        }
    ]
    answer = {
        "profile": "general",
        "blocks": [
            {
                "kind": "answer",
                "content": "资金流趋势",
                "chart_source_ids": [1],
                "chart_series_keys": ["main_net_inflow", "not_in_catalog"],
                "chart_title": "主力净流入",
            }
        ],
    }

    resolved = resolve_structured_answer_references(answer, tool_results=tool_results)
    chart = resolved["blocks"][0]["chart_refs"][0]
    assert chart["title"] == "主力净流入"
    assert chart["series"] == [{"key": "main_net_inflow", "label": "main_net_inflow"}]
    assert chart["data"] == [
        {"x": "2026-09-10", "main_net_inflow": 320000000},
        {"x": "2026-09-11", "main_net_inflow": 280000000},
    ]


def test_chart_reference_resolution_supports_symbol_rankings_for_bar_charts() -> None:
    tool_results = [
        {
            "action_id": "screen-1",
            "tool_name": "screen_atr_volatility_stocks",
            "effect": "read",
            "success": True,
            "result": {
                "items": [
                    {"code": "000636", "name": "风华高科", "current_atr_pct": 6.02},
                    {"code": "000338", "name": "潍柴动力", "current_atr_pct": 5.02},
                ],
            },
        }
    ]

    resolved = resolve_structured_answer_references(
        {
            "profile": "research",
            "blocks": [
                {
                    "kind": "fact",
                    "content": "排名",
                    "chart_source_ids": [1],
                    "chart_type": "bar",
                    "chart_series_keys": ["current_atr_pct"],
                    "chart_title": "ATR相对波动率排名",
                }
            ],
        },
        tool_results=tool_results,
    )

    chart = resolved["blocks"][0]["chart_refs"][0]
    assert chart["chart_type"] == "bar"
    assert chart["title"] == "ATR相对波动率排名"
    assert chart["series"] == [{"key": "current_atr_pct", "label": "current_atr_pct"}]
    assert chart["data"] == [
        {"x": "000636", "current_atr_pct": 6.02},
        {"x": "000338", "current_atr_pct": 5.02},
    ]


def test_structured_answer_projection_exposes_only_typed_client_fields() -> None:
    projection = project_structured_answer(
        {
            "profile": "research",
            "title": "研究结果",
            "blocks": [
                {
                    "section": "判断",
                    "kind": "inference",
                    "presentation_type": "table",
                    "language": "ignored",
                    "content": "| 指标 | 结果 |",
                    "source_ids": [1],
                    "unexpected": "discarded",
                }
            ],
        },
        _evidence(),
    )

    assert projection == {
        "profile": "research",
        "title": "研究结果",
        "blocks": [
            {
                "section": "判断",
                "kind": "inference",
                "presentation_type": "table",
                "language": "",
                "content": "| 指标 | 结果 |",
                "evidence_ids": ["ev_source-1"],
            }
        ],
    }


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
        "reference_integrity": True,
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
