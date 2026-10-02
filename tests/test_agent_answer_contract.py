"""Unit checks for the native structured final-answer contract."""

from __future__ import annotations

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import TypeAdapter, ValidationError

from src.agent.langgraph_runtime.answer_contract import (
    StructuredAgentAnswer,
    finalize_terminal_answer,
    output_reference_catalog_for_model,
    output_reference_catalogs,
    project_structured_answer,
    render_structured_answer,
    resolve_structured_answer_references,
    structured_answer_display_parts,
    structured_answer_contract_issues,
    structured_answer_blocks,
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


def _validated_answer(value: dict[str, object]) -> dict[str, object]:
    return TypeAdapter(StructuredAgentAnswer).validate_python(value).model_dump(mode="python")


def test_typed_answer_schema_rejects_extra_fields_and_empty_blocks() -> None:
    adapter = TypeAdapter(StructuredAgentAnswer)

    parsed = _validated_answer({"blocks": [{"kind": "fact", "content": "事实"}]})
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


def test_progress_text_has_no_artificial_character_limit() -> None:
    progress = "进" * 1_801
    answer = _validated_answer(
        {
            "progress_text": progress,
            "blocks": [{"kind": "answer", "content": "已完成。"}],
        }
    )

    assert answer["progress_text"] == progress


def test_general_profile_allows_an_explanation_without_external_evidence() -> None:
    answer = _validated_answer(
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


def test_cross_expert_synthesis_accepts_all_relevant_sources_without_truncation() -> None:
    adapter = TypeAdapter(StructuredAgentAnswer)
    answer = _validated_answer({"blocks": [{
        "kind": "inference", "content": "跨专家综合判断", "source_ids": list(range(1, 32)),
    }]})
    evidence = [
        {"evidence_id": f"ev-{index}", "success": True, "effect": "read"}
        for index in range(1, 32)
    ]
    resolved = resolve_structured_answer_references(answer, evidence=evidence, tool_results=[])
    assert resolved["blocks"][0]["evidence_ids"] == [f"ev-{index}" for index in range(1, 32)]
    for invalid_sources in ([0], ["1"], list(range(1, 82))):
        with pytest.raises(ValidationError):
            adapter.validate_python({"blocks": [{"content": "无效引用", "source_ids": invalid_sources}]})


def test_general_answer_still_requires_sources_after_external_evidence_is_read() -> None:
    answer = _validated_answer(
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


def test_sourced_research_table_rejects_a_header_without_data_rows() -> None:
    empty_table = {
        "profile": "research",
        "blocks": [{
            "kind": "fact",
            "presentation_type": "table",
            "content": "| 指标 | 数值 | 页码 |\n|---|---|---|",
            "source_ids": [1],
        }],
    }
    populated_table = {
        "profile": "research",
        "blocks": [{
            "kind": "fact",
            "presentation_type": "table",
            "content": "| 指标 | 数值 | 页码 |\n|---|---|---|\n| 营业收入 | 100 元 | 第 7 页 |",
            "source_ids": [1],
        }],
    }
    intentional_general_template = {
        "profile": "general",
        "blocks": [{
            "kind": "answer",
            "presentation_type": "table",
            "content": "| 指标 | 数值 |\n|---|---|",
        }],
    }

    issues = structured_answer_contract_issues(empty_table)
    assert any("不完整的 Markdown 表格" in issue for issue in issues)
    # Keep shape repair inside LangChain's native ToolStrategy validation loop;
    # the post-parse contract remains the publication safety net.
    with pytest.raises(ValidationError, match="不完整的 Markdown 表格"):
        TypeAdapter(StructuredAgentAnswer).validate_python(empty_table)
    TypeAdapter(StructuredAgentAnswer).validate_python(populated_table)
    assert structured_answer_contract_issues(populated_table) == []
    assert structured_answer_contract_issues(intentional_general_template) == []


def test_provider_output_schema_allows_server_rendered_typed_tables() -> None:
    schema = convert_to_openai_tool(StructuredAgentAnswer)["function"]["parameters"]
    block_schema = schema["properties"]["blocks"]["items"]

    assert "content" not in block_schema.get("required", [])
    assert block_schema["properties"]["table_columns"]["type"] == "array"
    assert block_schema["properties"]["table_rows"]["type"] == "array"
    assert "maxLength" not in block_schema["properties"]["content"]
    assert "kind" in block_schema.get("required", [])
    assert "maxItems" not in schema["properties"]["blocks"]
    assert "server constructs the complete Markdown table" in block_schema["properties"]["content"]["description"]
    with pytest.raises(ValidationError):
        TypeAdapter(StructuredAgentAnswer).validate_python(
            {"blocks": [{"kind": "fact", "presentation_type": "table"}]}
        )


def test_native_answer_requires_explicit_semantics_for_a_scope_note() -> None:
    note = "基于2026年半年度报告检索命中整理，未检索到的章节未纳入分析。"
    with pytest.raises(ValidationError):
        TypeAdapter(StructuredAgentAnswer).validate_python({"blocks": [{"content": note}]})
    answer = _validated_answer({"blocks": [{"kind": "disclaimer", "content": note}]})
    assert build_structured_claim_evidence_ledger(answer["blocks"], [], [], profile="research")["issues"] == []


def test_native_answer_rejects_identical_repeated_blocks_without_a_length_cap() -> None:
    block = {"kind": "disclaimer", "content": "本轮未检索到相关章节，不构成投资建议。"}
    with pytest.raises(ValidationError, match="重复"):
        TypeAdapter(StructuredAgentAnswer).validate_python({"blocks": [block, dict(block)]})
    answer = _validated_answer({"blocks": [
        {"kind": "answer", "content": f"独立内容 {index}"} for index in range(81)
    ]})
    assert len(answer["blocks"]) == 81


def test_typed_table_is_rendered_by_server_and_keeps_resolved_evidence() -> None:
    answer = _validated_answer(
        {
            "profile": "research",
            "blocks": [{
                "kind": "fact",
                "presentation_type": "table",
                "table_columns": ["指标", "本报告期（元）", "同比增减", "PDF页码"],
                "table_rows": [
                    ["营业收入", "2,073,339,945.39", "-6.17%", "第7页"],
                    ["归母净利润", "411,334,427.09", "2.93%", "第7页"],
                    ["经营活动现金流净额", "363,763,010.34", "275.77%", "第7页"],
                ],
                "source_ids": [1],
            }],
        }
    )

    expected_table = (
        "| 指标 | 本报告期（元） | 同比增减 | PDF页码 |\n"
        "| --- | --- | --- | --- |\n"
        "| 营业收入 | 2,073,339,945.39 | -6.17% | 第7页 |\n"
        "| 归母净利润 | 411,334,427.09 | 2.93% | 第7页 |\n"
        "| 经营活动现金流净额 | 363,763,010.34 | 275.77% | 第7页 |"
    )
    assert answer["blocks"][0]["content"] == expected_table
    assert structured_answer_contract_issues(
        answer,
        user_text="用一张 Markdown 表格回答",
    ) == []

    rendered = render_structured_answer(answer, _evidence())
    assert expected_table in rendered
    assert "【证据 ev_source-1】" in rendered
    assert f"{expected_table}\n\n【证据 ev_source-1】" in rendered

    display_text = "\n".join(
        str(part.get("text") or "")
        for part in structured_answer_display_parts(answer, _evidence())
        if part.get("type") == "text"
    )
    assert f"{expected_table}\n\n【证据 ev_source-1】" in display_text

    projection = project_structured_answer(answer, _evidence())
    assert projection["blocks"][0]["content"] == expected_table
    assert "table_rows" not in projection["blocks"][0]


@pytest.mark.parametrize(
    "table_rows",
    [[], [["单元格数不匹配"]]],
)
def test_typed_table_rejects_missing_or_mismatched_rows(table_rows) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(StructuredAgentAnswer).validate_python(
            {
                "blocks": [{
                    "kind": "fact",
                    "presentation_type": "table",
                    "table_columns": ["指标", "数值"],
                    "table_rows": table_rows,
                }],
            }
        )


def test_markdown_table_renders_and_keeps_evidence() -> None:
    table = (
        "| 类别 | 项目 | 本期金额 | 同比 | PDF页码 | 代码 | 上市市场 | 所属行业 |\n"
        "|---|---|---:|---:|---|---|---|---|\n"
        "| 财务 | 营业收入 | 2,073,339,945.39 元 | -6.17% | 第7页 | — | — | — |\n"
        "| 财务 | 归母净利润 | 411,334,427.09 元 | 2.93% | 第7页 | — | — | — |\n"
        "| 财务 | 经营活动现金流净额 | 363,763,010.34 元 | 275.77% | 第7页 | — | — | — |\n"
        "| 证券 | 新强联 | — | — | — | 300850 | 创业板 | C 制造业 |"
    )
    answer = _validated_answer(
        {
            "profile": "research",
            "blocks": [
                {
                    "kind": "fact",
                    "presentation_type": "table",
                    "content": table,
                    "source_ids": [1],
                },
                {
                    "kind": "context",
                    "content": "注：以上财务数据为半年报累计合并口径。",
                },
            ],
        }
    )

    blocks = structured_answer_blocks(answer)
    table_content = blocks[0]["content"]
    assert table_content.count("\n") == 5
    assert "| 财务 | 营业收入 | 2,073,339,945.39 元 | -6.17% | 第7页 |" in table_content
    assert "经营活动现金流净额" in table_content
    assert "300850" in table_content
    assert structured_answer_contract_issues(
        answer,
        user_text="最终只输出一个完整 Markdown 表格。",
    ) == []

    rendered = render_structured_answer(answer, _evidence())
    assert "| 类别 | 项目 | 本期金额 | 同比 | PDF页码 | 代码 | 上市市场 | 所属行业 |" in rendered
    assert "【证据 ev_source-1】" in rendered
    assert "以上财务数据为半年报累计合并口径" in rendered


def test_table_contract_rejects_untyped_header_rows_and_honors_explicit_table_request() -> None:
    malformed_markdown = {
        "profile": "research",
        "blocks": [{
            "kind": "fact",
            "presentation_type": "markdown",
            "content": "| 指标 | 数值 | 页码 |",
            "source_ids": [1],
        }],
    }
    prose_only = {
        "profile": "general",
        "blocks": [{"kind": "answer", "content": "已完成检索。"}],
    }

    malformed_issues = structured_answer_contract_issues(malformed_markdown)
    assert any("不完整的 Markdown 表格" in issue for issue in malformed_issues)
    requested_table_issues = structured_answer_contract_issues(
        prose_only,
        user_text="最终只输出一个完整 Markdown 表格，表后附一句说明。",
    )
    assert any("用户明确要求 Markdown 表格" in issue for issue in requested_table_issues)


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


def test_renderer_does_not_repeat_title_echoed_by_first_block() -> None:
    answer = {
        "title": "事件驱动架构的解耦机制",
        "blocks": [{
            "content": "事件驱动架构的解耦机制",
        }],
    }

    rendered = render_structured_answer(answer)
    display_text = "\n".join(
        str(part.get("text") or "")
        for part in structured_answer_display_parts(answer)
        if part.get("type") == "text"
    )

    assert rendered == "# 事件驱动架构的解耦机制"
    assert display_text == "# 事件驱动架构的解耦机制"


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
    assert model_catalog["actions"] == []

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


def test_model_output_catalog_exposes_only_side_effect_actions() -> None:
    catalog = output_reference_catalog_for_model(
        [
            {
                "action_id": "read-1",
                "tool_name": "search_knowledge_base",
                "effect": "read",
                "success": True,
                "result": {"results": []},
            },
            {
                "action_id": "write-1",
                "tool_name": "add_watchlist_items",
                "effect": "side_effect",
                "success": True,
                "result": {"message": "已加入自选"},
            },
        ]
    )

    assert catalog["actions"] == [
        {
            "source_id": 2,
            "action_id": "write-1",
            "tool_name": "add_watchlist_items",
            "effect": "side_effect",
            "status": "completed",
            "success": True,
            "reused": False,
        }
    ]


def test_answer_content_is_not_truncated_by_application_character_caps() -> None:
    content = "长回答内容" * 5_000
    answer = _validated_answer(
        {
            "profile": "general",
            "blocks": [{"kind": "answer", "content": content}],
        }
    )

    projected = project_structured_answer(answer)
    rendered = render_structured_answer(answer)

    assert projected["blocks"][0]["content"] == content
    assert rendered == content


def test_structured_answer_display_parts_keep_chart_after_owning_block() -> None:
    answer = {
        "profile": "research",
        "title": "行情与结论",
        "blocks": [
            {
                "section": "行情",
                "kind": "fact",
                "content": "行情已经核验。",
                "chart_refs": [
                    {
                        "chart_id": "chart-1",
                        "chart_type": "line",
                        "title": "行情走势",
                        "series": [{"key": "close", "label": "收盘价"}],
                        "data": [{"x": "2026-09-16", "close": 12.3}],
                    }
                ],
            },
            {
                "section": "结论",
                "kind": "answer",
                "content": "结论仍需结合风险判断。",
            },
        ],
    }

    parts = structured_answer_display_parts(answer)

    assert [part["type"] for part in parts] == ["text", "data", "text"]
    assert parts[0]["text"].endswith("行情已经核验。")
    assert parts[1]["name"] == "stock-chart"
    assert parts[1]["data"]["chart_id"] == "chart-1"
    assert parts[2]["text"].endswith("结论仍需结合风险判断。")


def test_output_reference_catalog_dedupes_identical_retry_charts() -> None:
    tool_results = [
        {
            "action_id": "team:market:attempt-1:call-1",
            "tool_name": "read_recent_kline",
            "success": True,
            "result": {
                "chart_title": "近 60 日走势",
                "data": [
                    {"date": "2026-09-16", "close": 12.3},
                    {"date": "2026-09-17", "close": 12.1},
                ],
            },
        },
        {
            "action_id": "team:market:attempt-2:call-2",
            "tool_name": "read_recent_kline",
            "success": True,
            "result": {
                "chart_title": "近 60 日走势",
                "data": [
                    {"date": "2026-09-16", "close": 12.3},
                    {"date": "2026-09-17", "close": 12.1},
                ],
            },
        },
    ]

    catalogs = output_reference_catalogs(tool_results)
    model_catalog = output_reference_catalog_for_model(tool_results)

    assert len(catalogs["actions"]) == 2
    assert len(catalogs["charts"]) == 1
    assert catalogs["charts"][0]["source_id"] == 1
    assert len(model_catalog["charts"]) == 1

    answer = {
        "profile": "research",
        "blocks": [{
            "section": "行情",
            "content": "行情已经核验。",
            "chart_source_ids": [1, 2],
        }],
    }
    rendered = render_structured_answer(answer, tool_results=tool_results)
    assert rendered.count("图表：近 60 日走势") == 1


def test_structured_answer_display_parts_do_not_duplicate_action_fallbacks() -> None:
    answer = {
        "profile": "research",
        "title": "行情与结论",
        "blocks": [{
            "section": "行情",
            "kind": "fact",
            "content": "行情已经核验。",
            "action_refs": [{
                "action_id": "quote-1",
                "tool_name": "read_realtime_quote",
                "status": "completed",
                "success": True,
            }],
        }],
    }

    parts = structured_answer_display_parts(answer)

    assert all("动作记录" not in str(part.get("text") or "") for part in parts)


def test_renderer_merges_repeated_section_number_without_duplicate_heading() -> None:
    answer = {
        "profile": "general",
        "title": "研究报告",
        "blocks": [
            {
                "section": "三、新闻与研报动态",
                "kind": "answer",
                "content": "研报内容",
            },
            {
                "section": "三、新闻与公告动态",
                "kind": "answer",
                "content": "公告内容",
            },
            {
                "section": "四、主要风险",
                "kind": "risk",
                "content": "风险内容",
            },
        ],
    }

    rendered = render_structured_answer(answer)
    display_text = "\n".join(
        str(part.get("text") or "")
        for part in structured_answer_display_parts(answer)
        if part.get("type") == "text"
    )

    for output in (rendered, display_text):
        assert "## 三、新闻与研报动态" in output
        assert "## 三、新闻与公告动态" not in output
        assert "公告内容" in output
        assert "## 四、主要风险" in output


def test_server_one_point_line_chart_is_not_replayed_as_a_chart() -> None:
    answer = {
        "profile": "research",
        "blocks": [{
            "section": "行情",
            "content": "行情已经核验。",
            "chart_refs": [{
                "chart_id": "quote-chart",
                "chart_type": "line",
                "title": "最新报价",
                "action_id": "quote-action",
                "series": [{"key": "price", "label": "price"}],
                "data": [{"x": "600519", "price": 1257.05}],
            }, {
                "chart_id": "kline-chart",
                "chart_type": "line",
                "title": "近60日走势",
                "action_id": "kline-action",
                "series": [{"key": "close", "label": "close"}],
                "data": [
                    {"x": "2026-09-15", "close": 1272.75},
                    {"x": "2026-09-16", "close": 1257.05},
                ],
            }],
        }],
    }

    parts = structured_answer_display_parts(answer)

    assert [part["type"] for part in parts] == ["text", "data"]
    assert parts[1]["data"]["chart_id"] == "kline-chart"


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


def test_terminal_finalizer_hides_internal_planning_diagnostics_from_chat_answer() -> None:
    answer = finalize_terminal_answer(
        "已保留部分结果",
        status="partial",
        error_code="planning_incomplete",
        detail="PlanningStepReport failed after 2 attempts (planning_contract_validation_failed)",
    )

    assert "PlanningStepReport" not in answer
    assert "planning_contract_validation_failed" not in answer
    assert "计划尚未完整结束" in answer
