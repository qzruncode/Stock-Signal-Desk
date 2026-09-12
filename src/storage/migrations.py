# -*- coding: utf-8 -*-
"""Database migration and schema compatibility logic."""

import json
import logging
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage.models import AgentRuntimeControl, Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2026.09.06.1"


def get_schema_version(engine) -> str | None:
    """Return the applied schema contract without mutating the database."""
    inspector = inspect(engine)
    if "_meta" not in inspector.get_table_names():
        return None
    session = Session(bind=engine)
    try:
        row = session.execute(text("SELECT value FROM _meta WHERE key = 'schema_version'")).fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        session.close()

def assert_schema_compatible(engine) -> None:
    """Fail closed when an externally managed database is not migrated."""
    current = get_schema_version(engine)
    if current != SCHEMA_VERSION:
        raise RuntimeError(
            "database schema version mismatch: "
            f"expected {SCHEMA_VERSION}, found {current or 'unversioned'}; "
            "run `python scripts/manage_database.py migrate` before startup"
        )


def ensure_compatible_schema(engine) -> None:
    """Create missing tables and run ordered, recorded compatibility migrations.

    This remains intentionally lightweight for local SQLite installations.
    Production databases can run the same idempotent changes during rollout,
    and ``schema_version`` provides an explicit readiness/backup compatibility
    contract instead of guessing from whatever columns happen to exist.
    """
    Base.metadata.create_all(engine)
    _seed_agent_runtime_control(engine)
    _migrate_chat_agent_context_field(engine)
    _migrate_chat_ownership_fields(engine)
    _migrate_agent_run_budget_fields(engine)
    _migrate_agent_step_observability_fields(engine)
    _migrate_agent_run_trace_latest_stage(engine)
    _migrate_agent_quality_fields(engine)
    _record_schema_version(engine)


def _seed_agent_runtime_control(engine) -> None:
    """Create the durable mutex row used by global run admission."""
    session = Session(bind=engine)
    try:
        if session.get(AgentRuntimeControl, "run_admission") is None:
            session.add(AgentRuntimeControl(name="run_admission"))
        session.commit()
    except IntegrityError:
        session.rollback()
    finally:
        session.close()


def _ensure_meta_table(session: Session) -> None:
    session.execute(text("CREATE TABLE IF NOT EXISTS _meta (" "  key VARCHAR(128) PRIMARY KEY," "  value TEXT" ")"))


def _record_schema_version(engine) -> None:
    session = Session(bind=engine)
    try:
        _ensure_meta_table(session)
        existing = session.execute(text("SELECT value FROM _meta WHERE key = 'schema_version'")).fetchone()
        if existing is None:
            session.execute(
                text("INSERT INTO _meta (key, value) " "VALUES ('schema_version', :version)"),
                {"version": SCHEMA_VERSION},
            )
        else:
            session.execute(
                text("UPDATE _meta SET value = :version " "WHERE key = 'schema_version'"),
                {"version": SCHEMA_VERSION},
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_chat_ownership_fields(engine) -> None:
    """Add explicit tenant/owner boundaries to pre-production conversations."""
    inspector = inspect(engine)
    if "chat_conversations" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("chat_conversations")}
    additions = (
        ("tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'local'"),
        ("owner_id", "VARCHAR(128) NOT NULL DEFAULT 'admin'"),
    )
    session = Session(bind=engine)
    try:
        for name, sql_type in additions:
            if name not in columns:
                session.execute(text(f'ALTER TABLE chat_conversations ADD COLUMN "{name}" {sql_type}'))
        session.execute(
            text("UPDATE chat_conversations SET tenant_id = 'local' " "WHERE tenant_id IS NULL OR tenant_id = ''")
        )
        session.execute(
            text("UPDATE chat_conversations SET owner_id = 'admin' " "WHERE owner_id IS NULL OR owner_id = ''")
        )
        session.execute(
            text("CREATE INDEX IF NOT EXISTS ix_chat_conversations_tenant_id " "ON chat_conversations (tenant_id)")
        )
        session.execute(
            text("CREATE INDEX IF NOT EXISTS ix_chat_conversations_owner_id " "ON chat_conversations (owner_id)")
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_agent_run_trace_latest_stage(engine) -> None:
    """Persist the authoritative terminal Agent stage across reconnects."""
    inspector = inspect(engine)
    if "agent_run_traces" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("agent_run_traces")}
    session = Session(bind=engine)
    try:
        if "latest_stage_json" not in columns:
            session.execute(text("ALTER TABLE agent_run_traces " "ADD COLUMN latest_stage_json TEXT"))
        if "verification_json" not in columns:
            session.execute(text("ALTER TABLE agent_run_traces " "ADD COLUMN verification_json TEXT"))
        if "goal_state_json" not in columns:
            session.execute(text("ALTER TABLE agent_run_traces " "ADD COLUMN goal_state_json TEXT"))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_agent_quality_fields(engine) -> None:
    """Add the non-sensitive projection consumed by deterministic evaluators."""
    inspector = inspect(engine)
    if "agent_run_traces" not in inspector.get_table_names():
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("agent_run_traces")
    }
    if "quality_projection_json" in columns:
        return
    session = Session(bind=engine)
    try:
        session.execute(
            text(
                "ALTER TABLE agent_run_traces "
                "ADD COLUMN quality_projection_json TEXT "
                "NOT NULL DEFAULT '{}'"
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_agent_run_budget_fields(engine) -> None:
    """Add resource-accounting columns to an early durable-run deployment."""
    inspector = inspect(engine)
    if "agent_runs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    additions = (
        ("tool_call_count", "INTEGER NOT NULL DEFAULT 0"),
        ("provider_call_count", "INTEGER NOT NULL DEFAULT 0"),
        ("estimated_token_count", "INTEGER NOT NULL DEFAULT 0"),
        ("estimated_cost_micros", "INTEGER NOT NULL DEFAULT 0"),
        ("usage_json", "TEXT NOT NULL DEFAULT '{}'"),
    )
    session = Session(bind=engine)
    try:
        for name, sql_type in additions:
            if name not in columns:
                session.execute(text(f'ALTER TABLE agent_runs ADD COLUMN "{name}" {sql_type}'))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_agent_step_observability_fields(engine) -> None:
    inspector = inspect(engine)
    if "agent_step_executions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("agent_step_executions")}
    if "reuse_count" in columns:
        return
    session = Session(bind=engine)
    try:
        session.execute(text("ALTER TABLE agent_step_executions " "ADD COLUMN reuse_count INTEGER NOT NULL DEFAULT 0"))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_chat_agent_context_field(engine) -> None:
    """Move server-owned semantic state out of the UI presentation snapshot."""
    inspector = inspect(engine)
    if "chat_conversations" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("chat_conversations")}
    session = Session(bind=engine)
    try:
        if "agent_context_json" not in columns:
            session.execute(text("ALTER TABLE chat_conversations " "ADD COLUMN agent_context_json TEXT"))
            session.commit()

        rows = (
            session.execute(
                text(
                    "SELECT id, thread_state_json, agent_context_json "
                    "FROM chat_conversations "
                    "WHERE agent_context_json IS NULL AND thread_state_json IS NOT NULL"
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            try:
                thread_state = json.loads(row["thread_state_json"])
            except (TypeError, ValueError):
                continue
            context = thread_state.get("agent_context") if isinstance(thread_state, dict) else None
            if not isinstance(context, dict):
                continue
            session.execute(
                text(
                    "UPDATE chat_conversations "
                    "SET agent_context_json = :agent_context_json "
                    "WHERE id = :conversation_id"
                ),
                {
                    "conversation_id": row["id"],
                    "agent_context_json": json.dumps(
                        context,
                        ensure_ascii=False,
                    ),
                },
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
