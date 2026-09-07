"""Operational CLI: initialize, read-only legacy import, and service status."""

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, func, inspect, select

from market_data_service.control_models import DataState, ImportIssue
from market_data_service.database import get_database
from market_data_service.models import StockDaily, StockMeta


def import_legacy(path: str):
    """Copy market tables only. Open SQLite in read-only mode; never unpickle caches.

    This operation is restartable via natural-key upserts and is only allowed
    before acquisition starts, so a historical import cannot overwrite live data.
    """
    from market_data_service.control_models import SyncJob, Observation

    database = get_database()
    source_path = Path(path).resolve(strict=True)
    if source_path.suffix not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("仅支持明确指定的旧 SQLite 数据库文件")
    with database.get_session() as session:
        if session.scalar(select(Observation.id).limit(1)) or session.scalar(
            select(SyncJob.id).limit(1)
        ):
            raise ValueError(
                "目标数据库已开始采集，禁止旧库覆盖；请在首次启动采集前导入"
            )
    source = create_engine(f"sqlite:///file:{source_path}?mode=ro&uri=true")
    source_tables = set(inspect(source).get_table_names())
    total = {}
    try:
        for model, keys in ((StockMeta, ["code"]), (StockDaily, ["code", "date"])):
            if model.__tablename__ not in source_tables:
                continue
            table = Table(model.__tablename__, MetaData(), autoload_with=source)
            names = [
                name
                for name in model.__table__.columns.keys()
                if name in table.c and name != "id"
            ]
            count = 0
            with source.connect() as connection:
                rows = (
                    connection.execution_options(stream_results=True)
                    .execute(select(*(table.c[name] for name in names)))
                    .mappings()
                )
                for batch in rows.partitions(1000):
                    normalized = []
                    issues = []
                    for row in batch:
                        values = dict(row)
                        for column in model.__table__.columns:
                            value = values.get(column.name)
                            if value is None:
                                continue
                            if column.type.python_type is float:
                                from market_data_service.providers.financial_sync import (
                                    _safe_float,
                                )

                                values[column.name] = _safe_float(value)
                                if values[column.name] is None:
                                    issues.append(
                                        {
                                            "table_name": model.__tablename__,
                                            "symbol": str(values.get("code", "")),
                                            "field": column.name,
                                            "raw_value": str(value)[:500],
                                            "reason": "旧库数值字段不是有效数字，已隔离为缺失并等待重新采集",
                                        }
                                    )
                            elif column.type.python_type is datetime:
                                parsed = (
                                    datetime.fromisoformat(str(value))
                                    if not isinstance(value, datetime)
                                    else value
                                )
                                values[column.name] = parsed - timedelta(
                                    hours=8
                                )  # old app stored Shanghai wall clock
                            elif column.type.python_type is date:
                                values[column.name] = date.fromisoformat(
                                    str(value)[:10]
                                )
                        if model is StockDaily:
                            # Preserve old provider units explicitly for the source
                            # migration normalizer, rather than treating them as shares.
                            from market_data_service.providers.legacy_kline_units import (
                                normalize_legacy_bar,
                            )

                            values = normalize_legacy_bar(values)
                        normalized.append(values)
                    with database.session_scope() as session:
                        database.upsert(session, model, normalized, keys)
                        database.upsert(
                            session,
                            ImportIssue,
                            issues,
                            ["table_name", "symbol", "field"],
                        )
                    count += len(batch)
                    if count % 10000 == 0:
                        print(f"{model.__tablename__}: {count}", flush=True)
            total[model.__tablename__] = count
            print(f"{model.__tablename__}: imported {count}", flush=True)
        with database.session_scope() as session:
            metas = list(
                session.scalars(select(StockMeta).where(StockMeta.status == "active"))
            )
            last_sync = min(
                (row.last_sync_at for row in metas if row.last_sync_at), default=None
            )
            if last_sync:
                database.upsert(
                    session,
                    DataState,
                    [
                        {
                            "dataset": "securities",
                            "symbol": "all",
                            "status": "ready",
                            "last_success_at": last_sync,
                            "checked_at": last_sync,
                            "source": "legacy-import",
                            "data_time": None,
                        }
                    ],
                    ["dataset", "symbol"],
                )
            for row in metas:
                if row.financial_fetched_at:
                    complete = all(
                        getattr(row, field) is not None
                        for field in (
                            "revenue_ttm",
                            "parent_net_profit_ttm",
                            "deducted_net_profit_ttm",
                            "debt_ratio",
                        )
                    )
                    database.upsert(
                        session,
                        DataState,
                        [
                            {
                                "dataset": "financials",
                                "symbol": row.code,
                                "status": "unknown" if complete else "partial",
                                "data_time": row.report_date,
                                "last_success_at": row.financial_fetched_at,
                                "checked_at": row.financial_fetched_at,
                                "source": "legacy-import",
                            }
                        ],
                        ["dataset", "symbol"],
                    )
            for code, day, fetched in session.execute(
                select(
                    StockDaily.code,
                    func.max(StockDaily.date),
                    func.max(StockDaily.updated_at),
                ).group_by(StockDaily.code)
            ):
                database.upsert(
                    session,
                    DataState,
                    [
                        {
                            "dataset": "kline",
                            "symbol": code,
                            "status": "unknown",
                            "data_time": day.isoformat(),
                            "last_success_at": fetched,
                            "checked_at": fetched,
                            "source": "legacy-import",
                        }
                    ],
                    ["dataset", "symbol"],
                )
        print(
            {
                "imported": total,
                "source_unchanged": str(source_path),
                "freshness": "original timestamps retained; no certification as fresh",
            },
            flush=True,
        )
    finally:
        source.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    migrate = sub.add_parser("import-legacy")
    migrate.add_argument("path")
    sub.add_parser("health")
    args = parser.parse_args()
    if args.command == "health":
        import httpx
        from market_data_service.settings import get_settings

        settings = get_settings()
        response = httpx.get(
            f"http://127.0.0.1:{settings.port}/v1/health",
            timeout=5,
            trust_env=False,
            headers={"Authorization": f"Bearer {settings.api_token}"}
            if settings.api_token
            else {},
        )
        response.raise_for_status()
        print(response.json())
        return
    database = get_database()
    database.initialize()
    if args.command == "import-legacy":
        import_legacy(args.path)
    else:
        print("Independent data schema initialized")


if __name__ == "__main__":
    main()
