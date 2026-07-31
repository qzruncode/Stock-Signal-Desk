# -*- coding: utf-8 -*-

from unittest.mock import patch
from types import SimpleNamespace

import pytest

from src.services.market_theme._context import (
    MARKET_MAINLINE_REPORT_CONTRACT,
    build_minimal_model_report,
    build_report_evidence_pack,
    get_latest_report,
)
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth
from src.services.market_theme_service import MarketThemeService
from src.services.market_theme._context import collect_context
from src.services.market_theme._streaming import (
    stream_market_mainline_report_via_litellm,
)
from src.services.market_theme._llm import _validate_model_report
from src.services.buy_criteria.data_service import DataService, _clear_cache
from src.services.task_queue import TaskStatus
from src.storage import MarketMainlineReport



"""Focused test slice 2; shared fixtures remain local to this slice."""

def _valid_market_mainline_payload() -> dict:
    return {
        "generated_at": "2026-07-29T10:00:00+08:00",
        "as_of_date": "2026-07-29",
        "overview": "机器人与国产算力是当前主线。",
        "full_report": "证据显示机器人与国产算力处于产业兑现阶段。",
        "market_stage": {
            "label": "结构性行情",
            "description": "多条产业主线并行。",
        },
        "current_mainlines": [
            {
                "name": "机器人",
                "lifecycle": "confirmed",
                "stage": "兑现期",
                "reason": "产业订单正在落地。",
                "branches": ["机器人"],
                "focus": "订单兑现",
                "risks": ["订单低于预期"],
                "evidence_refs": ["evidence-1"],
            }
        ],
        "candidate_mainlines": [
            {
                "name": "商业航天",
                "lifecycle": "emerging",
                "stage_hint": "观察期",
                "reason": "产业政策逐步落地。",
                "branches": ["商业航天"],
                "expected_horizon": "one_to_six_months",
                "evidence_axes": [
                    {
                        "axis": "policy",
                        "evidence_refs": ["evidence-2"],
                    },
                    {
                        "axis": "supply_demand",
                        "evidence_refs": ["evidence-3"],
                    },
                ],
                "trigger_assessments": [
                    {
                        "description": "订单确认",
                        "status": "partial",
                        "evidence_refs": ["evidence-2"],
                    }
                ],
                "evidence_refs": ["evidence-2", "evidence-3"],
            }
        ],
        "action_summary": ["跟踪订单兑现"],
        "evidence_digest": {
            "policy": ["政策支持"],
            "industry": ["订单增长"],
            "market": [],
        },
    }

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
    ):
        yield
    auth._auth_enabled = None
def test_market_mainline_request_streams_forced_schema_without_local_timeout(
    monkeypatch,
) -> None:
    captured: dict = {}
    reasoning: list[str] = []
    payload = _valid_market_mainline_payload()
    serialized_payload = __import__("json").dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    class AsyncStream:
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration:
                raise StopAsyncIteration

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return AsyncStream(
            [
                SimpleNamespace(
                    model="test-model",
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                reasoning_content="先核对证据。",
                                tool_calls=None,
                            ),
                        ),
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    model="test-model",
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                reasoning_content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        function=SimpleNamespace(
                                            name="submit_market_mainline_report",
                                            arguments=serialized_payload[:80],
                                        ),
                                    ),
                                ],
                            ),
                        ),
                    ],
                    usage=None,
                ),
                SimpleNamespace(
                    model="test-model",
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                reasoning_content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        function=SimpleNamespace(
                                            name=None,
                                            arguments=serialized_payload[80:],
                                        ),
                                    ),
                                ],
                            ),
                        ),
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                    ),
                ),
            ]
        )

    monkeypatch.setattr(
        "src.services.market_theme._streaming.resolve_anthropic_gateway_config",
        lambda: {"model": "test-model"},
    )
    monkeypatch.setattr(
        "src.services.market_theme._streaming.apply_litellm_generation_params",
        lambda kwargs, _model, _temperature: kwargs,
    )
    monkeypatch.setattr(
        "src.services.market_theme._streaming.litellm.acompletion",
        fake_completion,
    )

    raw, content, model, _usage = stream_market_mainline_report_via_litellm(
        system_prompt="system",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
        on_reasoning=reasoning.append,
    )

    assert __import__("json").loads(raw) == payload
    assert __import__("json").loads(content) == payload
    assert model == "test-model"
    assert captured["stream"] is True
    assert "timeout" not in captured
    assert captured["tool_choice"]["function"]["name"] == ("submit_market_mainline_report")
    assert captured["tools"][0]["function"]["name"] == ("submit_market_mainline_report")
    assert captured.get("extra_body") != {
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
    }
    assert reasoning == ["先核对证据。"]

def test_market_mainline_missing_forced_call_gets_one_targeted_repair(
    monkeypatch,
) -> None:
    calls: list[dict] = []
    reasoning: list[str] = []
    payload = _valid_market_mainline_payload()
    serialized = __import__("json").dumps(payload, ensure_ascii=False)

    class AsyncStream:
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration:
                raise StopAsyncIteration

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return AsyncStream(
                [
                    SimpleNamespace(
                        model="test-model",
                        choices=[
                            SimpleNamespace(
                                finish_reason="length",
                                delta=SimpleNamespace(
                                    content=None,
                                    reasoning_content="完整但未提交的市场分析过程",
                                    tool_calls=None,
                                ),
                            )
                        ],
                        usage=SimpleNamespace(
                            prompt_tokens=10,
                            completion_tokens=20,
                            total_tokens=30,
                        ),
                    ),
                ]
            )
        return SimpleNamespace(
            model="test-model",
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="submit_market_mainline_report",
                                    arguments=serialized,
                                ),
                            )
                        ],
                    ),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=5,
                completion_tokens=8,
                total_tokens=13,
            ),
        )

    monkeypatch.setattr(
        "src.services.market_theme._streaming.resolve_anthropic_gateway_config",
        lambda: {"model": "test-model"},
    )
    monkeypatch.setattr(
        "src.services.market_theme._streaming.apply_litellm_generation_params",
        lambda kwargs, _model, _temperature: kwargs,
    )
    monkeypatch.setattr(
        "src.services.market_theme._streaming.litellm.acompletion",
        fake_completion,
    )

    raw, _content, _model, usage = stream_market_mainline_report_via_litellm(
        system_prompt="system",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
        on_reasoning=reasoning.append,
    )

    assert __import__("json").loads(raw) == payload
    assert len(calls) == 2
    assert calls[0]["tools"] == calls[1]["tools"]
    assert calls[0]["stream"] is True
    assert calls[1]["stream"] is False
    assert calls[1]["extra_body"] == {
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
    }
    repair = __import__("json").loads(calls[1]["messages"][-1]["content"])["targeted_repair"]
    assert repair["invalid_payload"]["finish_reason"] == "length"
    assert repair["invalid_payload"]["reasoning_content"] == "完整但未提交的市场分析过程"
    assert repair["issues"][0]["pointer"] == ("/choices/0/message/tool_calls")
    assert repair["issues"][0]["code"] == "forced_tool_call_missing"
    assert usage["prompt_tokens"] == 15
    assert usage["completion_tokens"] == 28
    assert usage["repair_record"]["succeeded"] is True
    assert reasoning == ["完整但未提交的市场分析过程"]

def test_market_mainline_schema_repair_reports_exact_field_pointer(
    monkeypatch,
) -> None:
    calls: list[dict] = []
    invalid = _valid_market_mainline_payload()
    invalid.pop("full_report")
    repaired = _valid_market_mainline_payload()

    class AsyncStream:
        def __init__(self, payload):
            self._payload = payload
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return SimpleNamespace(
                model="test-model",
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            reasoning_content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    function=SimpleNamespace(
                                        name="submit_market_mainline_report",
                                        arguments=__import__("json").dumps(
                                            self._payload,
                                            ensure_ascii=False,
                                        ),
                                    ),
                                )
                            ],
                        ),
                    )
                ],
                usage=None,
            )

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        payload = invalid if len(calls) == 1 else repaired
        if len(calls) == 1:
            return AsyncStream(payload)
        return SimpleNamespace(
            model="test-model",
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="submit_market_mainline_report",
                                    arguments=__import__("json").dumps(
                                        payload,
                                        ensure_ascii=False,
                                    ),
                                ),
                            )
                        ],
                    ),
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(
        "src.services.market_theme._streaming.resolve_anthropic_gateway_config",
        lambda: {"model": "test-model"},
    )
    monkeypatch.setattr(
        "src.services.market_theme._streaming.apply_litellm_generation_params",
        lambda kwargs, _model, _temperature: kwargs,
    )
    monkeypatch.setattr(
        "src.services.market_theme._streaming.litellm.acompletion",
        fake_completion,
    )

    _raw, _content, _model, usage = stream_market_mainline_report_via_litellm(
        system_prompt="system",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
    )

    repair = __import__("json").loads(calls[1]["messages"][-1]["content"])["targeted_repair"]
    assert calls[1]["stream"] is False
    assert repair["invalid_payload"] == invalid
    assert repair["issues"][0]["pointer"] == "/full_report"
    assert repair["issues"][0]["code"] == "missing"
    assert usage["repair_record"]["succeeded"] is True
