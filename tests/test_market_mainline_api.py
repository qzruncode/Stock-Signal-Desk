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



"""Shared fixtures for the focused test slices."""

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
