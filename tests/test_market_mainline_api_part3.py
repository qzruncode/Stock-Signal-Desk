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
from src.services.task_queue import TaskStatus
from src.storage import MarketMainlineReport



"""Focused test slice 3; shared fixtures remain local to this slice."""

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
def test_market_mainline_value_error_context_is_json_safe_for_repair(
    monkeypatch,
) -> None:
    calls: list[dict] = []
    invalid = _valid_market_mainline_payload()
    invalid["current_mainlines"][0]["lifecycle"] = "emerging"
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
        if len(calls) == 1:
            return AsyncStream(invalid)
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
                                        repaired,
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

    raw, _content, _model, usage = stream_market_mainline_report_via_litellm(
        system_prompt="system",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
    )

    repair = __import__("json").loads(calls[1]["messages"][-1]["content"])["targeted_repair"]
    assert __import__("json").loads(raw) == repaired
    assert repair["invalid_payload"] == invalid
    assert repair["issues"][0]["pointer"] == "/current_mainlines/0"
    assert repair["issues"][0]["code"] == "value_error"
    assert repair["issues"][0]["allowed"] == {
        "error": ("current mainline lifecycle must be confirmed, expanding or fading"),
    }
    assert usage["repair_record"]["succeeded"] is True

def test_inline_market_mainline_generation_never_submits_background_task(
    monkeypatch,
) -> None:
    service = MarketThemeService()
    reports = iter([None, None])
    generated = {
        "contract_version": MARKET_MAINLINE_REPORT_CONTRACT,
        "generated_at": "2026-07-28T10:00:00+08:00",
        "as_of_date": "2026-07-28",
        "overview": "市场主线已经形成",
        "current_mainlines": [{"name": "机器人"}],
        "report_pending": False,
        "llm_used": True,
    }
    monkeypatch.setattr(
        "src.services.market_theme_service.get_latest_report",
        lambda _key: next(reports),
    )
    monkeypatch.setattr(
        "src.services.market_theme_service.generate_model_report_inline",
        lambda **_kwargs: dict(generated),
    )
    monkeypatch.setattr(
        service,
        "submit_model_report_task",
        lambda **_kwargs: pytest.fail("inline path submitted TaskQueue work"),
    )

    payload = service.ensure_model_report_inline()

    assert payload["report_pending"] is False
    assert payload["current_mainlines"] == [{"name": "机器人"}]
