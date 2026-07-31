# -*- coding: utf-8 -*-
"""Built-in capability grants, approval receipts and audit chain."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from src.storage import DatabaseManager
from src.agent.capability_release import (
    build_capability_release_manifest,
)
from src.agent.user_memory import build_explicit_memory_message
from src.storage.models import (
    AgentEvaluationCase,
    AgentEvaluationResult,
)


@pytest.fixture
def database(tmp_path: Path):
    DatabaseManager.reset_instance()
    manager = DatabaseManager(
        db_url=f"sqlite:///{tmp_path / 'agent-governance.db'}"
    )
    try:
        yield manager
    finally:
        DatabaseManager.reset_instance()


def _claim(
    database: DatabaseManager,
    *,
    run_id: str,
    conversation_id: str,
):
    return database.claim_agent_run(
        run_id=run_id,
        conversation_id=conversation_id,
        request_payload={"messages": []},
        worker_id="worker-a",
        tenant_id="tenant-a",
        owner_id="owner-a",
    )


def test_reviewed_action_mints_run_scoped_receipt(database):
    conversation_id = "governance-conversation"
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert _claim(
        database,
        run_id="review-run",
        conversation_id=conversation_id,
    )["claimed"]
    assert database.commit_agent_run_terminal(
        run_id="review-run",
        conversation_id=conversation_id,
        status="blocked",
        messages=[],
        final_text="等待确认",
        agent_context={},
        worker_id="worker-a",
    )
    assert _claim(
        database,
        run_id="approved-run",
        conversation_id=conversation_id,
    )["claimed"]

    fingerprint = "a" * 64
    authorization = database.prepare_agent_run_authorization(
        run_id="approved-run",
        conversation_id=conversation_id,
        source_run_id="review-run",
        reviewed_action_fingerprints=[fingerprint],
        task_descriptors=[
            {
                "task_id": "watchlist-change",
                "capability": "watchlist_mutation",
                "effect": "mutation",
                "confirmation": "explicit",
                "requires_approval": True,
                "action_fingerprint": fingerprint,
                "action_snapshot": {
                    "action": "add",
                    "symbols": ["600519"],
                },
            }
        ],
    )
    assert authorization["authorized_tasks"] == {
        "watchlist-change": True
    }
    assert authorization["approved_action_fingerprints"] == [
        fingerprint
    ]
    receipt_id = authorization["receipt_by_task"][
        "watchlist-change"
    ]
    assert database.authorize_agent_effect_step(
        run_id="approved-run",
        task_id="watchlist-change",
        step_id="add-stock",
        receipt_id=receipt_id,
    )
    # One reviewed workflow may contain multiple fixed steps, but the receipt
    # remains bound to the same run and task.
    assert database.authorize_agent_effect_step(
        run_id="approved-run",
        task_id="watchlist-change",
        step_id="save-group",
        receipt_id=receipt_id,
    )
    receipts = database.list_agent_approval_receipts(
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert receipts[0]["status"] == "used"
    assert receipts[0]["use_count"] == 2

    audit = list(
        reversed(
            database.list_agent_audit_events(
                tenant_id="tenant-a",
                owner_id="owner-a",
            )
        )
    )
    assert audit
    for previous, current in zip(audit, audit[1:]):
        assert current["previous_hash"] == previous["event_hash"]


def test_external_effect_is_default_denied_until_explicit_grant(database):
    conversation_id = "external-governance"
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert _claim(
        database,
        run_id="external-run",
        conversation_id=conversation_id,
    )["claimed"]
    descriptor = {
        "task_id": "notify",
        "capability": "notification",
        "effect": "external",
        "confirmation": "not_required",
        "requires_approval": False,
        "action_fingerprint": "b" * 64,
    }
    denied = database.prepare_agent_run_authorization(
        run_id="external-run",
        conversation_id=conversation_id,
        source_run_id=None,
        reviewed_action_fingerprints=[],
        task_descriptors=[descriptor],
    )
    assert denied["authorized_tasks"] == {"notify": False}

    database.upsert_agent_capability_grant(
        tenant_id="tenant-a",
        owner_id="owner-a",
        capability="notification",
        decision="allow",
        reason="允许使用内置通知能力",
    )
    allowed = database.prepare_agent_run_authorization(
        run_id="external-run",
        conversation_id=conversation_id,
        source_run_id=None,
        reviewed_action_fingerprints=[],
        task_descriptors=[descriptor],
    )
    assert allowed["authorized_tasks"] == {"notify": True}


def test_capability_release_requires_current_manifest_and_evaluation(
    database,
):
    conversation_id = "release-governance"
    database.create_chat_conversation(
        conversation_id,
        tenant_id="tenant-a",
        owner_id="owner-a",
    )
    assert _claim(
        database,
        run_id="release-run",
        conversation_id=conversation_id,
    )["claimed"]
    manifest = build_capability_release_manifest()
    baseline = database.prepare_agent_run_authorization(
        run_id="release-run",
        conversation_id=conversation_id,
        source_run_id=None,
        reviewed_action_fingerprints=[],
        registry_manifest=manifest,
        task_descriptors=[
            {
                "task_id": "lookup",
                "capability": "security_lookup",
                "effect": "read",
                "confirmation": "not_required",
                "requires_approval": False,
                "action_fingerprint": "c" * 64,
            }
        ],
    )
    assert baseline["release"]["authorized"] is True
    releases = database.list_agent_capability_releases()
    assert releases[0]["status"] == "active"

    draft = database.create_agent_capability_release(
        owner_id="owner-a",
        release_version="2026.07.31-test",
        manifest=manifest,
        evaluation_suite="release-suite",
        minimum_pass_rate=1.0,
    )
    case_id = uuid.uuid4().hex
    result_id = uuid.uuid4().hex
    with database.session_scope() as session:
        session.add(
            AgentEvaluationCase(
                id=case_id,
                tenant_id="tenant-a",
                owner_id="owner-a",
                suite="release-suite",
                name="release-case",
                status="active",
                version=1,
                request_snapshot_json="{}",
                evidence_snapshot_json="{}",
                expectations_json="{}",
                tags_json="[]",
            )
        )
        session.add(
            AgentEvaluationResult(
                id=result_id,
                case_id=case_id,
                evaluator_version="agent-quality-1.0",
                status="passed",
                total_score=1.0,
                scores_json="{}",
                violations_json="[]",
                snapshot_json=json.dumps(
                    {
                        "quality_projection": {
                            "capability_registry_fingerprint": (
                                "older-registry"
                            )
                        }
                    }
                ),
            )
        )
    with pytest.raises(
        ValueError,
        match="candidate capability registry",
    ):
        database.activate_agent_capability_release(
            release_id=draft["id"],
            tenant_id="tenant-a",
            owner_id="owner-a",
            runtime_fingerprint=manifest["registry_fingerprint"],
        )
    with database.session_scope() as session:
        result = session.get(AgentEvaluationResult, result_id)
        assert result is not None
        result.snapshot_json = json.dumps(
            {
                "quality_projection": {
                    "capability_registry_fingerprint": (
                        manifest["registry_fingerprint"]
                    )
                }
            }
        )
    activated = database.activate_agent_capability_release(
        release_id=draft["id"],
        tenant_id="tenant-a",
        owner_id="owner-a",
        runtime_fingerprint=manifest["registry_fingerprint"],
    )
    assert activated["status"] == "active"
    assert activated["evaluation_summary"]["pass_rate"] == 1.0

    changed_manifest = {
        **manifest,
        "registry_fingerprint": "d" * 64,
    }
    drifted = database.prepare_agent_run_authorization(
        run_id="release-run",
        conversation_id=conversation_id,
        source_run_id=None,
        reviewed_action_fingerprints=[],
        registry_manifest=changed_manifest,
        task_descriptors=[
            {
                "task_id": "lookup",
                "capability": "security_lookup",
                "effect": "read",
            },
            {
                "task_id": "mutate",
                "capability": "watchlist_mutation",
                "effect": "mutation",
            },
        ],
    )
    assert drifted["release"]["authorized"] is False
    assert drifted["authorized_tasks"] == {
        "lookup": True,
        "mutate": False,
    }


def test_user_memory_is_explicit_scoped_and_prompt_bounded(database):
    global_memory = database.upsert_agent_user_memory(
        tenant_id="tenant-a",
        owner_id="owner-a",
        memory_id=None,
        scope="global",
        conversation_id=None,
        kind="preference",
        memory_key="answer_language",
        content="默认使用简体中文",
        enabled=True,
    )
    conversation_memory = database.upsert_agent_user_memory(
        tenant_id="tenant-a",
        owner_id="owner-a",
        memory_id=None,
        scope="conversation",
        conversation_id="conversation-a",
        kind="instruction",
        memory_key="risk_style",
        content="风险部分优先列出证据不足项",
        enabled=True,
    )
    visible = database.list_agent_user_memories(
        tenant_id="tenant-a",
        owner_id="owner-a",
        conversation_id="conversation-a",
        enabled_only=True,
    )
    assert {item["id"] for item in visible} == {
        global_memory["id"],
        conversation_memory["id"],
    }
    message = build_explicit_memory_message(visible)
    assert message is not None
    assert "不得据此推断或新增未列出的记忆" in message["content"]
    assert "默认使用简体中文" in message["content"]

    assert database.delete_agent_user_memory(
        tenant_id="tenant-a",
        owner_id="owner-a",
        memory_id=conversation_memory["id"],
    )
    remaining = database.list_agent_user_memories(
        tenant_id="tenant-a",
        owner_id="owner-a",
        conversation_id="conversation-a",
    )
    assert [item["id"] for item in remaining] == [
        global_memory["id"]
    ]
