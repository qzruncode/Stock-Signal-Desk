# -*- coding: utf-8 -*-

from unittest.mock import patch
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
import src.auth as auth
from src.services.market_theme_service import MarketThemeService
from src.storage import MarketMainlineReport


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None


def test_market_mainline_route_returns_service_payload(client, monkeypatch):
    from api.v1.endpoints import market_themes

    monkeypatch.setattr(
        market_themes.MarketThemeService,
        "analyze",
        lambda self, force=False, use_llm=True: {
            "headline": "测试主线",
            "market_regime": "政策预期驱动",
            "primary_judgement": "这里是摘要",
            "investment_takeaway": "这里是提示",
            "policy_watchlist": ["政策观察点"],
            "current_themes": [],
            "next_themes": [],
            "source_notes": ["测试源"],
            "llm_used": False,
            "model_used": None,
            "generated_at": "2026-06-11 12:00:00 CST",
        },
    )

    response = client.get("/api/v1/market/mainline", params={"force": "true", "use_llm": "false"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["headline"] == "测试主线"
    assert payload["market_regime"] == "政策预期驱动"
    assert payload["llm_used"] is False


def test_model_report_task_runs_in_isolated_process(monkeypatch):
    service = MarketThemeService()
    progress_calls = []
    result_calls = []

    class DummyQueue:
        def update_task_progress(self, task_id, progress, message):
            progress_calls.append((task_id, progress, message))

        def update_task_result(self, task_id, result, **kwargs):
            result_calls.append((task_id, result, kwargs))

    monkeypatch.setattr(
        service,
        "_run_isolated",
        lambda *, force, layer, timeout=35: {
            "overview": "隔离报告摘要",
            "full_report": "隔离报告正文",
            "as_of_date": "2026-06-12",
            "market_stage": {"label": "主升初期", "description": "测试描述"},
            "llm_used": True,
            "model_used": "test-model",
            "debug_input": {"system_prompt": "s", "user_prompt": "u", "evidence_pack": {"k": "v"}},
        },
    )

    payload = service._generate_model_report_isolated_task(
        force=True,
        task_queue=DummyQueue(),
        task_id="task-1",
    )

    assert progress_calls[0] == ("task-1", 5, "正在启动独立研判进程")
    assert result_calls[0][1]["phase"] == "starting_subprocess"
    assert result_calls[-1][1]["phase"] == "finalizing"
    assert payload["phase"] == "completed"
    assert payload["stream_text"] == "隔离报告正文"
    assert payload["report"]["model_used"] == "test-model"


def test_submit_model_report_task_uses_streaming_runner(monkeypatch):
    service = MarketThemeService()
    captured = {}

    class DummyTaskQueue:
        def submit_background_task(self, run_task, **kwargs):
            captured["run_task"] = run_task
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                task_id=kwargs["task_id"],
                status=SimpleNamespace(value="pending"),
                message=kwargs["message"],
            )

        def update_task_progress(self, task_id, progress, message):
            return None

        def update_task_result(self, task_id, result, **kwargs):
            return None

    monkeypatch.setattr(
        "src.services.task_queue.get_task_queue",
        lambda: DummyTaskQueue(),
    )
    monkeypatch.setattr(
        service,
        "_generate_model_report_stream",
        lambda **kwargs: {"phase": "completed", "report": {"overview": "ok"}, **kwargs},
    )

    task = service.submit_model_report_task(force=False)
    result = captured["run_task"]()

    assert task.task_id
    assert captured["kwargs"]["stock_code"] == "MARKET_MAINLINE"
    assert captured["kwargs"]["report_type"] == "market_mainline_report"
    assert result["phase"] == "completed"
    assert result["force"] is False


def test_get_model_report_returns_ready_cached_payload(monkeypatch):
    service = MarketThemeService()

    monkeypatch.setattr(
        service,
        "_get_latest_report",
        lambda: {
            "overview": "已生成",
            "full_report": "完整正文",
            "as_of_date": "2026-06-13",
            "llm_used": True,
            "model_used": "openai/glm-5.1",
        },
    )

    payload = service.get_model_report()

    assert payload["_cached"] is True
    assert payload["report_pending"] is False
    assert payload["llm_used"] is True
    assert payload["model_used"] == "openai/glm-5.1"


def test_get_model_report_returns_pending_payload_when_no_cache(monkeypatch):
    service = MarketThemeService()

    monkeypatch.setattr(service, "_get_latest_report", lambda: None)
    monkeypatch.setattr(
        service,
        "_build_minimal_model_report",
        lambda: {
            "overview": "",
            "full_report": "",
            "as_of_date": "2026-06-13",
        },
    )

    payload = service.get_model_report()

    assert payload["_cached"] is False
    assert payload["report_pending"] is True
    assert payload["llm_used"] is False


def test_market_mainline_storage_backfills_full_report_from_raw_response():
    row = MarketMainlineReport(
        report_key="market_mainline",
        as_of_date="2026-06-13",
        mode="llm",
        model_used="openai/glm-5.1",
        overview="摘要",
        market_stage_label="主升",
        market_stage_description="阶段说明",
        raw_response='{"full_report":"完整模型输出","overview":"摘要"}',
        payload='{"overview":"摘要"}',
    )

    payload = row.to_dict()

    assert payload["full_report"] == "完整模型输出"
    assert payload["overview"] == "摘要"


def test_generate_model_report_stream_returns_raw_response_as_stream_text(monkeypatch):
    service = MarketThemeService()

    monkeypatch.setattr(service, "_collect_context", lambda **kwargs: {"source_snapshot": {"market_status": {"data_time": "2026-06-13"}}, "generated_at": "2026-06-13 13:00:00 CST"})
    monkeypatch.setattr(service, "_build_report_evidence_pack", lambda context: {"as_of_date": "2026-06-13"})
    monkeypatch.setattr(service, "_build_model_report_prompts", lambda evidence_pack: ("sys", "user"))
    monkeypatch.setattr(
        service,
        "_build_llm_model_report_streaming",
        lambda *args, **kwargs: {
            "overview": "摘要",
            "full_report": "正文",
            "raw_stream_output": "原始完整输出",
            "raw_response": "原始完整输出",
            "as_of_date": "2026-06-13",
            "market_stage": {"label": "阶段", "description": "描述"},
            "llm_used": True,
            "model_used": "openai/glm-5.1",
        },
    )

    class DummyQueue:
        def update_task_progress(self, task_id, progress, message):
            return None

        def update_task_result(self, task_id, result, **kwargs):
            return None

    payload = service._generate_model_report_stream(force=False, task_queue=DummyQueue(), task_id="task-1")

    assert payload["stream_text"] == "原始完整输出"


def test_market_mainline_storage_defaults_raw_stream_output():
    row = MarketMainlineReport(
        report_key="market_mainline",
        as_of_date="2026-06-13",
        mode="llm",
        model_used="openai/glm-5.1",
        overview="摘要",
        market_stage_label="主升",
        market_stage_description="阶段说明",
        raw_response='{"full_report":"完整模型输出","overview":"摘要"}',
        payload='{"overview":"摘要","full_report":"完整模型输出"}',
    )

    payload = row.to_dict()

    assert payload["raw_stream_output"] == '{"full_report":"完整模型输出","overview":"摘要"}'


def test_extract_json_object_from_text_handles_reasoning_prefix():
    raw_text = (
        "1. analyze request\\n"
        "2. think about sectors\\n"
        '{"generated_at":"2026-06-13 13:08:34 CST","as_of_date":"2026-06-12","overview":"摘要","full_report":"正文"}'
    )

    extracted = MarketThemeService._extract_json_object_from_text(raw_text)

    assert extracted == '{"generated_at":"2026-06-13 13:08:34 CST","as_of_date":"2026-06-12","overview":"摘要","full_report":"正文"}'
