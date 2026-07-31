# -*- coding: utf-8 -*-
"""Financial conclusion extraction and deterministic outcome validation."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from src.agent.financial_conclusions import (
    extract_financial_conclusions,
)
from src.storage import DatabaseManager
from src.storage.models import StockDaily


@pytest.fixture
def database(tmp_path: Path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(
        db_url=f"sqlite:///{tmp_path / 'financial-lifecycle.db'}"
    )
    try:
        yield manager
    finally:
        DatabaseManager.reset_instance()


def _buy_outcome(
    *,
    analysis_status: str = "completed",
    decision: str = "可买入",
):
    return {
        "task_id": "buy-task",
        "status": "succeeded",
        "result": {
            "calls": [
                {
                    "tool": "evaluate_multi_stock_buy_criteria",
                    "result": {
                        "contract_version": "buy-gate-v9",
                        "thesis": "测试投资逻辑",
                        "data_time": "2026-01-02T16:00:00+08:00",
                        "source": "内置行情与财务数据",
                        "items": [
                            {
                                "symbol": "600519",
                                "name": "贵州茅台",
                                "analysis_status": analysis_status,
                                "final_decision": decision,
                                "coverage_complete": True,
                                "gate_pass_complete": (
                                    decision == "可买入"
                                ),
                                "criteria": [
                                    {
                                        "criterion_id": "market_mainline",
                                        "status": "pass",
                                    }
                                ],
                            }
                        ],
                    },
                }
            ]
        },
    }


def test_extractor_uses_typed_packet_and_ignores_unfinished_analysis():
    conclusions = extract_financial_conclusions([_buy_outcome()])
    assert len(conclusions) == 1
    assert conclusions[0]["symbol"] == "600519"
    assert conclusions[0]["verdict"] == "buy"

    unfinished = extract_financial_conclusions(
        [_buy_outcome(analysis_status="source_unavailable")]
    )
    assert unfinished == []


def test_terminal_commit_registers_and_evaluates_forward_outcomes(database):
    baseline_date = date(2026, 1, 2)
    with database.session_scope() as session:
        session.add(
            StockDaily(
                code="600519",
                date=baseline_date,
                close=100.0,
                high=101.0,
                low=99.0,
                data_source="builtin-test",
            )
        )
        for index in range(1, 61):
            price = 100.0 + index
            session.add(
                StockDaily(
                    code="600519",
                    date=baseline_date + timedelta(days=index),
                    close=price,
                    high=price + 1.0,
                    low=price - 1.0,
                    data_source="builtin-test",
                )
            )

    conversation_id = "financial-conversation"
    run_id = "financial-run"
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    claimed = database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={"messages": []},
        worker_id="worker-a",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert claimed["claimed"] is True
    conclusions = extract_financial_conclusions([_buy_outcome()])
    assert database.commit_agent_run_terminal(
        run_id=run_id,
        conversation_id=conversation_id,
        status="completed",
        messages=[],
        final_text="可买入",
        agent_context={},
        conclusions=conclusions,
        trace={"status": "completed"},
        worker_id="worker-a",
    )

    before = database.list_financial_conclusions(
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert len(before) == 1
    assert before[0]["lifecycle_status"] == "pending"
    assert before[0]["baseline_price"] == 100.0

    refreshed = database.refresh_financial_conclusion_outcomes(
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert refreshed["outcomes_completed"] == 3
    after = database.list_financial_conclusions(
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert after[0]["lifecycle_status"] == "completed"
    outcome_20d = next(
        item
        for item in after[0]["outcomes"]
        if item["horizon_trading_days"] == 20
    )
    assert outcome_20d["return_pct"] == 20.0
    assert outcome_20d["directional_success"] is True

    calibration = database.financial_conclusion_calibration(
        tenant_id="tenant-a",
        owner_id="owner-a",
        horizon_trading_days=20,
    )
    assert calibration["buy"]["directional_accuracy"] == 1.0
    assert calibration["probability_calibration"]["available"] is False


def test_not_buy_is_not_mislabeled_as_bearish_accuracy(database):
    outcome = _buy_outcome(decision="不可买入")
    conclusion = extract_financial_conclusions([outcome])[0]
    assert conclusion["verdict"] == "not_buy"
