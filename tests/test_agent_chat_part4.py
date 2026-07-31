# -*- coding: utf-8 -*-
"""Agent chat endpoint tests — standard-task pipeline and SSE stream.

Covers api.v1.endpoints.agent.chat:
- _get_llm_config: model/api_key/api_base resolution (normal / boundary paths)
- _run_standard_task_pipeline: fixed workflows and policy-validated execution
- _stream_final_answer_without_tools: normal output, LLM failure fallback, empty content
- agent_chat SSE: HTTP-level normal stream and LLM-call failure path

Async tests use asyncio.run (the project does not use pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints.agent import chat as chat_mod
import src.auth as auth



"""Focused test slice 4; shared fixtures remain local to this slice."""

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)

@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
        patch.object(
            chat_mod,
            "execute_tool_isolated",
            side_effect=lambda name, arguments, **_kwargs: chat_mod._registry.execute(name, arguments),
        ),
    ):
        yield
    auth._auth_enabled = None

def _mock_llm_chunk(content=None, tool_calls=None, finish_reason=None, reasoning_content=None):
    """Build a litellm streaming chunk mock."""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
    delta.reasoning_content = reasoning_content
    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=delta, finish_reason=finish_reason)]
    return chunk

class _AsyncChunkStream:
    """Wrap a list of chunks into an async iterator for `async for`."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

def _async_completion(chunks):
    """Return an async fake acompletion that yields the given chunks."""

    async def _fake(**kwargs):
        return _AsyncChunkStream(chunks)

    return _fake

def _mock_tool_call_delta(idx=0, name="", arguments="", tc_id="call_1"):
    tc = MagicMock()
    tc.index = idx
    tc.id = tc_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = arguments
    return tc

class _FakeController:
    """Minimal RunController substitute capturing appended text."""

    def __init__(self):
        self.texts = []
        self.reasoning = []
        self.data = []
        self._stream_tasks = []
        self.tool_calls = []

    def append_text(self, text):
        self.texts.append(text)

    def append_reasoning(self, text):
        self.reasoning.append(text)

    def add_data(self, value):
        self.data.append(value)

    async def add_tool_call(self, name, tool_call_id=None):
        self.tool_calls.append((name, tool_call_id))
        tool = MagicMock()
        tool.append_args_text = MagicMock()
        tool.set_response = MagicMock()
        return tool
def test_generic_final_synthesis_repairs_header_only_table():
    controller = _FakeController()
    calls = {"value": 0}
    evidence = [
        {
            "tool": "get_multi_stock_snapshot",
            "result": {
                "success": True,
                "items": [
                    {
                        "name": "绿的谐波",
                        "symbol": "688017",
                        "quote": {"price": 330.12, "change_pct": -12.81, "pe_dynamic": 463.63, "pb_ratio": 17.14},
                        "financial": {"net_profit": 32634147.52, "debt_ratio_pct": 9.44},
                        "technical": {"is_stale": False},
                    }
                ],
                "data_time": "2026-07-18T13:39:02+08:00",
                "quote_basis": "非交易时段的最近市场快照",
            },
        }
    ]

    async def fake_acompletion(**kwargs):
        calls["value"] += 1
        if calls["value"] == 1:
            return _AsyncChunkStream(
                [
                    _mock_llm_chunk(
                        content="| 公司/代码 | 判断 |\n|---|---|",
                        finish_reason="stop",
                    )
                ]
            )
        return _AsyncChunkStream(
            [
                _mock_llm_chunk(
                    content="| 公司/代码 | 判断 |\n|---|---|\n| 绿的谐波 (688017) | 等业绩兑现 |",
                    finish_reason="stop",
                )
            ]
        )

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "找出这些股票"}],
                cfg,
                evidence=evidence,
            )

    result = asyncio.run(run())
    assert "绿的谐波 (688017)" in result
    assert calls["value"] == 2

def test_industry_playbook_does_not_use_keyword_section_validator():
    controller = _FakeController()
    call_count = {"value": 0}
    requests = []

    async def fake_acompletion(**kwargs):
        call_count["value"] += 1
        requests.append(kwargs)
        if call_count["value"] == 1:
            return _AsyncChunkStream([_mock_llm_chunk(content="机器人行业会受益。", finish_reason="stop")])
        repaired = (
            "优先级与最受益排序如下。上游、中游、下游构成产业链。"
            "受益机制看价值量，兑现指标看订单与产能。反证和风险是不及预期。"
            "后续跟踪量化指标。来源见工具证据，截至2026-07-17，置信度中等，证据缺口明确。"
            "[来源一](https://example.com/a) [来源二](https://example.org/b)"
        )
        return _AsyncChunkStream([_mock_llm_chunk(content=repaired, finish_reason="stop")])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}
    evidence = [{"tool": "search_financial_news", "result": {"success": True, "items": []}}]

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "分析人形机器人产业链"}],
                cfg,
                evidence=evidence,
                playbook=chat_mod.INDUSTRY_CHAIN,
            )

    result = asyncio.run(run())
    assert result == "机器人行业会受益。"
    assert call_count["value"] == 1

def test_industry_contract_does_not_discard_safe_answer_for_link_format_only():
    safe_answer = (
        "优先级和最受益排序。上游、中游、下游构成产业链。"
        "受益机制看价值量，兑现指标看订单和产能。反证与风险是不及预期。"
        "后续持续跟踪量化指标，来源截至当前证据时间，整体置信度中等。"
        "[来源](https://example.com/a)"
    )
    assert (
        chat_mod._playbook_answer_contract_issues(
            chat_mod.INDUSTRY_CHAIN,
            safe_answer,
            [],
        )
        == []
    )

def test_final_synthesis_does_not_install_a_deadline():
    controller = _FakeController()
    complete = (
        "优先级和最受益排序。上游、中游、下游产业链。"
        "受益机制看价值量，兑现指标看订单产能。反证与风险是不及预期。"
        "后续跟踪量化指标，来源截至2026-07-17，置信度中等，证据缺口明确。"
        "[来源一](https://example.com/a) [来源二](https://example.org/b)"
    )

    async def fake_acompletion(**kwargs):
        await asyncio.sleep(0.01)
        return _AsyncChunkStream(
            [
                _mock_llm_chunk(content=complete, finish_reason="stop"),
            ]
        )

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with (
            patch("api.v1.endpoints.agent.chat.litellm") as llm_mod,
            patch(
                "api.v1.endpoints.agent.chat.asyncio.timeout",
                side_effect=AssertionError(
                    "final synthesis must wait for the model"
                ),
            ) as timeout_factory,
        ):
            llm_mod.acompletion = fake_acompletion
            result = await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "分析产业链"}],
                cfg,
                evidence=[],
                playbook=chat_mod.INDUSTRY_CHAIN,
            )
            timeout_factory.assert_not_called()
            return result

    assert asyncio.run(run()) == complete
    assert controller.texts == [complete]

def test_final_synthesis_emits_heartbeat_while_waiting_for_a_delta():
    controller = _FakeController()

    class _DelayedAnswer:
        def __init__(self):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            await asyncio.sleep(0.03)
            return _mock_llm_chunk(content="完成", finish_reason="stop")

    async def fake_acompletion(**kwargs):
        return _DelayedAnswer()

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with (
            patch("api.v1.endpoints.agent.chat.litellm") as llm_mod,
            patch.object(
                chat_mod,
                "MODEL_STREAM_HEARTBEAT_SECONDS",
                0.005,
            ),
        ):
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "解释一下"}],
                cfg,
            )

    assert asyncio.run(run()) == "完成"
    assert "模型仍在处理「最终答案综合」" in "".join(controller.reasoning)

def test_playbook_empty_completion_retries_once_before_failing_closed():
    controller = _FakeController()
    calls = {"value": 0}
    repaired = (
        "优先级和最受益排序。上游、中游、下游产业链。"
        "受益机制看价值量，兑现指标看订单产能。反证与风险是不及预期。"
        "后续跟踪量化指标，来源截至2026-07-17，置信度中等，证据缺口明确。"
        "[来源一](https://example.com/a) [来源二](https://example.org/b)"
    )

    async def fake_acompletion(**kwargs):
        calls["value"] += 1
        if calls["value"] == 1:
            return _AsyncChunkStream([_mock_llm_chunk(content=None, finish_reason="stop")])
        return _AsyncChunkStream([_mock_llm_chunk(content=repaired, finish_reason="stop")])

    cfg = {"model": "gpt-4o", "api_key": None, "api_base": None, "extra_headers": None}

    async def run():
        with patch("api.v1.endpoints.agent.chat.litellm") as llm_mod:
            llm_mod.acompletion = fake_acompletion
            return await chat_mod._stream_final_answer_without_tools(
                controller,
                [{"role": "user", "content": "分析产业链"}],
                cfg,
                evidence=[],
                playbook=chat_mod.INDUSTRY_CHAIN,
            )

    assert asyncio.run(run()) == repaired
    assert calls["value"] == 2

def test_deep_research_answer_is_not_validated_by_keyword_axes():
    evidence = [
        {
            "tool": "get_multi_stock_decision_evidence",
            "result": {
                "success": True,
                "resolved_entities": [
                    {"name": "兆威机电", "symbol": "003021"},
                    {"name": "绿的谐波", "symbol": "688017"},
                ],
            },
        }
    ]
    incomplete = "003021：主营业务不错，财务和估值需要看。"
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.STOCK_DEEP_RESEARCH,
        incomplete,
        evidence,
    )
    assert issues == []

    complete = (
        "003021、688017：主营业务兑现；财务营收、净利和现金流；"
        "估值PE与一致预期；交易趋势与资金；公告催化和风险；"
        "结论等待验证，并给出成立条件与失效条件。"
    )
    assert (
        chat_mod._playbook_answer_contract_issues(
            chat_mod.STOCK_DEEP_RESEARCH,
            complete,
            evidence,
        )
        == []
    )

def test_deep_research_contract_does_not_scan_buy_prose():
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.STOCK_DEEP_RESEARCH,
        "我认为可以买入。",
        [{"tool": "get_multi_stock_decision_evidence", "result": {"success": False}}],
    )
    assert issues == []
    assert (
        chat_mod._playbook_answer_contract_issues(
            chat_mod.STOCK_DEEP_RESEARCH,
            "本轮专业证据不足，因此暂不做买入判断。",
            [{"tool": "get_multi_stock_decision_evidence", "result": {"success": False}}],
        )
        == []
    )

def test_mapping_contract_does_not_parse_answer_tables_for_state():
    prose_only = "兆威机电003021属于上游环节，L2证据为已披露送样；" "订单收入缺口待核验，来源为2026年公告。"
    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        prose_only,
        [],
    )
    assert issues == []

    table = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源与日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 兆威机电 (003021) | 上游 | L2 | 已披露送样 | 订单收入待核验 | [公司公告](https://example.com/003021)，2026-06-01 |\n"
        "\n本轮仅列上述代表公司，其他环节未覆盖，需进一步核验；仅概念公司未列入。\n\n"
        "| 未覆盖环节 | 原因 |\n|---|---|\n| 减速器 | 本轮证据不足 |"
    )
    assert (
        chat_mod._playbook_answer_contract_issues(
            chat_mod.THEME_COMPANY_MAPPING,
            table,
            [],
        )
        == []
    )

    pending_code = (
        "| 公司/代码 | 环节 | 证据等级 | 已验证事实 | 缺口 | 来源日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 雷迪克（代码待核验） | 上游 | L2 | 已披露送样 | 收入待核验 | [来源](https://example.com/a)，2026-07-01 |\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )
    pending_issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        pending_code,
        [],
    )
    assert pending_issues == []

def test_mapping_contract_does_not_compare_rendered_prose_with_candidate_pool():
    table = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源与日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 兆威机电 (003021) | 上游 | L1 | 已验证概念关联 | 订单收入待核验 | "
        "[概念板块](https://example.com/theme)，2026-07-17 |\n\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )
    candidate_names = [
        ("兆威机电", "003021"),
        ("绿的谐波", "688017"),
        ("汇川技术", "300124"),
        ("机器人", "300024"),
        ("秦川机床", "000837"),
        ("巨轮智能", "002031"),
        ("新时达", "002527"),
        ("博实股份", "002698"),
    ]
    evidence = [
        {
            "tool": "get_theme_stock_candidates",
            "result": {
                "success": True,
                "items": [{"name": name, "symbol": symbol} for name, symbol in candidate_names],
            },
        }
    ]

    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        table,
        evidence,
    )

    assert issues == []

def test_mapping_contract_leaves_semantic_grounding_to_the_model():
    table = (
        "| 公司/代码 | 产业链环节 | 证据等级 | 已验证事实 | 证据缺口 | 来源与日期 |\n"
        "|---|---|---|---|---|---|\n"
        "| 汇川技术 (300124) | 伺服电机/驱动 | L1 | 属于机器人概念板块成分股，是工业自动化龙头 | "
        "订单收入待核验 | [新浪概念板块](http://vip.stock.finance.sina.com.cn/mkt/#gn_zjqrgn)，2026-07-17 |\n\n"
        "本轮仅列代表公司，未覆盖环节需进一步核验。"
    )

    issues = chat_mod._playbook_answer_contract_issues(
        chat_mod.THEME_COMPANY_MAPPING,
        table,
        [],
    )

    assert issues == []

def test_legacy_theme_mapping_fallback_is_removed():
    assert not hasattr(chat_mod, "_build_theme_mapping_fallback")

def test_mapping_synthesis_receives_runtime_verified_candidate_codes():
    messages = chat_mod._build_synthesis_messages(
        [{"role": "user", "content": "这些领域有哪些公司"}],
        [
            {
                "tool": "search_financial_news",
                "result": {"success": True, "items": [{"title": "雷迪克推进机器人丝杠送样"}]},
            }
        ],
        chat_mod.THEME_COMPANY_MAPPING,
    )
    evidence_text = messages[-1]["content"]
    assert "runtime_security_entity_map" in evidence_text
    assert "雷迪克" in evidence_text
    assert "300652" in evidence_text
