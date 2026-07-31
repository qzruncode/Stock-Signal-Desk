# -*- coding: utf-8 -*-
"""Database engine creation, session factory configuration, WAL mode setup."""

import logging
import threading
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from src.config import get_config

logger = logging.getLogger(__name__)


def create_db_engine(db_url: Optional[str] = None):
    """Create a SQLAlchemy engine from config or explicit URL."""
    config = get_config()
    if db_url is None:
        db_url = config.get_db_url()

    engine_kwargs = {
        "echo": False,
        "pool_pre_ping": True,
    }
    if str(db_url).startswith("sqlite:") and config.sqlite_busy_timeout_ms > 0:
        engine_kwargs["connect_args"] = {
            "timeout": config.sqlite_busy_timeout_ms / 1000,
        }

    engine = create_engine(db_url, **engine_kwargs)
    return engine, db_url


def configure_sqlite_pragma(engine, sqlite_wal_enabled: bool = True):
    """Install SQLite pragma handler on the engine."""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA cache_size=-4000")
        cursor.close()


def create_session_factory(engine):
    """Create a scoped session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)


_lock = threading.Lock()
_engine = None
_SessionLocal = None
_db_url = None


def get_or_create_engine(db_url_arg: Optional[str] = None):
    """Get or create the database engine (singleton helper)."""
    global _engine, _SessionLocal, _db_url
    if _engine is not None:
        return _engine, _SessionLocal

    with _lock:
        if _engine is not None:
            return _engine, _SessionLocal
        from src.config import get_config

        _engine, _db_url = create_db_engine(db_url_arg)
        config = get_config()
        if config.sqlite_wal_enabled and str(_engine.url).startswith("sqlite:"):
            configure_sqlite_pragma(_engine, config.sqlite_wal_enabled)
        _SessionLocal = create_session_factory(_engine)
        return _engine, _SessionLocal
