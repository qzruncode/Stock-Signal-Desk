from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from api.v1.endpoints.agent import exports
from api.v1.endpoints.agent.chat import _build_quantitative_screen_answer
from api.v1.endpoints.agent import tool_registry_meta
from src.agent.task_workflows import StandardTaskKind, workflow_for


def _successful_result() -> dict:
    return {
        "success": True,
        "screen_spec": {
            "version": "1.0",
            "preview_limit": 10,
            "sort": {"field": "qualified_ratio_pct", "order": "desc"},
        },
        "spec_fingerprint": "abc123def456",
        "applied_rules": [
            "ATR=TR的20日指数移动平均",
            "长期均值为90日指数移动平均；动态线=长期均值*1.1",
            "统计最近120个交易日；达标天数>=72且达标比例>=60%",
            "营业收入TTM>10亿元",
            "按达标比例降序",
        ],
        "columns": [
            {"field": "code", "label": "股票代码", "format": "text"},
            {"field": "name", "label": "股票名称", "format": "text"},
            {"field": "current_atr_pct", "label": "当前ATR相对波动率(%)", "format": "percent"},
            {"field": "qualified_days", "label": "近120日达标天数", "format": "integer"},
            {"field": "revenue_ttm", "label": "营业收入TTM(元)", "format": "currency_yuan"},
        ],
        "items": [{
            "code": "000001", "name": "平安银行", "current_atr_pct": 2.1,
            "qualified_days": 80, "revenue_ttm": 1_500_000_000,
        }],
        "total": 11,
        "download_url": "/api/v1/agent/exports/result.csv",
        "data_time": "2026-07-17",
        "data_times": {
            "kline_expected_date": "2026-07-17",
            "financial_report_period": "2026-03-31",
        },
        "financial_report_period": "2026-03-31",
        "coverage": {
            "universe": 5528, "history_preexcluded": 30, "financial_covered": 5498,
            "financial_eligible": 2800, "fresh_kline": 2800,
            "financial_cache_count": 5495, "financial_fallback_count": 3, "complete": True,
        },
        "source": "verified source",
        "warnings": ["财务主源故障后已切换本日快照"],
    }


def test_deterministic_answer_uses_dynamic_spec_columns_and_download_link() -> None:
    answer = _build_quantitative_screen_answer([{
        "tool": "screen_atr_volatility_stocks",
        "result": _successful_result(),
    }])

    assert "000001" in answer and "平安银行" in answer
    assert "共 **11 只**" in answer
    assert "近120日达标天数" in answer
    assert "20日指数移动平均" in answer
    assert "规格指纹：`abc123def456`" in answer
    assert "[下载完整 11 只筛选结果（CSV）](/api/v1/agent/exports/result.csv)" in answer
    assert "verified source" in answer
    assert "本日财务快照接管 5495 只" in answer
    assert "财务主源故障后已切换本日快照" in answer
    assert "14日TR简单均值" not in answer
    assert "近250日" not in answer


def test_deterministic_answer_confirms_saved_watchlist_group() -> None:
    result = _successful_result()
    result["saved_group"] = {"id": 3, "name": "高波动观察", "count": 11}

    answer = _build_quantitative_screen_answer([{
        "tool": "screen_atr_volatility_stocks",
        "result": result,
    }])

    assert "已保存到自选分组" in answer
    assert "高波动观察" in answer
    assert "共 11 只股票" in answer


def test_deterministic_answer_fails_closed_without_tool_coverage() -> None:
    answer = _build_quantitative_screen_answer([{
        "tool": "screen_atr_volatility_stocks",
        "result": {
            "success": False, "failure_stage": "kline_coverage",
            "errors": ["行情刷新失败"], "coverage": {"universe": 5528},
        },
    }])
    assert "本轮不输出任何股票结论" in answer
    assert "kline_coverage" in answer
    assert "行情刷新失败" in answer


@pytest.mark.parametrize("missing_key", ["screen_spec", "columns", "applied_rules"])
def test_deterministic_answer_rejects_success_without_auditable_contract(missing_key: str) -> None:
    result = _successful_result()
    result.pop(missing_key)
    answer = _build_quantitative_screen_answer([{
        "tool": "screen_atr_volatility_stocks", "result": result,
    }])
    assert "本轮不输出任何股票结论" in answer


def test_deterministic_answer_rejects_missing_requested_row_field() -> None:
    result = _successful_result()
    result["items"][0].pop("revenue_ttm")
    answer = _build_quantitative_screen_answer([{
        "tool": "screen_atr_volatility_stocks", "result": result,
    }])
    assert "预览行缺少请求字段" in answer


def test_export_endpoint_serves_only_generated_file_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)
    file_id = "stock-screen-20260718-120000-abcdef12.csv"
    (tmp_path / file_id).write_text("股票代码\n000001\n", encoding="utf-8")

    response = asyncio.run(exports.download_agent_export(file_id))
    assert response.path == tmp_path / file_id

    with pytest.raises(HTTPException) as exc:
        asyncio.run(exports.download_agent_export("../secret.csv"))
    assert exc.value.status_code == 404


def test_full_market_screen_has_no_application_deadline() -> None:
    workflow = workflow_for(StandardTaskKind.STOCK_SCREENING)
    assert not hasattr(workflow, "timeout_seconds")
    assert not hasattr(tool_registry_meta, "_execution_timeout")
