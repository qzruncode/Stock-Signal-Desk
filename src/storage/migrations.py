# -*- coding: utf-8 -*-
"""Database migration and schema compatibility logic."""

import json
import logging
from typing import Optional

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage.models import AgentRuntimeControl, Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2026.09.05.2"


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


def ensure_compatible_schema(engine, is_sqlite_engine: bool) -> None:
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
    if is_sqlite_engine:
        _migrate_legacy_kline_tables(engine)
        _migrate_financial_fields_rename(engine)
        _migrate_quant_screen_fields(engine)
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


def _migrate_quant_screen_fields(engine) -> None:
    """Add the financial fields required by deterministic TTM screening."""
    inspector = inspect(engine)
    if "stock_meta" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("stock_meta")}
    additions = {
        "revenue_ttm": "FLOAT",
        "parent_net_profit_ttm": "FLOAT",
        "deducted_net_profit_ttm": "FLOAT",
    }
    missing = [(name, sql_type) for name, sql_type in additions.items() if name not in columns]
    if not missing:
        return
    session = Session(bind=engine)
    try:
        for name, sql_type in missing:
            session.execute(text(f'ALTER TABLE stock_meta ADD COLUMN "{name}" {sql_type}'))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_financial_fields_rename(engine) -> None:
    """Rename stock_meta financial fields and drop deleted columns.

    SQLite 不支持 RENAME COLUMN（< 3.25.0）和 DROP COLUMN（< 3.35.0）时，
    用重建表的方式实现。版本够新时直接 ALTER。
    """
    inspector = inspect(engine)
    if "stock_meta" not in inspector.get_table_names():
        return
    columns = {c["name"]: c for c in inspector.get_columns("stock_meta")}

    rename_map = {
        "revenue_ttm": "revenue_latest",
        "net_profit_ttm": "net_profit_latest",
        "operating_cf_ttm": "operating_cf_latest",
    }
    drop_cols = [
        "area",
        "total_market_cap",
        "circulating_market_cap",
        "pe_ttm",
        "pb",
        "amount_today",
        "deducted_profit_ttm",
        "interest_bearing_debt_ratio",
        "cash_debt_ratio",
    ]
    # A current schema intentionally contains both revenue_ttm and
    # revenue_latest: they now have different meanings. Rename only a legacy
    # source column whose destination does not exist yet.
    effective_rename_map = {old: new for old, new in rename_map.items() if old in columns and new not in columns}
    needs_rename = bool(effective_rename_map)
    needs_drop = any(col in columns for col in drop_cols)
    if not needs_rename and not needs_drop:
        return

    logger.info("Migrating stock_meta financial fields (rename/drop)...")
    session = Session(bind=engine)
    try:
        # 用 _meta 标记一次性执行
        session.execute(text("CREATE TABLE IF NOT EXISTS _meta (" "  key   TEXT PRIMARY KEY," "  value TEXT" ")"))
        already = session.execute(text("SELECT value FROM _meta WHERE key = 'stock_meta_financial_v2'")).fetchone()
        if already:
            return
        session.commit()

        # 拿到当前完整列定义
        existing_ordered = [c["name"] for c in inspector.get_columns("stock_meta")]
        new_ordered = []
        for col in existing_ordered:
            if col in effective_rename_map:
                new_ordered.append(effective_rename_map[col])
            elif col in drop_cols:
                continue
            else:
                new_ordered.append(col)

        # 重建表：SELECT INTO new + RENAME
        session.execute(text("DROP TABLE IF EXISTS stock_meta_new"))
        # 复用最新模型定义（用 SQLAlchemy 反射得到 CREATE TABLE 语句）
        from sqlalchemy.schema import CreateTable
        from src.storage.models import StockMeta

        create_stmt = str(CreateTable(StockMeta.__table__).compile(engine))
        # CreateTable 会包含 IF NOT EXISTS 但我们要的是 stock_meta_new
        create_stmt = create_stmt.replace("stock_meta", "stock_meta_new", 1)
        session.execute(text(create_stmt))

        # INSERT ... SELECT 从旧表映射列
        new_cols_csv = ", ".join(f'"{c}"' for c in new_ordered)
        select_exprs = []
        reverse_rename_map = {new: old for old, new in effective_rename_map.items()}
        for new_col in new_ordered:
            if new_col in existing_ordered:
                select_exprs.append(f'"{new_col}"')
            elif new_col in reverse_rename_map and reverse_rename_map[new_col] in existing_ordered:
                old_col = reverse_rename_map[new_col]
                select_exprs.append(f'"{old_col}"')
            else:
                select_exprs.append("NULL")
        select_csv = ", ".join(select_exprs)
        session.execute(text(f"INSERT INTO stock_meta_new ({new_cols_csv}) SELECT {select_csv} FROM stock_meta"))
        # 重建索引（create_all 已在新表上加，这里再保险一次）
        for idx in inspector.get_indexes("stock_meta"):
            cols_csv = ", ".join(f'"{c}"' for c in idx["column_names"])
            unique = "UNIQUE " if idx.get("unique") else ""
            session.execute(text(f'CREATE {unique}INDEX IF NOT EXISTS "{idx["name"]}" ON stock_meta_new ({cols_csv})'))

        session.execute(text("DROP TABLE stock_meta"))
        session.execute(text("ALTER TABLE stock_meta_new RENAME TO stock_meta"))
        session.execute(text("INSERT OR REPLACE INTO _meta (key, value) VALUES ('stock_meta_financial_v2', '1')"))
        session.commit()
        logger.info("stock_meta financial fields migration complete.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _migrate_legacy_kline_tables(engine) -> None:
    """Handle legacy kline snapshot table schema changes.

    SQLite does not support ALTER COLUMN.  For kline_snapshot we use a
    schema-version row to detect the old schema once.
    """
    inspector = inspect(engine)
    if "kline_snapshot" not in inspector.get_table_names():
        return

    session: Optional[Session] = None
    try:
        session = Session(bind=engine)
        row = session.execute(text("SELECT value FROM _meta WHERE key = 'kline_snapshot_v2'")).fetchone()
        if row:
            return
    except Exception:
        pass
    finally:
        if session:
            session.close()

    # Check kline_snapshot column count
    columns = [c["name"] for c in inspector.get_columns("kline_snapshot")]
    if "code" in columns:
        return

    logger.info("Migrating legacy kline_snapshot table schema...")
    session = Session(bind=engine)
    try:
        session.execute(text("DROP TABLE IF EXISTS kline_snapshot_new"))
        session.execute(
            text(
                "CREATE TABLE kline_snapshot_new ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  code VARCHAR(16) NOT NULL UNIQUE,"
                "  data TEXT NOT NULL,"
                "  created_at DATETIME,"
                "  updated_at DATETIME"
                ")"
            )
        )
        session.execute(
            text(
                "INSERT INTO kline_snapshot_new (id, data, created_at, updated_at) "
                "SELECT id, data, created_at, updated_at FROM kline_snapshot"
            )
        )
        session.execute(text("DROP TABLE kline_snapshot"))
        session.execute(text("ALTER TABLE kline_snapshot_new RENAME TO kline_snapshot"))
        session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_kline_snapshot_code ON kline_snapshot(code)"))

        session.execute(text("CREATE TABLE IF NOT EXISTS _meta (" "  key   TEXT PRIMARY KEY," "  value TEXT" ")"))
        session.execute(text("INSERT OR REPLACE INTO _meta (key, value) VALUES ('kline_snapshot_v2', '1')"))
        session.commit()
        logger.info("Legacy kline_snapshot migration complete.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
