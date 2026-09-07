"""Versioned data and maintenance API. Only this service can access its database."""

import csv
import hmac
import io
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from redis.exceptions import RedisError
from sqlalchemy import func, or_, select, update

from market_data_service.control_models import (
    DatasetPolicy,
    DataState,
    Observation,
    ServiceHeartbeat,
    SourceSubscription,
    SyncItem,
    SyncJob,
    TradingDay,
    utcnow,
)
from market_data_service.database import get_database
from market_data_service.freshness import iso, state_status
from market_data_service.jobs import cancel, enqueue, job_dict, retry
from market_data_service.models import StockMeta
from market_data_service.schemas import (
    Dataset,
    PolicyUpdate,
    SnapshotRequest,
    SourceRequest,
    SyncRequest,
)
from market_data_service.settings import get_settings

logger = logging.getLogger(__name__)


def authorize(request: Request):
    settings = get_settings()
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if settings.api_token:
        if not hmac.compare_digest(token, settings.api_token):
            raise HTTPException(401, "数据服务凭证无效")
    elif request.client and request.client.host not in {
        "127.0.0.1",
        "::1",
        "testclient",
    }:
        raise HTTPException(403, "未配置凭证时仅允许本机访问")


@asynccontextmanager
async def lifespan(app):
    get_database().initialize()
    yield
    get_database().engine.dispose()


app = FastAPI(
    title="Market Data Service",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(authorize)],
)


@app.exception_handler(ValueError)
async def invalid_request(_request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(KeyError)
async def missing_record(_request, _exc):
    return JSONResponse(status_code=404, content={"detail": "记录不存在"})


def table_dict(row):
    return jsonable_encoder(
        {
            column.key: iso(value) if isinstance(value, datetime) else value
            for column in row.__table__.columns
            for value in [getattr(row, column.key)]
        }
    )


def security_dict(row):
    values = table_dict(row)
    return {
        key: values.get(key)
        for key in (
            "code",
            "name",
            "market",
            "sector",
            "status",
            "ipo_date",
            "last_sync_at",
            "updated_at",
        )
    }


def policy_dict(policy):
    return {
        "enabled": policy.enabled,
        "interval_seconds": policy.interval_seconds,
        "max_age_seconds": policy.max_age_seconds,
        "next_run_at": iso(policy.next_run_at),
    }


def coverage_rows(session, dataset):
    from market_data_service.control_models import CoverageProjection
    from market_data_service.projections import coverage_dict, coverage_query

    return [
        coverage_dict(row)
        for row in session.scalars(
            coverage_query(dataset).order_by(
                CoverageProjection.symbol, CoverageProjection.entity
            )
        )
    ]


@app.get("/v1/health")
def health():
    database = get_database()
    with database.get_session() as session:
        session.execute(select(1))
        heartbeats = {
            row.component: {
                "last_seen_at": iso(row.updated_at),
                "healthy": utcnow() - row.updated_at < timedelta(seconds=120),
                "detail": row.detail,
            }
            for row in session.scalars(select(ServiceHeartbeat))
        }
        from market_data_service.control_models import ImportIssue

        import_issues = session.scalar(select(func.count()).select_from(ImportIssue))
    return {
        "status": "ok",
        "service": "market-data",
        "version": "1.0.0",
        "database": database.engine.dialect.name,
        "provider": get_settings().provider,
        "checked_at": iso(utcnow()),
        "components": heartbeats,
        "legacy_import_issues": import_issues,
    }


@app.get("/v1/capabilities")
def capabilities():
    from market_data_service.contracts import operation_schema
    from market_data_service.sources import registry

    return {
        "version": "1.0.0",
        "operations": [
            {
                "operation": operation,
                "dataset": dataset,
                "arguments_schema": operation_schema(operation).model_json_schema(),
            }
            for operation, (dataset, function) in registry().items()
        ],
    }


@app.get("/v1/datasets")
def datasets():
    from market_data_service.events import current_cursor
    from market_data_service.projections import overview

    # Capture the event cursor BEFORE the snapshot: reconnect/replay closes the
    # snapshot/subscription race without dropping an intervening commit.
    cursor = current_cursor()
    with get_database().get_session() as session:
        return {**overview(session), "event_cursor": cursor, "service": health()}


@app.get("/v1/datasets/{dataset}/coverage")
def coverage(
    dataset: Dataset,
    status: str = "all",
    search: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    from market_data_service.control_models import CoverageProjection
    from market_data_service.projections import STATUSES, coverage_dict, coverage_query

    if status not in {"all", *STATUSES}:
        raise ValueError("无效的覆盖状态")
    statement = coverage_query(dataset, status, search)
    with get_database().get_session() as session:
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = session.scalars(
            statement.order_by(CoverageProjection.symbol, CoverageProjection.entity)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {
            "items": [coverage_dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


@app.get("/v1/datasets/{dataset}/coverage.csv")
def export_coverage(dataset: Dataset):
    with get_database().get_session() as session:
        rows = coverage_rows(session, dataset)
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "symbol",
            "name",
            "status",
            "data_time",
            "checked_at",
            "last_success_at",
            "source",
            "version",
            "error",
        ],
    )
    writer.writeheader()
    # Guard spreadsheet formula execution in provider-originated names/errors.
    writer.writerows(
        {
            key: "'" + str(value)
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
            else value
            for key, value in row.items()
        }
        for row in rows
    )
    return Response(
        "\ufeff" + stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{dataset}-coverage.csv"'
        },
    )


@app.put("/v1/datasets/{dataset}/policy")
def update_policy(dataset: Dataset, body: PolicyUpdate):
    if body.max_age_seconds < body.interval_seconds:
        raise ValueError("允许的数据延迟不能小于同步间隔")
    with get_database().session_scope() as session:
        policy = session.get(DatasetPolicy, dataset)
        for key, value in body.model_dump().items():
            setattr(policy, key, value)
        policy.updated_at, policy.next_run_at = utcnow(), utcnow()
        session.execute(
            update(SourceSubscription)
            .where(SourceSubscription.dataset == dataset)
            .values(interval_seconds=body.interval_seconds, next_run_at=utcnow())
        )
        return policy_dict(policy)


@app.post("/v1/jobs", status_code=202)
def start_job(body: SyncRequest):
    job, created = enqueue(body)
    return {**job, "created": created}


@app.get("/v1/jobs")
def jobs(
    dataset: Dataset | None = None,
    limit: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    with get_database().get_session() as session:
        statement = select(SyncJob)
        if dataset:
            statement = statement.where(SyncJob.dataset == dataset)
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        return {
            "items": [
                job_dict(row)
                for row in session.scalars(
                    statement.order_by(SyncJob.created_at.desc(), SyncJob.id)
                    .offset((page - 1) * limit)
                    .limit(limit)
                )
            ],
            "total": total,
            "page": page,
            "page_size": limit,
        }


@app.get("/v1/jobs/{job_id}")
def job_detail(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    failures_only: bool = False,
):
    with get_database().get_session() as session:
        job = session.get(SyncJob, job_id)
        if not job:
            raise KeyError(job_id)
        statement = select(SyncItem).where(SyncItem.job_id == job_id)
        if failures_only:
            statement = statement.where(SyncItem.status == "failed")
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = list(
            session.scalars(
                statement.order_by(SyncItem.symbol)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return {
            **job_dict(job),
            "items": [table_dict(row) for row in rows],
            "items_total": total,
            "page": page,
        }


@app.post("/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return cancel(job_id)


@app.post("/v1/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    return retry(job_id)


@app.get("/v1/securities")
def securities(
    search: str = "",
    market: str = "all",
    sector: str = "",
    status: str = "active",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=10000),
    codes: str = "",
):
    with get_database().get_session() as session:
        statement = select(StockMeta)
        if status != "all":
            statement = statement.where(StockMeta.status == status)
        if market != "all":
            statement = statement.where(StockMeta.market.in_(market.split(",")))
        if search:
            statement = statement.where(
                or_(StockMeta.code.contains(search), StockMeta.name.contains(search))
            )
        if sector:
            statement = statement.where(StockMeta.sector.contains(sector))
        if codes:
            statement = statement.where(StockMeta.code.in_(codes.split(",")))
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = session.scalars(
            statement.order_by(StockMeta.code)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        state = session.scalar(
            select(DataState).where(
                DataState.dataset == "securities", DataState.symbol == "all"
            )
        )
        return {
            "items": [security_dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "freshness": state_status(state, session.get(DatasetPolicy, "securities")),
        }


@app.get("/v1/calendar")
def calendar():
    with get_database().get_session() as session:
        days = list(session.scalars(select(TradingDay.day).order_by(TradingDay.day)))
        state = session.scalar(
            select(DataState).where(
                DataState.dataset == "calendar", DataState.symbol == "all"
            )
        )
        fresh = state_status(state, session.get(DatasetPolicy, "calendar")) == "fresh"
    ready = (
        bool(days)
        and max(days)[:4] >= str(datetime.now(ZoneInfo("Asia/Shanghai")).year)
        and fresh
    )
    if not ready:
        enqueue(SyncRequest(dataset="calendar"))
    return {"days": days, "ready": ready}


def _snapshot_once(body: SnapshotRequest):
    database = get_database()
    with database.get_session() as session:
        metas = {
            row.code: table_dict(row)
            for row in session.scalars(
                select(StockMeta).where(StockMeta.code.in_(body.symbols))
            )
        }
        items = {
            code: {
                "security": {
                    key: value
                    for key, value in (metas.get(code) or {}).items()
                    if key in {"code", "name", "market", "sector", "status", "ipo_date"}
                },
                "financials": metas.get(code)
                if "financials" in body.datasets
                else None,
                "kline": [],
                "news": [],
                "quotes": None,
            }
            for code in body.symbols
        }
        states = {
            (row.dataset, row.symbol): row
            for row in session.scalars(
                select(DataState).where(
                    DataState.dataset.in_(body.datasets),
                    DataState.symbol.in_([*body.symbols, "all"]),
                )
            )
        }
        policies = {row.dataset: row for row in session.scalars(select(DatasetPolicy))}
        statuses = {
            code: {
                dataset: state_status(
                    states.get(
                        (
                            dataset,
                            "all" if dataset in {"calendar", "securities"} else code,
                        )
                    ),
                    policies[dataset],
                )
                for dataset in body.datasets
            }
            for code in body.symbols
        }
        if "kline" in body.datasets:
            from market_data_service.sources import (
                latest_observation,
                observation_payload,
            )

            versions = [
                state.version
                for (dataset, code), state in states.items()
                if dataset == "kline" and state.version
            ]
            observations_by_version = {
                row.id: row
                for row in session.scalars(
                    select(Observation).where(Observation.id.in_(versions))
                )
            }
            for code in body.symbols:
                state = states.get(("kline", code))
                observation = (
                    observations_by_version.get(state.version) if state else None
                )
                if body.start_date or body.end_date:
                    arguments = {"symbol": code, "count": body.count}
                    if body.start_date:
                        arguments["start_date"] = body.start_date
                    if body.end_date:
                        arguments["end_date"] = body.end_date
                    observation = latest_observation("kline", arguments)
                if observation:
                    payload = observation_payload(observation, allow_stale=True)
                    rows = payload.get("data") or []
                    # A historical query may be fresh for its own window, but
                    # cannot override the current trading-day watermark.
                    if body.start_date or body.end_date:
                        statuses[code]["kline"] = payload["data_service"]["status"]
                    requested = (
                        payload.get("requested_count")
                        or observation.arguments.get("count")
                        or len(rows)
                    )
                    if (
                        not body.start_date
                        and len(rows) < body.count
                        and requested < body.count
                    ):
                        statuses[code]["kline"] = "partial"
                    items[code]["kline"] = rows[-body.count :]
                    items[code]["kline_coverage"] = {
                        "requested": body.count,
                        "returned": len(items[code]["kline"]),
                        "partial": len(rows) < body.count,
                        "version": observation.id,
                    }
                else:
                    statuses[code]["kline"] = "missing"
        if "news" in body.datasets:
            versions = [
                state.version
                for (dataset, code), state in states.items()
                if dataset == "news" and state.version
            ]
            observations = {
                row.id: row
                for row in session.scalars(
                    select(Observation).where(Observation.id.in_(versions))
                )
            }
            for code in body.symbols:
                state = states.get(("news", code))
                if state and (observation := observations.get(state.version)):
                    items[code]["news"] = observation.payload.get("items", [])
        for code in body.symbols:
            if "quotes" in body.datasets:
                state = states.get(("quotes", code))
                observation = (
                    session.get(Observation, state.version)
                    if state and state.version
                    else None
                )
                if observation:
                    items[code]["quotes"] = observation.payload
            items[code]["versions"] = {
                dataset: state.version
                for dataset in body.datasets
                if (state := states.get((dataset, code)))
            }
            if "kline_coverage" in items[code]:
                items[code]["versions"]["kline"] = items[code]["kline_coverage"][
                    "version"
                ]
    not_ready = {
        code: [dataset for dataset, status in values.items() if status != "fresh"]
        for code, values in statuses.items()
    }
    not_ready = {code: values for code, values in not_ready.items() if values}
    if not_ready and body.freshness == "latest":
        from market_data_service.sources import dispatch_source, register_request

        for dataset in body.datasets:
            codes = [code for code, missing in not_ready.items() if dataset in missing]
            if not codes:
                continue
            if dataset not in {"calendar", "securities"} and (
                len(codes) <= 24
                or (
                    dataset == "kline"
                    and (body.count > 500 or body.start_date or body.end_date)
                )
            ):
                for index, code in enumerate(codes):
                    arguments = {"symbol": code}
                    if dataset == "kline":
                        arguments["count"] = body.count
                        if body.start_date:
                            arguments["start_date"] = body.start_date
                        if body.end_date:
                            arguments["end_date"] = body.end_date
                    key = register_request(dataset, arguments)
                    # Beat is the durable fallback if immediate dispatch fails.
                    try:
                        if index < 24:
                            dispatch_source(key)
                    except Exception:
                        logger.exception("立即派发暂不可用；持久订阅将由调度恢复")
            else:
                enqueue(SyncRequest(dataset=dataset, symbols=codes))
        return JSONResponse(
            status_code=202,
            content={
                "status": "refreshing",
                "message": "数据正在更新，尚不能作为最新数据使用",
                "not_ready": not_ready,
                "freshness": statuses,
            },
            headers={"Retry-After": "3"},
        )
    return {
        "items": items,
        "freshness": statuses,
        "partial": bool(not_ready),
        "checked_at": iso(utcnow()),
    }


def _observation_once(body: SourceRequest):
    from market_data_service.sources import (
        dispatch_source,
        latest_observation,
        observation_payload,
        register_request,
    )

    key = register_request(body.operation, body.arguments)
    observation = latest_observation(body.operation, body.arguments)
    if observation:
        payload = observation_payload(
            observation, allow_stale=body.freshness == "allow_stale"
        )
        if payload is not None:
            return requested_payload(payload, body)
    try:
        dispatch_source(key)
    except Exception:
        logger.exception("立即派发暂不可用；持久订阅将由调度恢复")
    with get_database().get_session() as session:
        subscription = session.get(SourceSubscription, key)
        error = subscription.error
    return JSONResponse(
        status_code=202,
        content={
            "status": "refreshing",
            "request_id": key,
            "message": "上游暂不可用，后台将继续补采"
            if error
            else "正在获取符合时效要求的数据",
            "error": error,
        },
        headers={"Retry-After": "3"},
    )


def requested_payload(payload, body):
    if body.operation == "kline" and not body.arguments.get("start_date"):
        rows = (payload.get("data") or [])[-int(body.arguments.get("count", 500)) :]
        return {**payload, "data": rows, "count": len(rows)}
    return payload


@app.get("/v1/observations/{version}")
def observation_version(version: str):
    with get_database().get_session() as session:
        observation = session.get(Observation, version)
        if not observation:
            raise KeyError(version)
        return {
            "version": observation.id,
            "data": observation.payload,
            "data_time": observation.data_time,
            "fetched_at": iso(observation.fetched_at),
            "source": observation.source,
            "historical": True,
        }


async def _read_when_ready(body, read, predicate):
    from market_data_service.events import event_reader

    if not body.max_wait_seconds:
        return await run_in_threadpool(read, body)
    deadline = time.monotonic() + body.max_wait_seconds
    async with event_reader() as reader:
        try:
            cursor = await reader.tail()
        except RedisError:
            # A notification outage must not prevent an already-fresh read.
            return await run_in_threadpool(read, body)
        result = await run_in_threadpool(read, body)
        while isinstance(result, JSONResponse) and result.status_code == 202:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                cursor, event = await reader.wait(cursor, remaining, predicate)
            except RedisError:
                break
            if event is None:
                break
            result = await run_in_threadpool(read, body)
        return result


@app.post("/v1/observations")
async def observations(body: SourceRequest):
    from market_data_service.events import affects_data
    from market_data_service.sources import identity, registry

    key = identity(body.operation, body.arguments)
    dataset = registry()[body.operation][0]
    return await _read_when_ready(
        body,
        _observation_once,
        lambda payload: affects_data(
            payload,
            datasets=[dataset],
            symbols=[str(body.arguments.get("symbol") or "all")],
            request_keys=[key],
        ),
    )


@app.post("/v1/snapshots")
async def snapshots(body: SnapshotRequest):
    from market_data_service.events import affects_data
    from market_data_service.sources import identity

    keys = []
    for dataset in body.datasets:
        if dataset in {"calendar", "securities"}:
            continue
        for code in body.symbols:
            arguments = {"symbol": code}
            if dataset == "kline":
                arguments["count"] = body.count
                arguments.update(
                    {
                        key: value
                        for key in ("start_date", "end_date")
                        if (value := getattr(body, key))
                    }
                )
            keys.append(identity(dataset, arguments))
    return await _read_when_ready(
        body,
        _snapshot_once,
        lambda payload: affects_data(
            payload,
            datasets=body.datasets,
            symbols=body.symbols,
            request_keys=keys,
        ),
    )


@app.get("/v1/events", response_class=EventSourceResponse)
async def changes(
    request: Request,
    after: str = "0-0",
    last_event_id: str | None = Query(None, alias="lastEventId"),
):
    from market_data_service.events import event_reader, validate_cursor

    cursor = validate_cursor(
        request.headers.get("Last-Event-ID") or last_event_id or after
    )
    async with event_reader() as reader:
        cursor, reset = await reader.resume(cursor)
        if reset:
            yield ServerSentEvent(
                event="reset",
                id=cursor,
                data={"reason": "事件历史已更新，重新读取快照"},
            )
        yield ServerSentEvent(
            event="ready", id=cursor, data={"cursor": cursor}, retry=3000
        )
        while True:
            cursor, reset = await reader.resume(cursor)
            if reset:
                yield ServerSentEvent(
                    event="reset",
                    id=cursor,
                    data={"reason": "订阅落后于保留窗口，重新读取快照"},
                )
            rows = await reader.read(cursor)
            for event_id, payload in rows:
                cursor = event_id
                yield ServerSentEvent(event="change", id=event_id, data=payload)
            if not rows:
                cursor, reset = await reader.resume(cursor)
                if reset:
                    yield ServerSentEvent(
                        event="reset",
                        id=cursor,
                        data={"reason": "事件历史已更新，重新读取快照"},
                    )
                yield ServerSentEvent(comment="keep-alive")


@app.get("/v1/jobs/{job_id}/events", response_class=EventSourceResponse)
async def job_events(job_id: str, timeout: float = Query(30, ge=0, le=300)):
    from market_data_service.events import event_reader
    from market_data_service.jobs import TERMINAL

    def read_job():
        with get_database().get_session() as session:
            row = session.get(SyncJob, job_id)
            if row is None:
                raise HTTPException(404, "任务不存在")
            return job_dict(row)

    async with event_reader() as reader:
        cursor = await reader.tail()
        job = await run_in_threadpool(read_job)
        deadline = time.monotonic() + timeout
        yield ServerSentEvent(event="job", data=job)
        while (
            job["status"] not in TERMINAL
            and (remaining := deadline - time.monotonic()) > 0
        ):
            cursor, payload = await reader.wait(
                cursor,
                remaining,
                lambda value: (
                    value.get("reset")
                    or any(item["id"] == job_id for item in value.get("jobs", []))
                ),
            )
            if payload is None:
                break
            job = await run_in_threadpool(read_job)
            yield ServerSentEvent(event="job", id=cursor, data=job)
