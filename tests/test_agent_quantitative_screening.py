from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from api.v1.endpoints.agent import exports
from api.v1.endpoints.agent.chat import _build_atr_screen_answer
from api.v1.endpoints.agent import tool_registry_meta


def test_deterministic_answer_uses_tool_rows_and_download_link() -> None:
    answer = _build_atr_screen_answer([{
        "tool": "screen_atr_volatility_stocks",
        "result": {
            "success": True,
            "items": [{
                "code": "000001", "name": "平安银行", "current_atr_pct": 2.1,
                "long_term_mean_pct": 2.0, "dynamic_warning_pct": 1.57,
                "qualified_days": 180, "qualified_ratio_pct": 72,
                "revenue_ttm": 1_000_000_000, "deducted_net_profit_ttm": 20_000_000,
                "debt_ratio": 50,
            }],
            "total": 11,
            "download_url": "/api/v1/agent/exports/result.csv",
            "data_time": "2026-07-17",
            "financial_report_period": "2026-03-31",
            "coverage": {"active": 5528, "financial_covered": 5523, "financial_eligible": 3000, "fresh_kline": 3000},
            "source": "verified source",
        },
    }])

    assert "平安银行 (000001)" in answer
    assert "共 **11 只**" in answer
    assert "[下载完整 11 只筛选结果（CSV）](/api/v1/agent/exports/result.csv)" in answer
    assert "verified source" in answer


def test_deterministic_answer_fails_closed_without_tool_coverage() -> None:
    answer = _build_atr_screen_answer([{
        "tool": "screen_atr_volatility_stocks",
        "result": {"success": False, "errors": ["行情刷新失败"], "coverage": {"active": 5528}},
    }])
    assert "本轮不输出任何股票结论" in answer
    assert "行情刷新失败" in answer


def test_export_endpoint_serves_only_generated_file_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)
    file_id = "atr-volatility-20260718-120000-abcdef12.csv"
    (tmp_path / file_id).write_text("股票代码\n000001\n", encoding="utf-8")

    response = asyncio.run(exports.download_agent_export(file_id))
    assert response.path == tmp_path / file_id

    with pytest.raises(HTTPException) as exc:
        asyncio.run(exports.download_agent_export("../secret.csv"))
    assert exc.value.status_code == 404


def test_tool_registry_uses_long_timeout_for_full_market_screen() -> None:
    assert tool_registry_meta._execution_timeout(
        "screen_atr_volatility_stocks", {"refresh_if_stale": True},
    ) == tool_registry_meta._QUANTITATIVE_SCREEN_TIMEOUT_SECONDS
