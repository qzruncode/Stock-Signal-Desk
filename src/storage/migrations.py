# -*- coding: utf-8 -*-
"""Database migration and schema compatibility logic."""

import logging
from typing import Optional

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from src.storage.models import Base

logger = logging.getLogger(__name__)


def ensure_compatible_schema(engine, is_sqlite_engine: bool) -> None:
    """Create missing tables and run any lightweight migrations."""
    Base.metadata.create_all(engine)
    if is_sqlite_engine:
        _migrate_legacy_kline_tables(engine)


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
        row = session.execute(
            text("SELECT value FROM _meta WHERE key = 'kline_snapshot_v2'")
        ).fetchone()
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
        session.execute(text(
            "CREATE TABLE kline_snapshot_new ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  code VARCHAR(16) NOT NULL UNIQUE,"
            "  data TEXT NOT NULL,"
            "  created_at DATETIME,"
            "  updated_at DATETIME"
            ")"
        ))
        session.execute(text(
            "INSERT INTO kline_snapshot_new (id, data, created_at, updated_at) "
            "SELECT id, data, created_at, updated_at FROM kline_snapshot"
        ))
        session.execute(text("DROP TABLE kline_snapshot"))
        session.execute(text("ALTER TABLE kline_snapshot_new RENAME TO kline_snapshot"))
        session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_kline_snapshot_code ON kline_snapshot(code)"
        ))

        session.execute(text(
            "CREATE TABLE IF NOT EXISTS _meta ("
            "  key   TEXT PRIMARY KEY,"
            "  value TEXT"
            ")"
        ))
        session.execute(text(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('kline_snapshot_v2', '1')"
        ))
        session.commit()
        logger.info("Legacy kline_snapshot migration complete.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()