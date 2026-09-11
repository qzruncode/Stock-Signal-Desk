"""Data-only repository. All writes use native SQLAlchemy transactions/upserts."""

from contextlib import contextmanager
from datetime import date
from functools import lru_cache
import json
from typing import Any

from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from market_data_service.models import (
    ToolCache,
    KlineSnapshot,
    QuoteSnapshot,
    RssCache,
    MacroIndexDaily,
    BondYieldDaily,
    MacroIndicator,
)
from market_data_service.control_models import DatasetPolicy, ServiceHeartbeat, utcnow
from market_data_service.schemas import DEFAULT_POLICIES
from market_data_service.settings import get_settings


class Database:
    def __init__(self, url: str):
        options: dict[str, Any] = {"pool_pre_ping": True, "hide_parameters": True}
        if url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        self._engine = self.engine
        self._is_sqlite_engine = self.engine.dialect.name == "sqlite"
        if self._is_sqlite_engine:

            @event.listens_for(self.engine, "connect")
            def pragmas(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        from market_data_service.migration import upgrade

        upgrade(self.engine)
        with self.session_scope() as session:
            for dataset, (interval, age) in DEFAULT_POLICIES.items():
                self.upsert(
                    session,
                    DatasetPolicy,
                    [
                        {
                            "dataset": dataset,
                            "enabled": True,
                            "interval_seconds": interval,
                            "max_age_seconds": age,
                            "next_run_at": utcnow(),
                            "updated_at": utcnow(),
                        }
                    ],
                    ["dataset"],
                    update=False,
                )

    def get_session(self):
        return self.sessions()

    @contextmanager
    def session_scope(self):
        with self.sessions.begin() as session:
            yield session

    def _run_write_transaction(self, _name, fn):
        with self.session_scope() as session:
            return fn(session)

    @staticmethod
    def upsert(session, model, records, keys, *, update=True):
        if not records:
            return
        dialect = session.get_bind().dialect.name
        insert = sqlite_insert if dialect == "sqlite" else pg_insert
        for start in range(0, len(records), 200):
            batch = records[start : start + 200]
            statement = insert(model).values(batch)
            if update:
                fields = (
                    set.intersection(*(set(item) for item in batch))
                    - set(keys)
                    - {"id", "created_at"}
                )
                statement = statement.on_conflict_do_update(
                    index_elements=keys,
                    set_={
                        field: getattr(statement.excluded, field) for field in fields
                    },
                )
            else:
                statement = statement.on_conflict_do_nothing(index_elements=keys)
            session.execute(statement)

    def heartbeat(self, component, detail=""):
        with self.session_scope() as session:
            self.upsert(
                session,
                ServiceHeartbeat,
                [{"component": component, "updated_at": utcnow(), "detail": detail}],
                ["component"],
            )

    def get_tool_cache(self, key):
        with self.get_session() as session:
            row = session.scalar(select(ToolCache).where(ToolCache.cache_key == key))
            return (
                {"payload": row.payload, "updated_at": row.updated_at} if row else None
            )

    def save_tool_cache(self, key, payload):
        with self.session_scope() as session:
            self.upsert(
                session,
                ToolCache,
                [{"cache_key": key, "payload": payload, "updated_at": utcnow()}],
                ["cache_key"],
            )

    def _get_json_cache(self, model, field, key):
        with self.get_session() as session:
            row = session.scalar(select(model).where(getattr(model, field) == key))
            if not row:
                return None
            payload = json.loads(row.data)
            payload["_fetched_at"] = (
                row.updated_at.isoformat() if row.updated_at else None
            )
            return payload

    def _save_json_cache(self, model, field, key, data):
        with self.session_scope() as session:
            self.upsert(
                session,
                model,
                [{field: key, "data": data, "updated_at": utcnow()}],
                [field],
            )

    def get_kline_snapshot(self, key):
        return self._get_json_cache(KlineSnapshot, "code", key)

    def save_kline_snapshot(self, key, data):
        self._save_json_cache(KlineSnapshot, "code", key, data)

    def get_rss_cache(self, key):
        with self.get_session() as session:
            row = session.scalar(select(RssCache).where(RssCache.cache_key == key))
            return json.loads(row.data) if row else None

    def save_rss_cache(self, key, data):
        self._save_json_cache(RssCache, "cache_key", key, data)

    def get_quote_snapshots(self, codes, since=None):
        with self.get_session() as session:
            query = select(QuoteSnapshot).where(QuoteSnapshot.code.in_(codes))
            if since:
                query = query.where(QuoteSnapshot.updated_at >= since)
            result = {}
            for row in session.scalars(query):
                result[row.code] = {
                    **json.loads(row.data),
                    "_fetched_at": row.updated_at.isoformat(),
                }
            return result

    def save_quote_snapshot(self, code, data):
        self._save_json_cache(QuoteSnapshot, "code", code, data)

    def save_macro_index_daily(self, index_code, records, data_source="新浪"):
        fields = {
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "pct_chg",
            "change_amount",
        }
        rows = [
            {
                "index_code": index_code,
                "date": date.fromisoformat(str(row["date"])[:10]),
                **{key: row.get(key) for key in fields},
                "data_source": data_source,
                "updated_at": utcnow(),
            }
            for row in records
            if row.get("date")
        ]
        with self.session_scope() as session:
            self.upsert(session, MacroIndexDaily, rows, ["index_code", "date"])
        return len(rows)

    def get_macro_index_daily(self, index_code, limit=50):
        with self.get_session() as session:
            rows = session.scalars(
                select(MacroIndexDaily)
                .where(MacroIndexDaily.index_code == index_code)
                .order_by(MacroIndexDaily.date.desc())
                .limit(limit)
            )
            return [
                {
                    "date": row.date.isoformat(),
                    **{
                        key: getattr(row, key)
                        for key in (
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                            "amount",
                            "pct_chg",
                            "change_amount",
                        )
                    },
                }
                for row in rows
            ] or None

    def get_trading_days(self, start_date, end_date):
        from market_data_service.calendar import trade_dates

        return {day for day in trade_dates() if start_date <= day <= end_date}

    def save_bond_yield_daily(self, country, term, records):
        rows = [
            {
                "country": country,
                "term": term,
                "date": date.fromisoformat(str(row["date"])[:10]),
                "yield_value": row["value"],
                "updated_at": utcnow(),
            }
            for row in records
            if row.get("date") and row.get("value") is not None
        ]
        with self.session_scope() as session:
            self.upsert(session, BondYieldDaily, rows, ["country", "term", "date"])
        return len(rows)

    def get_bond_yield_daily(self, country, term, limit=30):
        with self.get_session() as session:
            rows = session.scalars(
                select(BondYieldDaily)
                .where(BondYieldDaily.country == country, BondYieldDaily.term == term)
                .order_by(BondYieldDaily.date.desc())
                .limit(limit)
            )
            return [
                {"date": row.date.isoformat(), "value": row.yield_value} for row in rows
            ] or None

    def save_macro_indicator(self, indicator, records):
        rows = [
            {
                "indicator": indicator,
                "period": str(row["period"]),
                "value": row.get("value"),
                "yoy": row.get("yoy"),
                "mom": row.get("mom"),
                "extra_json": json.dumps(row.get("extra"), ensure_ascii=False),
                "updated_at": utcnow(),
            }
            for row in records
            if row.get("period")
        ]
        with self.session_scope() as session:
            self.upsert(session, MacroIndicator, rows, ["indicator", "period"])
        return len(rows)

    def get_macro_indicator(self, indicator, limit=120):
        with self.get_session() as session:
            rows = session.scalars(
                select(MacroIndicator)
                .where(MacroIndicator.indicator == indicator)
                .order_by(MacroIndicator.period.desc())
                .limit(limit)
            )
            return [
                {
                    "period": row.period,
                    "value": row.value,
                    "yoy": row.yoy,
                    "mom": row.mom,
                    "extra": json.loads(row.extra_json) if row.extra_json else None,
                }
                for row in rows
            ] or None

    @classmethod
    def get_instance(cls):
        return get_database()


@lru_cache
def get_database() -> Database:
    return Database(get_settings().database_url)


DatabaseManager = Database
