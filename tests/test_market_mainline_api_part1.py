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



"""Focused test slice 1; shared fixtures remain local to this slice."""

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

def test_legacy_market_mainline_report_is_not_reused(monkeypatch):
    database = SimpleNamespace(
        get_latest_market_mainline_report=lambda **_kwargs: {
            "as_of_date": "2026-07-27",
            "current_mainlines": [{"name": "旧逻辑主线"}],
        },
    )
    monkeypatch.setattr(
        "src.services.market_theme._context._current_report_as_of_date",
        lambda: "2026-07-27",
    )
    monkeypatch.setattr(
        "src.services.market_theme._context.DatabaseManager.get_instance",
        lambda: database,
    )

    assert get_latest_report("market_mainline") is None
    assert build_minimal_model_report()["contract_version"] == MARKET_MAINLINE_REPORT_CONTRACT

def test_market_mainline_context_never_collects_capital_flow(monkeypatch):
    from api.v1.endpoints import market_status, sectors

    monkeypatch.setattr(market_status, "get_market_status", lambda force=False: {})
    monkeypatch.setattr(
        sectors,
        "get_sector_list",
        lambda type, force=False: {
            "items": [
                {
                    "name": f"{type}-board",
                    "code": "BK001",
                    "change_pct": 9.9,
                    "net_flow": 1_000_000,
                }
            ],
            "data_time": "2026-07-27",
            "errors": [],
        },
    )

    payload = collect_context(force=False, include_rss=False)

    snapshot = payload["source_snapshot"]
    assert "industry_flow" not in snapshot
    assert "concept_flow" not in snapshot
    assert "market_breadth" not in snapshot
    assert snapshot["industry_sectors"] == [
        {
            "name": "industry-board",
            "code": "BK001",
            "data_source": None,
        }
    ]
    assert "change_pct" not in snapshot["industry_sectors"][0]
    assert "net_flow" not in snapshot["industry_sectors"][0]

def test_market_mainline_evidence_uses_board_catalog_only_for_mapping() -> None:
    packet = build_report_evidence_pack(
        {
            "generated_at": "2026-07-27T10:00:00+08:00",
            "source_snapshot": {
                "market_status": {"data_time": "2026-07-27"},
                "industry_sectors": [{"name": "机器人", "code": "BK001"}],
                "concept_sectors": [{"name": "人形机器人", "code": "BK002"}],
                "rss": {
                    "strategy_reports": {
                        "items": [
                            {
                                "title": "中期策略",
                                "summary": "产业资本开支持续",
                                "link": "https://example.com/report",
                            }
                        ],
                    },
                },
                "source_catalog": [],
            },
        }
    )

    assert packet["board_catalog"]["industry"][0]["name"] == "机器人"
    assert "industry_flow" not in packet
    assert "concept_flow" not in packet
    assert all(
        value["section"]
        not in {
            "industry_sector",
            "concept_sector",
            "industry_flow",
            "concept_flow",
        }
        for value in packet["evidence_refs"].values()
    )

def test_candidate_mainline_keeps_structured_branch_and_trigger_progress() -> None:
    payload = _valid_market_mainline_payload()
    payload["current_mainlines"] = []
    validated = _validate_model_report(
        payload,
        {
            "evidence_refs": {
                "evidence-1": {"name": "机器人订单"},
                "evidence-2": {"name": "商业航天政策与订单"},
                "evidence-3": {"name": "商业航天产业订单"},
            },
            "board_catalog": {
                "industry": [],
                "concept": [{"name": "商业航天"}],
            },
        },
    )

    assert validated["current_mainlines"] == []
    candidate = validated["candidate_mainlines"][0]
    assert candidate["branches"] == ["商业航天"]
    assert candidate["expected_horizon"] == "one_to_six_months"
    assert candidate["evidence_axes"] == [
        {"axis": "policy", "evidence_refs": ["evidence-2"]},
        {
            "axis": "supply_demand",
            "evidence_refs": ["evidence-3"],
        },
    ]
    assert candidate["trigger_assessments"][0] == {
        "description": "订单确认",
        "status": "partial",
        "evidence_refs": ["evidence-2"],
    }
    assert validated["future_mainlines"] == validated["candidate_mainlines"]

def test_buy_criteria_never_promotes_legacy_market_evidence_to_mainline(monkeypatch):
    monkeypatch.setattr(
        MarketThemeService,
        "get_model_report",
        lambda self, force=False, trigger_generation=True: {
            "report_pending": True,
            "as_of_date": "2026-07-21",
            "current_mainlines": [],
        },
    )
    monkeypatch.setattr(
        MarketThemeService,
        "get_cached_evidence",
        lambda self: {
            "generated_at": "2026-07-21 10:00:00 CST",
            "data_time": "2026-07-21",
            "market_stage": {"label": "主线扩散期"},
            "current_themes": [
                {
                    "name": "科技成长",
                    "rank_label": "主线",
                    "stage": "发酵期",
                    "components": ["机器人"],
                    "thesis": "产业与资金共振",
                    "evidence": ["机器人板块走强"],
                }
            ],
            "next_themes": [],
        },
    )
    _clear_cache()

    report = DataService().get_market_mainline_report()

    assert report["report_pending"] is True
    assert report["current_mainlines"] == []

def test_report_evidence_pack_uses_the_public_feed_summarizer() -> None:
    context = {
        "generated_at": "2026-07-21T12:00:00+08:00",
        "source_snapshot": {
            "market_status": {"data_time": "2026-07-21"},
            "rss": {
                "market_news": {
                    "items": [
                        {
                            "title": "人形机器人产业进展",
                            "summary": "<p>核心零部件进入验证阶段</p>",
                            "published": "2026-07-21",
                        }
                    ],
                },
            },
        },
    }
    packed = build_report_evidence_pack(context)
    assert packed["market_news"] == [
        {
            "evidence_id": "market_news:0",
            "title": "人形机器人产业进展",
            "summary": "核心零部件进入验证阶段",
            "published": "2026-07-21",
            "link": "",
        }
    ]

def test_model_report_read_does_not_spawn_background_work_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.market_theme_service.get_latest_report",
        lambda _key: None,
    )
    service = MarketThemeService()

    def forbidden_submit(*, force=True):
        raise AssertionError("short-lived readers must not start the daily report task")

    monkeypatch.setattr(service, "submit_model_report_task", forbidden_submit)
    payload = service.get_model_report()
    assert payload["report_pending"] is True

def test_model_report_trigger_reuses_the_active_generation_task(monkeypatch) -> None:
    active = SimpleNamespace(
        task_id="active-mainline-task",
        stock_code="MARKET_MAINLINE",
        report_type="market_mainline_report",
        status=TaskStatus.PROCESSING,
        progress=72,
        message="正在生成",
        error=None,
    )
    queue = SimpleNamespace(
        list_pending_tasks=lambda: [active],
    )
    monkeypatch.setattr(
        "src.services.market_theme_service.get_latest_report",
        lambda _key: None,
    )
    monkeypatch.setattr(
        "src.services.task_queue.get_task_queue",
        lambda: queue,
    )

    payload = MarketThemeService().get_model_report(
        trigger_generation=True,
    )

    assert payload["report_pending"] is True
    assert payload["generation_task"] == {
        "task_id": "active-mainline-task",
        "status": "processing",
        "progress": 72,
        "message": "正在生成",
        "error": None,
    }

def test_ensure_model_report_waits_for_a_real_persisted_report(monkeypatch) -> None:
    service = MarketThemeService()
    task = SimpleNamespace(
        task_id="mainline-task",
        status=TaskStatus.PROCESSING,
        progress=88,
        message="正在生成",
        error=None,
    )
    queue = SimpleNamespace(get_task=lambda _task_id: task)
    calls = iter(
        [
            None,
            {
                "contract_version": MARKET_MAINLINE_REPORT_CONTRACT,
                "generated_at": "2026-07-27T22:00:00+08:00",
                "as_of_date": "2026-07-27",
                "overview": "市场主线已经形成",
                "current_mainlines": [{"name": "国产算力"}],
                "report_pending": False,
                "llm_used": True,
            },
        ]
    )
    monkeypatch.setattr(
        service,
        "get_model_report",
        lambda **_kwargs: {
            "report_pending": True,
            "current_mainlines": [],
            "generation_task": {
                "task_id": "mainline-task",
                "status": "processing",
                "progress": 24,
            },
        },
    )
    monkeypatch.setattr(
        "src.services.market_theme_service.get_latest_report",
        lambda _key: next(calls),
    )
    monkeypatch.setattr(
        "src.services.task_queue.get_task_queue",
        lambda: queue,
    )
    monkeypatch.setattr(
        "src.services.market_theme_service.time.sleep",
        lambda _seconds: None,
    )

    payload = service.ensure_model_report(
        poll_interval_seconds=0.01,
    )

    assert payload["report_pending"] is False
    assert payload["current_mainlines"] == [{"name": "国产算力"}]
    assert payload["generation_task"]["task_id"] == "mainline-task"
