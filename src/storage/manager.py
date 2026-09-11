# -*- coding: utf-8 -*-
"""DatabaseManager singleton — core connection management and initialization."""

import atexit
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Optional, Any, Callable, TypeVar, Dict

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker, Session

from src.config import get_config
from src.storage.models import Base
from src.storage.mixins.agent_usage import AgentUsageMixin
from src.storage.migrations import (
    assert_schema_compatible,
    ensure_compatible_schema,
)
from src.storage.mixins import (
    NewsMixin,
    MacroMixin,
    AnalysisMixin,
    ChatMixin,
    BatchMixin,
    WatchlistMixin,
    AgentPromptMixin,
    AgentArtifactMixin,
    AgentRunTraceMixin,
    AgentRuntimeMixin,
    AgentQualityMixin,
    FinancialLifecycleMixin,
    TextDocumentMixin,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class DatabaseManager(
    AgentUsageMixin,
    NewsMixin,
    MacroMixin,
    AnalysisMixin,
    ChatMixin,
    BatchMixin,
    WatchlistMixin,
    AgentPromptMixin,
    AgentArtifactMixin,
    AgentRunTraceMixin,
    AgentRuntimeMixin,
    AgentQualityMixin,
    FinancialLifecycleMixin,
    TextDocumentMixin,
):
    """
    数据库管理器 - 单例模式

    职责：
    1. 管理数据库连接池
    2. 提供 Session 上下文管理
    3. 封装数据存取操作
    """

    _instance: Optional["DatabaseManager"] = None
    _instance_lock = threading.Lock()
    _initialized: bool = False

    # -- Static helpers accessible via self in mixins --

    @staticmethod
    def _is_sqlite_locked_error(exc: OperationalError) -> bool:
        err_text = str(getattr(exc, "orig", exc)).lower()
        return any(
            token in err_text
            for token in (
                "database is locked",
                "locking protocol",
                "locked",
            )
        )

    @staticmethod
    def _normalize_daily_date(value: Any) -> Any:
        from datetime import date, datetime

        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value is None:
            return None

        # AKShare does not use one stable date type across endpoints: depending
        # on the upstream it may return pandas.Timestamp, YYYY-MM-DD,
        # YYYYMMDD, slash-separated dates, or a full ISO timestamp.  SQLAlchemy
        # Date columns accept only ``date`` objects, so normalize all supported
        # variants at the storage boundary instead of requiring every caller to
        # know the database driver's rules.
        to_pydatetime = getattr(value, "to_pydatetime", None)
        if callable(to_pydatetime):
            try:
                converted = to_pydatetime()
                if isinstance(converted, datetime):
                    return converted.date()
                if isinstance(converted, date):
                    return converted
            except (TypeError, ValueError, OverflowError):
                pass

        text = str(value).strip()
        if not text or text.lower() in {"none", "nan", "nat"}:
            return None
        normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
        normalized = normalized.replace("/", "-").strip("-")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            pass
        for fmt in ("%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(normalized, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _normalize_sql_value(value: Any) -> Any:
        from datetime import date, datetime

        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return value

    @classmethod
    def _safe_json_dumps(cls, data: Any) -> str:
        import json

        try:
            return json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps({"__error": "failed to serialize"})

    @classmethod
    def _parse_published_date(cls, value: Optional[str]) -> Optional[Any]:
        from datetime import datetime

        if not value:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value, fmt)
            except (ValueError, TypeError):
                continue
        return None

    @classmethod
    def _build_raw_result(cls, result: Any) -> Dict[str, Any]:
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if hasattr(result, "dict"):
            return result.dict()
        if isinstance(result, dict):
            return result
        return {"result": str(result)}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_url: Optional[str] = None):
        if getattr(self, "_initialized", False):
            return

        config = get_config()
        if db_url is None:
            db_url = config.get_db_url()

        self._db_url = db_url
        self._sqlite_wal_enabled = config.sqlite_wal_enabled
        self._sqlite_busy_timeout_ms = config.sqlite_busy_timeout_ms
        self._sqlite_write_retry_max = config.sqlite_write_retry_max
        self._sqlite_write_retry_base_delay = config.sqlite_write_retry_base_delay

        engine_kwargs = {
            "echo": False,
            "pool_pre_ping": True,
        }
        backend_name = make_url(db_url).get_backend_name()
        if backend_name == "sqlite" and self._sqlite_busy_timeout_ms > 0:
            engine_kwargs["connect_args"] = {
                "timeout": self._sqlite_busy_timeout_ms / 1000,
            }
        elif backend_name != "sqlite":

            def _pool_int(name: str, default: int, minimum: int = 0) -> int:
                try:
                    return max(
                        minimum,
                        int((os.getenv(name) or str(default)).strip()),
                    )
                except (TypeError, ValueError):
                    return default

            engine_kwargs.update(
                {
                    "pool_size": _pool_int("DATABASE_POOL_SIZE", 10, 1),
                    "max_overflow": _pool_int(
                        "DATABASE_MAX_OVERFLOW",
                        10,
                    ),
                    "pool_timeout": _pool_int(
                        "DATABASE_POOL_TIMEOUT_SECONDS",
                        30,
                        1,
                    ),
                    "pool_recycle": _pool_int(
                        "DATABASE_POOL_RECYCLE_SECONDS",
                        1800,
                        30,
                    ),
                }
            )
            if backend_name.startswith("postgresql"):
                statement_timeout_ms = _pool_int(
                    "DATABASE_STATEMENT_TIMEOUT_MS",
                    30_000,
                    1_000,
                )
                lock_timeout_ms = _pool_int(
                    "DATABASE_LOCK_TIMEOUT_MS",
                    5_000,
                    100,
                )
                engine_kwargs["connect_args"] = {
                    "options": (
                        f"-c statement_timeout={statement_timeout_ms} "
                        f"-c lock_timeout={lock_timeout_ms}"
                    ),
                }

        self._engine = create_engine(db_url, **engine_kwargs)
        self._is_sqlite_engine = self._engine.url.get_backend_name() == "sqlite"
        self._sqlite_file_db = (
            self._is_sqlite_engine and self._is_file_sqlite_database()
        )
        self._install_sqlite_pragma_handler()

        environment = (
            str(os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
        )
        production = environment in {"prod", "production"} or str(
            os.getenv("DSA_PRODUCTION") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        auto_migrate_raw = str(os.getenv("DATABASE_AUTO_MIGRATE") or "").strip().lower()
        auto_migrate = (
            auto_migrate_raw in {"1", "true", "yes", "on"}
            if auto_migrate_raw
            else not production
        )
        if auto_migrate:
            ensure_compatible_schema(self._engine)
        else:
            # Production rollout owns migration ordering. Application workers
            # only verify the schema, so multiple replicas never race DDL.
            assert_schema_compatible(self._engine)

        self._SessionLocal = sessionmaker(bind=self._engine, expire_on_commit=False)
        self._initialized = True

        atexit.register(self._cleanup_engine, self._engine)
        logger.info(
            "DatabaseManager 初始化成功（%s）",
            "SQLite 文件数据库"
            if (self._is_sqlite_engine and self._sqlite_file_db)
            else db_url,
        )

    @classmethod
    def get_instance(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        if cls._instance is not None:
            engine = getattr(cls._instance, "_engine", None)
            if engine:
                cls._cleanup_engine(engine)
        cls._instance = None

    @classmethod
    def _cleanup_engine(cls, engine) -> None:
        try:
            engine.dispose()
        except Exception:
            pass

    def _install_sqlite_pragma_handler(self) -> None:
        if not (self._is_sqlite_engine and self._sqlite_wal_enabled):
            return

        @event.listens_for(self._engine, "connect")
        def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={self._sqlite_busy_timeout_ms}")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA cache_size=-4000")
            cursor.close()

    def _is_file_sqlite_database(self) -> bool:
        from pathlib import Path

        db_identity = str(self._engine.url)
        if db_identity.startswith("sqlite:///"):
            db_path = db_identity[len("sqlite:///") :]
            return Path(db_path).exists()
        return False

    def _run_write_transaction(
        self,
        operation_name: str,
        write_operation: Callable[[Session], T],
    ) -> T:
        max_retries = self._sqlite_write_retry_max if self._is_sqlite_engine else 0

        for attempt in range(max_retries + 1):
            session = self.get_session()
            try:
                if self._is_sqlite_engine:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                result = write_operation(session)
                session.commit()
                return result
            except OperationalError as exc:
                session.rollback()
                if (
                    self._is_sqlite_engine
                    and self._is_sqlite_locked_error(exc)
                    and attempt < max_retries
                ):
                    delay = self._sqlite_write_retry_base_delay * (2**attempt)
                    logger.warning(
                        "SQLite 写入锁冲突，准备重试: %s (%s/%s, %.2fs)",
                        operation_name,
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    if delay > 0:
                        time.sleep(delay)
                    continue
                raise
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def get_session(self) -> Session:
        if not getattr(self, "_initialized", False) or not hasattr(
            self, "_SessionLocal"
        ):
            raise RuntimeError(
                "DatabaseManager 未正确初始化。"
                "请确保通过 DatabaseManager.get_instance() 获取实例。"
            )
        session = self._SessionLocal()
        try:
            return session
        except Exception:
            session.close()
            raise

    @contextmanager
    def session_scope(self):
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
