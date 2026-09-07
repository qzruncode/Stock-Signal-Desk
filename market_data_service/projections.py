"""Incremental, service-owned read models. HTTP readers never rebuild them.

The relay consumes committed changes once, regardless of browser count. Time
transitions are indexed deadlines, not a rescan triggered by each subscriber.
"""

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select

from market_data_service.control_models import (
    CoverageProjection as Coverage,
)
from market_data_service.control_models import (
    DatasetPolicy,
    DatasetProjection,
    DataState,
    Observation,
    SourceSubscription,
    SyncJob,
    utcnow,
)
from market_data_service.freshness import iso, state_status
from market_data_service.models import StockMeta
from market_data_service.schemas import DATASET_LABELS, DATASETS

SUBSCRIBED = {"market", "macro", "rss"}
SYMBOL_SUBSCRIPTIONS = {"quotes", "news", "announcements"}
STATUSES = ("fresh", "stale", "missing", "failed", "partial", "unknown")


def next_transition(
    dataset, now, *, fetched_at=None, max_age=0, data_time=None, expires=None
):
    """Schedule only boundaries that can change the current freshness verdict."""
    candidates = [expires] if expires else []
    if fetched_at and dataset != "quotes":
        candidates.append(fetched_at + timedelta(seconds=max_age, microseconds=1))
    local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Shanghai"))
    if dataset in {"quotes", "kline"}:
        from market_data_service.calendar import trade_dates

        try:
            trading = trade_dates()
        except RuntimeError:
            trading = ()  # A calendar change will wake these unknown records.
        for day in trading[bisect_left(trading, local.date()) :]:
            boundaries = (
                ((9, 15), (11, 30), (13, 0), (15, 5))
                if dataset == "quotes"
                else ((15, 15),)
            )
            for hour, minute in boundaries:
                point = datetime.combine(day, time(hour, minute), local.tzinfo)
                if point >= local:
                    candidates.append(
                        point.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
                        + timedelta(microseconds=1)
                    )
            if any(point and point > now for point in candidates):
                break
        if dataset == "quotes" and data_time:
            try:
                stamp = datetime.fromisoformat(data_time.replace("Z", "+00:00"))
                stamp = (
                    (stamp if stamp.tzinfo else stamp.replace(tzinfo=local.tzinfo))
                    .astimezone(ZoneInfo("UTC"))
                    .replace(tzinfo=None)
                )
                candidates.extend(
                    (
                        stamp - timedelta(seconds=60),
                        stamp + timedelta(seconds=max_age, microseconds=1),
                    )
                )
            except ValueError:
                pass
    if dataset == "financials":
        candidates.append(
            datetime.combine(local.date() + timedelta(days=1), time(), local.tzinfo)
            .astimezone(ZoneInfo("UTC"))
            .replace(tzinfo=None)
        )
    return min((point for point in candidates if point and point > now), default=None)


def _coverage_records(session, dataset, entities, now):
    policy = session.get(DatasetPolicy, dataset)
    if dataset in SUBSCRIBED:
        from market_data_service.sources import observation_payload

        query = select(SourceSubscription).where(
            SourceSubscription.dataset == dataset,
            SourceSubscription.last_requested_at > now - timedelta(days=7),
        )
        if entities is not None:
            query = query.where(SourceSubscription.request_key.in_(entities))
        subscriptions = list(session.scalars(query))
        keys = [row.request_key for row in subscriptions]
        if not keys:
            return []
        latest = (
            select(
                Observation.id,
                func.row_number()
                .over(
                    partition_by=Observation.request_key,
                    order_by=(Observation.fetched_at.desc(), Observation.id.desc()),
                )
                .label("rank"),
            )
            .where(Observation.request_key.in_(keys))
            .subquery()
        )
        observations = {
            row.request_key: row
            for row in session.scalars(
                select(Observation)
                .join(latest, latest.c.id == Observation.id)
                .where(latest.c.rank == 1)
            )
        }
        result = []
        for item in subscriptions:
            observation = observations.get(item.request_key)
            payload = (
                observation_payload(
                    observation, allow_stale=True, policy=policy, now=now
                )
                if observation
                else None
            )
            result.append(
                {
                    "dataset": dataset,
                    "entity": item.request_key,
                    "symbol": str(
                        item.arguments.get("symbol")
                        or item.arguments.get("index_code")
                        or item.arguments.get("indicator")
                        or item.request_key[:12]
                    ),
                    "name": item.operation,
                    "status": payload["data_service"]["status"]
                    if payload
                    else "failed"
                    if item.error
                    else "missing",
                    "data_time": observation.data_time if observation else None,
                    "checked_at": observation.fetched_at if observation else None,
                    "last_success_at": observation.fetched_at if observation else None,
                    "source": observation.source if observation else None,
                    "version": observation.id if observation else None,
                    "error": item.error,
                    "next_check_at": next_transition(
                        dataset,
                        now,
                        fetched_at=observation.fetched_at if observation else None,
                        max_age=policy.max_age_seconds,
                        expires=item.last_requested_at + timedelta(days=7),
                    ),
                }
            )
        return result
    state_query = select(DataState).where(DataState.dataset == dataset)
    if entities is not None:
        state_query = state_query.where(DataState.symbol.in_(entities))
    states = {row.symbol: row for row in session.scalars(state_query)}
    if dataset in {"calendar", "securities"}:
        securities = [("all", "全市场")]
    else:
        query = select(StockMeta.code, StockMeta.name).where(
            StockMeta.status == "active"
        )
        if entities is not None:
            query = query.where(StockMeta.code.in_(entities))
        securities = list(session.execute(query))
    expires = {}
    if dataset in SYMBOL_SUBSCRIPTIONS:
        subscription_query = select(SourceSubscription).where(
            SourceSubscription.dataset == dataset,
            SourceSubscription.last_requested_at > now - timedelta(days=7),
        )
        if entities is not None:
            subscription_query = subscription_query.where(
                SourceSubscription.arguments["symbol"].as_string().in_(entities)
            )
        subscriptions = session.scalars(subscription_query)
        for item in subscriptions:
            symbol = str(item.arguments.get("symbol") or "")
            expires[symbol] = max(
                expires.get(symbol, now), item.last_requested_at + timedelta(days=7)
            )
        securities = [(code, name) for code, name in securities if code in expires]
    result = []
    for symbol, name in securities:
        state = states.get(symbol)
        result.append(
            {
                "dataset": dataset,
                "entity": symbol,
                "symbol": symbol,
                "name": name or symbol,
                "status": state_status(state, policy, now=now),
                **{
                    key: getattr(state, key, None)
                    for key in (
                        "data_time",
                        "checked_at",
                        "last_success_at",
                        "source",
                        "version",
                        "error",
                    )
                },
                "next_check_at": next_transition(
                    dataset,
                    now,
                    fetched_at=state.last_success_at if state else None,
                    max_age=policy.max_age_seconds,
                    data_time=state.data_time if state else None,
                    expires=expires.get(symbol),
                )
                if state or expires.get(symbol)
                else None,
            }
        )
    return result


def _summary(session, dataset):
    from market_data_service.jobs import job_dict

    counts = dict(
        session.execute(
            select(Coverage.status, func.count())
            .where(Coverage.dataset == dataset)
            .group_by(Coverage.status)
        ).all()
    )
    total = sum(counts.values())
    latest, oldest = session.execute(
        select(func.max(Coverage.data_time), func.min(Coverage.data_time)).where(
            Coverage.dataset == dataset
        )
    ).one()
    policy = session.get(DatasetPolicy, dataset)
    job = session.scalar(
        select(SyncJob)
        .where(SyncJob.dataset == dataset)
        .order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
        .limit(1)
    )
    return {
        "id": dataset,
        "label": DATASET_LABELS[dataset],
        "scope": "market"
        if dataset in {"calendar", "securities", "kline", "financials"}
        else "subscribed",
        "total": total,
        **{status: counts.get(status, 0) for status in STATUSES},
        "coverage_percent": round(counts.get("fresh", 0) * 100 / total, 1)
        if total
        else None,
        "latest_data_time": latest,
        "oldest_data_time": oldest,
        "policy": {
            "enabled": policy.enabled,
            "interval_seconds": policy.interval_seconds,
            "max_age_seconds": policy.max_age_seconds,
            "next_run_at": iso(policy.next_run_at),
        },
        "latest_job": job_dict(job) if job else None,
    }


def refresh_projections(database, events=(), *, now=None, force=False):
    """One coalesced commit for all changed entities, with constant-size summaries."""
    from market_data_service.jobs import job_dict

    now = now or utcnow()
    full, targets, jobs, summaries = set(), defaultdict(set), set(), set()
    with database.session_scope() as session:
        full.update(
            set(DATASETS) - set(session.scalars(select(DatasetProjection.dataset)))
        )
        if force:
            full.update(DATASETS)
        for event in events:
            dataset, kind, entity = event.dataset, event.kind, event.entity
            if kind in {"master", "calendar"} or (
                dataset == "calendar" and kind in {"state", "policy"}
            ):
                from market_data_service.calendar import _calendar_bucket

                _calendar_bucket.cache_clear()
                full.update(DATASETS)
            elif kind == "policy":
                full.add(dataset)
            elif kind == "job":
                jobs.add(entity)
                summaries.add(dataset)
            elif kind == "state":
                targets[dataset].add(entity)
            elif kind == "observation" and dataset in SUBSCRIBED:
                targets[dataset].add(entity)
            elif kind == "subscription":
                if dataset in SUBSCRIBED:
                    targets[dataset].add(entity)
                elif dataset in SYMBOL_SUBSCRIPTIONS:
                    item = session.get(SourceSubscription, entity)
                    if item:
                        targets[dataset].add(str(item.arguments.get("symbol") or ""))
                    else:
                        full.add(dataset)
        due = session.execute(
            select(Coverage.dataset, Coverage.entity).where(
                Coverage.next_check_at <= now
            )
        ).all()
        for dataset, entity in due:
            targets[dataset].add(entity)
        changed = {}
        for dataset in full | set(targets):
            entities = None if dataset in full else targets[dataset]
            query = delete(Coverage).where(Coverage.dataset == dataset)
            if entities is not None:
                query = query.where(Coverage.entity.in_(entities))
            session.execute(query)
            rows = _coverage_records(session, dataset, entities, now)
            database.upsert(session, Coverage, rows, ["dataset", "entity"])
            changed[dataset] = (
                sorted(entities)
                if entities is not None and len(entities) <= 200
                else ["*"]
            )
            summaries.add(dataset)
        payloads = []
        for dataset in sorted(summaries):
            payload = _summary(session, dataset)
            database.upsert(
                session,
                DatasetProjection,
                [{"dataset": dataset, "payload": payload, "updated_at": now}],
                ["dataset"],
            )
            payloads.append(payload)
        job_rows = (
            [
                job_dict(row)
                for row in session.scalars(select(SyncJob).where(SyncJob.id.in_(jobs)))
            ]
            if jobs
            else []
        )
    return {
        "datasets": payloads,
        "jobs": job_rows,
        "coverage": changed,
        "checked_at": iso(now),
    }


def overview(session):
    rows = {row.dataset: row for row in session.scalars(select(DatasetProjection))}
    if len(rows) != len(DATASETS):
        from fastapi import HTTPException

        raise HTTPException(503, "数据汇总正在初始化，请检查事件维护进程")
    return {
        "items": [rows[dataset].payload for dataset in DATASETS],
        "checked_at": iso(max(row.updated_at for row in rows.values())),
    }


def coverage_query(dataset, status="all", search=""):
    query = select(Coverage).where(Coverage.dataset == dataset)
    if status != "all":
        query = query.where(Coverage.status == status)
    if search:
        query = query.where(
            or_(
                Coverage.symbol.contains(search, autoescape=True),
                Coverage.name.contains(search, autoescape=True),
            )
        )
    return query


def coverage_dict(row):
    return {
        key: iso(value) if isinstance(value, datetime) else value
        for key in (
            "symbol",
            "name",
            "status",
            "data_time",
            "checked_at",
            "last_success_at",
            "source",
            "version",
            "error",
        )
        for value in [getattr(row, key)]
    }
