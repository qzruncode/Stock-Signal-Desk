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
        _migrate_financial_fields_rename(engine)


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
    needs_rename = any(old in columns for old in rename_map)
    needs_drop = any(col in columns for col in drop_cols)
    if not needs_rename and not needs_drop:
        return

    logger.info("Migrating stock_meta financial fields (rename/drop)...")
    session = Session(bind=engine)
    try:
        # 用 _meta 标记一次性执行
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS _meta ("
            "  key   TEXT PRIMARY KEY,"
            "  value TEXT"
            ")"
        ))
        already = session.execute(
            text("SELECT value FROM _meta WHERE key = 'stock_meta_financial_v2'")
        ).fetchone()
        if already:
            return
        session.commit()

        # 拿到当前完整列定义
        existing_ordered = [c["name"] for c in inspector.get_columns("stock_meta")]
        new_ordered = []
        for col in existing_ordered:
            if col in rename_map:
                new_ordered.append(rename_map[col])
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
        reverse_rename_map = {new: old for old, new in rename_map.items()}
        for new_col in new_ordered:
            if new_col in existing_ordered:
                select_exprs.append(f'"{new_col}"')
            elif new_col in reverse_rename_map and reverse_rename_map[new_col] in existing_ordered:
                old_col = reverse_rename_map[new_col]
                select_exprs.append(f'"{old_col}"')
            else:
                select_exprs.append("NULL")
        select_csv = ", ".join(select_exprs)
        session.execute(text(
            f'INSERT INTO stock_meta_new ({new_cols_csv}) SELECT {select_csv} FROM stock_meta'
        ))
        # 重建索引（create_all 已在新表上加，这里再保险一次）
        for idx in inspector.get_indexes("stock_meta"):
            cols_csv = ", ".join(f'"{c}"' for c in idx["column_names"])
            unique = "UNIQUE " if idx.get("unique") else ""
            session.execute(text(
                f'CREATE {unique}INDEX IF NOT EXISTS "{idx["name"]}" ON stock_meta_new ({cols_csv})'
            ))

        session.execute(text("DROP TABLE stock_meta"))
        session.execute(text("ALTER TABLE stock_meta_new RENAME TO stock_meta"))
        session.execute(text(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('stock_meta_financial_v2', '1')"
        ))
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
