from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from src.services.research_alerts import save_rule, evaluate_rule, dispatch_notification
from src.services.research_archive import project_research_conclusions
from src.storage import DatabaseManager
from src.storage.models import AgentFinancialConclusion, AlertTriggerRecord


@pytest.fixture(autouse=True)
def maintained_data(monkeypatch):
    """Only the market HTTP boundary is replaced; research persistence is real."""
    state = {
        "financials": {"600519": {"report_date": "2026-06-30", "revenue_ttm": 100}},
        "bars": [],
    }

    def snapshot(codes, *args, **kwargs):
        return {
            "items": {
                code: {
                    "financials": state["financials"].get(code),
                    "kline": [],
                    "news": [],
                    "versions": {"financials": "fixture-version"},
                }
                for code in codes
            }
        }

    client = Mock()
    client.snapshot.side_effect = snapshot
    monkeypatch.setattr(
        "src.services.market_data_client.get_market_data_client", lambda: client
    )
    monkeypatch.setattr(
        "src.services.market_data_history.read_source",
        lambda *args, **kwargs: {
            "data": state["bars"],
            "source": "fixture",
            "data_service": {"version": "fixture-version"},
        },
    )
    state["client"] = client
    return state


@pytest.fixture
def db(tmp_path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'research.db'}")
    yield manager
    DatabaseManager.reset_instance()


def research_state():
    return {
        "structured_answer": {
            "blocks": [
                {
                    "kind": "recommendation",
                    "content": "600519 的历史财务数据支持继续观察。",
                    "source_ids": [1],
                }
            ],
            "research": [
                {"symbol": "600519", "verdict": "watch", "block_indices": [1]}
            ],
        },
        "evidence": [
            {
                "evidence_id": "ev_test",
                "action_id": "read",
                "success": True,
                "effect": "read",
                "tool_name": "read_metric",
                "source_refs": ["https://example.test/financial"],
                "entities": {"symbol": "600519"},
                "data_time": "2026-09-04",
                "data_time_provenance": "source",
                "result": {"symbol": "600519", "revenue": 100},
            }
        ],
        "tool_results": [
            {
                "action_id": "read",
                "tool_name": "read_metric",
                "arguments": {"symbol": "600519"},
                "success": True,
            }
        ],
    }


def test_research_requires_current_verified_blocks():
    state = research_state()
    result = project_research_conclusions(state, as_of=datetime(2026, 9, 6))
    assert len(result) == 1
    assert result[0]["conclusion_type"] == "research"
    state["structured_answer"]["research"][0]["symbol"] = "000001"
    assert project_research_conclusions(state, as_of=datetime.now()) == []
    state["structured_response"] = state.pop("structured_answer")
    assert project_research_conclusions(state, as_of=datetime.now()) == []


def test_archived_runs_survive_runtime_retention_and_are_owned(db):
    db.create_chat_conversation("conversation", tenant_id="tenant", owner_id="owner")
    db.claim_agent_run(
        run_id="run",
        conversation_id="conversation",
        request_payload={},
        worker_id="worker",
        tenant_id="tenant",
        owner_id="owner",
    )
    conclusions = project_research_conclusions(
        research_state(), as_of=datetime(2026, 9, 6)
    )
    db.commit_agent_run_terminal(
        run_id="run",
        conversation_id="conversation",
        status="completed",
        final_text="600519 继续观察",
        messages=[],
        agent_context={},
        worker_id="worker",
        conclusions=conclusions,
    )
    assert len(db.list_financial_conclusions(tenant_id="tenant", owner_id="owner")) == 1
    assert db.list_financial_conclusions(tenant_id="tenant", owner_id="other") == []
    db.prune_agent_runtime_data(finished_before=datetime.now() + timedelta(days=1))
    with db.get_session() as session:
        assert session.query(AgentFinancialConclusion).count() == 1


def rule_input(enabled=False, notification=False):
    return {
        "name": "财务变化",
        "target_scope": "single_symbol",
        "target": "600519",
        "enabled": enabled,
        "notification_enabled": notification,
        "parameters": {
            "kinds": ["financial"],
            "interval_seconds": 60,
            "cooldown_seconds": 120,
            "below_price": None,
        },
    }


def test_alert_baseline_opt_in_cooldown_and_once_only_delivery(db, maintained_data):
    rule = save_rule(db, "tenant", "owner", rule_input())
    assert evaluate_rule(db, rule["id"])["status"] == "disabled"
    with pytest.raises(KeyError):
        evaluate_rule(db, rule["id"], tenant_id="tenant", owner_id="other")
    rule = save_rule(db, "tenant", "owner", rule_input(True, True), rule["id"])
    now = datetime.now()
    assert evaluate_rule(db, rule["id"], now=now)["status"] == "unchanged"
    maintained_data["financials"]["600519"]["revenue_ttm"] = 120
    changed = evaluate_rule(db, rule["id"], now=now + timedelta(seconds=61))
    assert changed["status"] == "changed"
    send = Mock(return_value=True)
    assert dispatch_notification(db, changed["trigger_id"], send)
    assert not dispatch_notification(db, changed["trigger_id"], send)
    assert send.call_count == 1
    maintained_data["financials"]["600519"]["revenue_ttm"] = 130
    assert (
        evaluate_rule(db, rule["id"], now=now + timedelta(seconds=90))["status"]
        == "cooldown"
    )
    assert (
        evaluate_rule(db, rule["id"], now=now + timedelta(seconds=200))["status"]
        == "changed"
    )
    with db.get_session() as session:
        assert session.query(AlertTriggerRecord).count() == 2


def test_pause_revokes_pending_delivery_even_after_reenable(db):
    rule = save_rule(db, "tenant", "owner", rule_input(True, True))
    with db.session_scope() as session:
        trigger = AlertTriggerRecord(
            rule_id=rule["id"],
            target="600519",
            reason="pending",
            status="pending",
            diagnostics="{}",
        )
        session.add(trigger)
        session.flush()
        trigger_id = trigger.id
    save_rule(db, "tenant", "owner", rule_input(False, True), rule["id"])
    save_rule(db, "tenant", "owner", rule_input(True, True), rule["id"])
    send = Mock(return_value=True)
    assert not dispatch_notification(db, trigger_id, send)
    send.assert_not_called()


def test_unconfirmed_notification_is_not_automatically_retried(db):
    rule = save_rule(db, "tenant", "owner", rule_input(True, True))
    with db.session_scope() as session:
        trigger = AlertTriggerRecord(
            rule_id=rule["id"],
            target="600519",
            reason="unknown",
            status="pending",
            diagnostics="{}",
        )
        session.add(trigger)
        session.flush()
        trigger_id = trigger.id
    send = Mock(side_effect=RuntimeError("uncertain response"))
    assert not dispatch_notification(db, trigger_id, send)
    assert not dispatch_notification(db, trigger_id, send)
    assert send.call_count == 1
    with db.get_session() as session:
        assert session.get(AlertTriggerRecord, trigger_id).status == "delivery_unknown"


def test_outcomes_exclude_today_future_bars_and_do_not_label_watch_as_prediction(
    db, maintained_data
):
    as_of = datetime.now().replace(
        hour=12, minute=0, second=0, microsecond=0
    ) - timedelta(days=10)
    baseline = as_of.date() - timedelta(days=1)
    maintained_data["bars"] = [{"date": baseline.isoformat(), "close": 100}]
    maintained_data["bars"] += [
        {
            "date": (as_of.date() + timedelta(days=index)).isoformat(),
            "close": 101 + index,
        }
        for index in range(3)
    ]
    maintained_data["bars"] += [
        {
            "date": (datetime.now().date() + timedelta(days=offset)).isoformat(),
            "close": 99999,
        }
        for offset in (0, 1)
    ]
    db.create_chat_conversation("c", tenant_id="tenant", owner_id="owner")
    db.claim_agent_run(
        run_id="r",
        conversation_id="c",
        request_payload={},
        worker_id="worker",
        tenant_id="tenant",
        owner_id="owner",
    )
    db.commit_agent_run_terminal(
        run_id="r",
        conversation_id="c",
        status="completed",
        final_text="观察",
        messages=[],
        agent_context={},
        worker_id="worker",
        conclusions=project_research_conclusions(research_state(), as_of=as_of),
    )
    db.refresh_financial_conclusion_outcomes(tenant_id="tenant", owner_id="owner")
    note = db.list_financial_conclusions(tenant_id="tenant", owner_id="owner")[0]
    assert note["baseline_price"] == 100
    assert all(outcome["status"] == "pending" for outcome in note["outcomes"])
    maintained_data["bars"] += [
        {
            "date": (as_of.date() + timedelta(days=index)).isoformat(),
            "close": 101 + index,
            "high": 110,
            "low": 95,
        }
        for index in (3, 4)
    ]
    maintained_data["bars"].sort(key=lambda row: row["date"])
    db.refresh_financial_conclusion_outcomes(tenant_id="tenant", owner_id="owner")
    outcomes = db.list_financial_conclusions(tenant_id="tenant", owner_id="owner")[0][
        "outcomes"
    ]
    five = next(item for item in outcomes if item["horizon_trading_days"] == 5)
    assert five["status"] == "completed" and five["return_pct"] == 5
    assert five["directional_success"] is None
    assert five["engine_version"] == "financial-outcome-1.1"
    assert five["max_high"] == 110 and five["min_low"] == 95


def test_group_snapshot_uses_one_remote_batch_and_one_owned_research_query(
    db, maintained_data
):
    from sqlalchemy import event
    from src.services.research_alerts import snapshot

    statements = []
    with db.get_session() as session:
        engine = session.get_bind()

        def collect(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", collect)
        try:
            result = snapshot(
                session,
                [f"{i:06}" for i in range(200)],
                "tenant",
                "owner",
                datetime.now(),
            )
        finally:
            event.remove(engine, "before_cursor_execute", collect)
    assert len(result) == 200
    assert len(statements) == 1
    maintained_data["client"].snapshot.assert_called_once()
