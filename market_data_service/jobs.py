"""Durable domain jobs. Celery owns delivery; database leases fence duplicate execution."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import hashlib
import json
import logging
import threading
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from market_data_service.control_models import (
    DataState,
    DatasetPolicy,
    Observation,
    SourceSubscription,
    SyncItem,
    SyncJob,
    TradingDay,
    utcnow,
)
from market_data_service.database import get_database
from market_data_service.freshness import state_status, iso
from market_data_service.models import StockMeta, StockDaily, NewsIntel
from market_data_service.providers.common import bare_symbol, json_value
from market_data_service.providers.live import get_provider
from market_data_service.schemas import SyncRequest
from market_data_service.settings import get_settings

logger = logging.getLogger(__name__)
TERMINAL = {"success", "partial", "failed", "cancelled"}


def job_dict(job):
    return {
        key: getattr(job, key)
        for key in (
            "id",
            "dataset",
            "status",
            "trigger",
            "mode",
            "symbols",
            "retry_of",
            "total",
            "progress",
            "succeeded",
            "failed",
            "message",
            "error",
            "cancel_requested",
        )
    } | {
        key: iso(getattr(job, key))
        for key in ("created_at", "started_at", "finished_at")
    }


def enqueue(request: SyncRequest, *, trigger="manual", retry_of=None):
    database = get_database()
    with database.session_scope() as session:
        existing = session.scalar(
            select(SyncJob).where(SyncJob.active_key == request.dataset)
        )
        if existing:
            if (
                request.symbols
                and existing.symbols
                and not set(request.symbols).issubset(existing.symbols)
            ):
                raise ValueError("该数据集已有其他范围的同步任务，请等待完成后重试")
            return job_dict(existing), False
        job = SyncJob(
            id=str(uuid.uuid4()),
            dataset=request.dataset,
            active_key=request.dataset,
            trigger=trigger,
            mode=request.mode,
            symbols=request.symbols,
            retry_of=retry_of,
        )
        try:
            with session.begin_nested():
                session.add(job)
                session.flush()
        except IntegrityError:
            existing = session.scalar(
                select(SyncJob).where(SyncJob.active_key == request.dataset)
            )
            if not existing:
                raise
            return job_dict(existing), False
        return job_dict(job), True


def cancel(job_id):
    with get_database().session_scope() as session:
        job = session.get(SyncJob, job_id)
        if not job:
            raise KeyError(job_id)
        if job.status not in TERMINAL:
            job.cancel_requested = True
            job.message = "正在安全取消，已写入的数据会保留"
            if job.status == "queued":
                job.status, job.active_key, job.finished_at = (
                    "cancelled",
                    None,
                    utcnow(),
                )
        return job_dict(job)


def retry(job_id):
    with get_database().get_session() as session:
        old = session.get(SyncJob, job_id)
        if not old:
            raise KeyError(job_id)
        if old.status not in TERMINAL or old.status == "success":
            raise ValueError("仅可重试失败、部分完成或已取消的任务")
        pending = list(
            session.scalars(
                select(SyncItem.symbol).where(
                    SyncItem.job_id == job_id, SyncItem.status != "success"
                )
            )
        )
        symbols = (
            ([code for code in pending if code != "all"] or old.symbols)
            if old.dataset not in {"market", "macro", "rss"}
            else []
        )
        return enqueue(
            SyncRequest(dataset=old.dataset, symbols=symbols, mode="all"),
            trigger="retry",
            retry_of=old.id,
        )[0]


def target_symbols(session, job):
    if job.dataset in {"market", "macro", "rss"}:
        from market_data_service.sources import latest_observation, observation_payload

        subscriptions = list(
            session.scalars(
                select(SourceSubscription).where(
                    SourceSubscription.dataset == job.dataset,
                    SourceSubscription.last_requested_at
                    >= utcnow() - timedelta(days=7),
                )
            )
        )
        if job.retry_of:
            pending = set(
                session.scalars(
                    select(SyncItem.symbol).where(
                        SyncItem.job_id == job.retry_of, SyncItem.status != "success"
                    )
                )
            )
            subscriptions = [
                item
                for item in subscriptions
                if not pending or item.request_key in pending
            ]
        result = []
        for item in subscriptions:
            observation = latest_observation(item.operation, item.arguments)
            if (
                job.mode == "all"
                or observation is None
                or (job.mode == "stale" and observation_payload(observation) is None)
            ):
                result.append(item.request_key)
        return result
    if job.dataset in {"calendar", "securities"}:
        return ["all"]
    codes = job.symbols or list(
        session.scalars(
            select(StockMeta.code)
            .where(StockMeta.status == "active")
            .order_by(StockMeta.code)
        )
    )
    if not job.symbols and job.dataset in {"quotes", "news", "announcements"}:
        subscriptions = session.scalars(
            select(SourceSubscription).where(
                SourceSubscription.dataset == job.dataset,
                SourceSubscription.last_requested_at >= utcnow() - timedelta(days=7),
            )
        )
        tracked = set()
        for item in subscriptions:
            raw_symbol = str(item.arguments.get("symbol") or "").strip()
            if not raw_symbol:
                continue
            try:
                tracked.add(bare_symbol(raw_symbol))
            except ValueError:
                logger.warning(
                    "忽略无法解析证券订阅：dataset=%s symbol=%s",
                    job.dataset,
                    raw_symbol,
                )
        codes = [code for code in codes if code in tracked]
    if job.mode == "all":
        return codes
    states = {
        state.symbol: state
        for state in session.scalars(
            select(DataState).where(DataState.dataset == job.dataset)
        )
    }
    policy = session.get(DatasetPolicy, job.dataset)
    if job.mode == "missing":
        return [
            code
            for code in codes
            if code not in states or not states[code].last_success_at
        ]
    return [code for code in codes if state_status(states.get(code), policy) != "fresh"]


def _claim(job_id):
    database, token, now = get_database(), str(uuid.uuid4()), utcnow()
    with database.session_scope() as session:
        claimed = session.execute(
            update(SyncJob)
            .where(
                SyncJob.id == job_id,
                SyncJob.cancel_requested.is_(False),
                (SyncJob.status == "queued")
                | ((SyncJob.status == "running") & (SyncJob.lease_until < now)),
            )
            .values(
                status="running",
                lease_token=token,
                lease_until=now + timedelta(seconds=get_settings().lease_seconds),
                started_at=func.coalesce(SyncJob.started_at, now),
                message="正在核查数据范围并采集",
            )
        )
        if claimed.rowcount != 1:
            return None
        job = session.get(SyncJob, job_id)
        existing = list(
            session.scalars(select(SyncItem).where(SyncItem.job_id == job_id))
        )
        if not existing:
            codes = target_symbols(session, job)
            database.upsert(
                session,
                SyncItem,
                [
                    {
                        "job_id": job_id,
                        "symbol": code,
                        "status": "queued",
                        "attempts": 0,
                        "updated_at": now,
                    }
                    for code in codes
                ],
                ["job_id", "symbol"],
                update=False,
            )
            job.total = len(codes)
        session.flush()
        codes = list(
            session.scalars(
                select(SyncItem.symbol).where(
                    SyncItem.job_id == job_id, SyncItem.status != "success"
                )
            )
        )
        return token, job.dataset, codes


def _owns(session, job_id, token):
    return session.scalar(
        select(SyncJob)
        .where(
            SyncJob.id == job_id,
            SyncJob.lease_token == token,
            SyncJob.status == "running",
            SyncJob.cancel_requested.is_(False),
        )
        .with_for_update()
    )


def publish(
    session, dataset, symbol, payload, fetched_at, *, operation=None, arguments=None
):
    """Atomically publish source rows + immutable observation + freshness watermark."""
    database = get_database()
    operation = operation or dataset
    arguments = arguments or {"symbol": symbol}
    payload = json_value(payload)
    request_key = hashlib.sha256(
        json.dumps([operation, arguments], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    version = hashlib.sha256(
        json.dumps(
            [request_key, payload, fetched_at.isoformat()],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    source = str(payload.get("source") or "AKShare")[:128]
    data_time = str(payload.get("data_time") or "") or None
    database.upsert(
        session,
        Observation,
        [
            {
                "id": version,
                "dataset": dataset,
                "symbol": symbol,
                "operation": operation,
                "request_key": request_key,
                "arguments": arguments,
                "payload": payload,
                "source": source,
                "data_time": data_time,
                "fetched_at": fetched_at,
            }
        ],
        ["id"],
        update=False,
    )
    canonical = (
        operation == dataset
        or dataset in {"kline", "quotes"}
        or operation
        in {
            "news.read_company_news_akshare",
            "announcements.read_company_announcements_akshare",
        }
    )
    if canonical:
        # Materialize the lock row first: two first-ever publications must be
        # fenced just as strictly as updates to an existing watermark.
        database.upsert(
            session,
            DataState,
            [{"dataset": dataset, "symbol": symbol, "status": "missing"}],
            ["dataset", "symbol"],
            update=False,
        )
    current = session.scalar(
        select(DataState)
        .where(DataState.dataset == dataset, DataState.symbol == symbol)
        .with_for_update()
    )
    if current and current.last_success_at and current.last_success_at > fetched_at:
        return version
    status = "ready"
    if dataset == "kline":
        rows = payload.get("data") or []
        if not rows:
            raise ValueError("上游没有提供有效日线")
        fields = {
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "pct_chg",
            "ma5",
            "ma10",
            "ma20",
            "volume_ratio",
        }
        from collections import defaultdict

        batches = defaultdict(list)
        for row in rows:
            # Never overwrite an existing amount with absent provider data.
            values = {
                key: value
                for key, value in row.items()
                if key in fields and value is not None
            }
            values.update(
                code=symbol,
                date=datetime.fromisoformat(str(row["date"])[:10]).date(),
                data_source=source + "_shares",
                updated_at=fetched_at,
            )
            batches[tuple(sorted(values))].append(values)
        for batch in batches.values():
            database.upsert(session, StockDaily, batch, ["code", "date"])
        data_time = max(str(row["date"])[:10] for row in rows)
        if current and current.data_time and data_time < current.data_time:
            canonical = False  # a historical read cannot certify today's bars
        elif current and current.data_time == data_time and current.version:
            previous = session.get(Observation, current.version)
            if previous and len(previous.payload.get("data") or []) > len(rows):
                canonical = False  # a short-window read must not discard the full maintained window
    elif (
        dataset == "financials"
        and operation == "financials"
        and isinstance(payload.get("data"), dict)
    ):
        data = payload["data"]
        fields = (
            "revenue_latest",
            "net_profit_latest",
            "operating_cf_latest",
            "revenue_ttm",
            "parent_net_profit_ttm",
            "deducted_net_profit_ttm",
            "debt_ratio",
            "report_date",
        )
        # Replace the report as a coherent unit; never COALESCE across periods.
        written = session.execute(
            update(StockMeta)
            .where(StockMeta.code == symbol)
            .values(
                **{key: data.get(key) for key in fields},
                financial_fetched_at=fetched_at,
            )
        )
        if not written.rowcount:
            raise ValueError("证券主数据尚未包含该股票，不能认证财务快照")
        data_time = data.get("report_date")
        if any(
            data.get(key) is None
            for key in (
                "revenue_ttm",
                "parent_net_profit_ttm",
                "deducted_net_profit_ttm",
                "debt_ratio",
            )
        ):
            status = "partial"
    elif dataset == "news":
        for item in payload.get("items") or []:
            url = item.get("url") or item.get("link")
            if not url:
                continue
            database.upsert(
                session,
                NewsIntel,
                [
                    {
                        "code": symbol,
                        "title": str(item.get("title") or ""),
                        "url": url,
                        "snippet": str(
                            item.get("summary") or item.get("snippet") or ""
                        ),
                        "source": source,
                        "fetched_at": fetched_at,
                    }
                ],
                ["url"],
                update=False,
            )
    # A six-period provider statement is not the canonical TTM snapshot. Its
    # refresh must not certify a different table that it did not update.
    if canonical and (
        current is None
        or not current.last_success_at
        or current.last_success_at <= fetched_at
    ):
        database.upsert(
            session,
            DataState,
            [
                {
                    "dataset": dataset,
                    "symbol": symbol,
                    "data_time": data_time,
                    "checked_at": fetched_at,
                    "last_success_at": fetched_at,
                    "status": status,
                    "source": source,
                    "version": version,
                    "error": None,
                }
            ],
            ["dataset", "symbol"],
        )
        if current is not None:
            session.expire(current)  # native upsert bypasses the ORM identity map
    return version


def _publish_global(session, dataset, value, fetched_at):
    database = get_database()
    if dataset == "calendar":
        if not value or max(value)[:4] < str(datetime.now().year):
            raise ValueError("上游交易日历未覆盖当前年份")
        database.upsert(
            session, TradingDay, [{"day": day} for day in value], ["day"], update=False
        )
        from market_data_service.calendar import _calendar_bucket

        _calendar_bucket.cache_clear()
        return {
            "success": True,
            "data": value,
            "source": "AKShare/Sina",
            "data_time": max(value),
        }
    old_codes = set(
        session.scalars(select(StockMeta.code).where(StockMeta.status == "active"))
    )
    new_codes = {item["code"] for item in value}
    if old_codes and len(old_codes - new_codes) > max(50, int(len(old_codes) * 0.02)):
        raise ValueError("上游证券列表缺页风险，已阻止覆盖和错误退市")
    for item in value:
        values = {key: item.get(key) for key in ("code", "name", "market")}
        for key in ("sector", "ipo_date"):
            if item.get(key):
                values[key] = (
                    datetime.fromisoformat(str(item[key])[:10]).date()
                    if key == "ipo_date"
                    else item[key]
                )
        values.update(status="active", last_sync_at=fetched_at, updated_at=fetched_at)
        database.upsert(session, StockMeta, [values], ["code"])
    if new_codes:
        session.execute(
            update(StockMeta)
            .where(StockMeta.status == "active", StockMeta.code.not_in(new_codes))
            .values(status="delisted", updated_at=fetched_at)
        )
    return {
        "success": True,
        "count": len(value),
        "source": "AKShare/security-master",
        "data_time": None,
    }


def run_job(job_id):
    claim = _claim(job_id)
    if not claim:
        return
    token, dataset, codes = claim
    database, stopped = get_database(), threading.Event()

    def heartbeat():
        while not stopped.wait(10):
            with database.session_scope() as session:
                session.execute(
                    update(SyncJob)
                    .where(
                        SyncJob.id == job_id,
                        SyncJob.lease_token == token,
                        SyncJob.status == "running",
                    )
                    .values(
                        lease_until=utcnow()
                        + timedelta(seconds=get_settings().lease_seconds)
                    )
                )
            database.heartbeat("sync-worker", "采集任务运行中")

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        provider = get_provider()
        bulk = {}
        if (
            dataset == "financials"
            and len(codes) > 10
            and get_settings().provider == "live"
        ):
            try:
                bulk = provider.financials_bulk(codes)
            except Exception:
                logger.warning("批量财报源失败，进入受限逐股补采", exc_info=True)

        def process(symbol):
            with database.get_session() as session:
                if not _owns(session, job_id, token):
                    return
            error = None
            from market_data_service.providers.common import force_source_read

            refresh_context = force_source_read.set(True)
            try:
                fetched_at = utcnow()
                if dataset in {"market", "macro", "rss"}:
                    from market_data_service.sources import (
                        refresh_source,
                        latest_observation,
                        observation_payload,
                    )

                    with database.get_session() as session:
                        force = session.get(SyncJob, job_id).mode == "all"
                    refresh_source(symbol, force=force)
                    with database.get_session() as session:
                        subscription = session.get(SourceSubscription, symbol)
                    observation = latest_observation(
                        subscription.operation, subscription.arguments
                    )
                    if observation is None or observation_payload(observation) is None:
                        raise ValueError(subscription.error or "来源尚未达到时效要求")
                    value = None
                else:
                    value = bulk.get(symbol)
                    if dataset == "kline":
                        from market_data_service.acquisition import read_kline

                        with database.get_session() as session:
                            force_full = session.get(SyncJob, job_id).mode == "all"
                        value = read_kline({"symbol": symbol}, force_full=force_full)
                    if value and dataset == "financials":
                        required = (
                            "revenue_ttm",
                            "parent_net_profit_ttm",
                            "deducted_net_profit_ttm",
                            "debt_ratio",
                        )
                        if any(
                            value.get("data", {}).get(field) is None
                            for field in required
                        ):
                            try:
                                replacement = provider.financials(symbol)
                                if (replacement.get("data_time") or "") >= (
                                    value.get("data_time") or ""
                                ):
                                    value = replacement
                            except Exception as exc:
                                value = {
                                    **value,
                                    "warnings": [f"逐股补源未完成: {exc}"],
                                }
                    value = value or (
                        getattr(provider, dataset)()
                        if symbol == "all"
                        else getattr(provider, dataset)(symbol)
                    )
                with database.session_scope() as session:
                    if not _owns(session, job_id, token):
                        return
                    if value is not None:
                        payload = (
                            _publish_global(session, dataset, value, fetched_at)
                            if symbol == "all"
                            else value
                        )
                        if payload.get("success") is False:
                            raise ValueError(
                                "; ".join(
                                    payload.get("errors") or ["来源未返回有效数据"]
                                )
                            )
                        publish(session, dataset, symbol, payload, fetched_at)
                        session.flush()
                        state = session.scalar(
                            select(DataState).where(
                                DataState.dataset == dataset, DataState.symbol == symbol
                            )
                        )
                        status = state_status(
                            state, session.get(DatasetPolicy, dataset)
                        )
                        if status != "fresh":
                            error = f"数据已保存但未达到时效/完整性要求（{status}）"
            except Exception as exc:
                error = str(exc)[:1000]
            finally:
                force_source_read.reset(refresh_context)
            with database.session_scope() as session:
                job = _owns(session, job_id, token)
                if not job:
                    return
                item = session.scalar(
                    select(SyncItem).where(
                        SyncItem.job_id == job_id, SyncItem.symbol == symbol
                    )
                )
                item.status, item.error, item.updated_at = (
                    ("failed" if error else "success"),
                    error,
                    utcnow(),
                )
                item.attempts += 1
                if error and dataset not in {"market", "macro", "rss"}:
                    state = session.scalar(
                        select(DataState).where(
                            DataState.dataset == dataset, DataState.symbol == symbol
                        )
                    )
                    if state:
                        state.error, state.checked_at = error, utcnow()
                    else:
                        session.add(
                            DataState(
                                dataset=dataset,
                                symbol=symbol,
                                status="failed",
                                checked_at=utcnow(),
                                error=error,
                            )
                        )
                session.execute(
                    update(SyncJob)
                    .where(SyncJob.id == job_id, SyncJob.lease_token == token)
                    .values(
                        progress=SyncJob.progress + 1,
                        failed=SyncJob.failed + int(bool(error)),
                        succeeded=SyncJob.succeeded + int(not error),
                    )
                )
                session.refresh(job)
                job.message = f"已处理 {job.progress} / {job.total}，成功 {job.succeeded}，待修复 {job.failed}"

        with database.session_scope() as session:
            job = _owns(session, job_id, token)
            if job:
                succeeded = session.scalar(
                    select(func.count())
                    .select_from(SyncItem)
                    .where(SyncItem.job_id == job_id, SyncItem.status == "success")
                )
                job.progress, job.succeeded, job.failed = succeeded, succeeded, 0
        # Bounded submission avoids thousands of pending futures and makes
        # cancellation responsive between provider requests.
        width = 1 if database._is_sqlite_engine else get_settings().concurrency
        with ThreadPoolExecutor(max_workers=width) as pool:
            for start in range(0, len(codes), width):
                with database.get_session() as session:
                    job = session.get(SyncJob, job_id)
                    if job.cancel_requested or job.lease_token != token:
                        break
                for future in as_completed(
                    [
                        pool.submit(process, code)
                        for code in codes[start : start + width]
                    ]
                ):
                    future.result()
        with database.session_scope() as session:
            job = session.get(SyncJob, job_id)
            if job.lease_token != token:
                return
            job.status = (
                "cancelled"
                if job.cancel_requested
                else "partial"
                if job.failed and job.succeeded
                else "failed"
                if job.failed
                else "success"
            )
            job.active_key, job.finished_at, job.lease_until = None, utcnow(), None
            if job.failed:
                policy = session.get(DatasetPolicy, dataset)
                policy.next_run_at = min(
                    policy.next_run_at,
                    utcnow() + timedelta(seconds=min(300, policy.interval_seconds)),
                )
            if not codes:
                job.message = (
                    "当前范围已达标，无需重复采集"
                    if dataset not in {"quotes", "news", "announcements"}
                    else "暂无需要采集的订阅；读取证券数据后会自动维护"
                )
    except Exception as exc:
        logger.exception("同步任务中断")
        with database.session_scope() as session:
            job = session.get(SyncJob, job_id)
            if job and job.lease_token == token:
                job.status, job.error, job.active_key, job.finished_at = (
                    "failed",
                    str(exc)[:1000],
                    None,
                    utcnow(),
                )
                policy = session.get(DatasetPolicy, dataset)
                policy.next_run_at = min(
                    policy.next_run_at,
                    utcnow() + timedelta(seconds=min(300, policy.interval_seconds)),
                )
    finally:
        stopped.set()
        thread.join(timeout=0.5)
        database.heartbeat("sync-worker", "采集进程可用")
