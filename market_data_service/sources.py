"""Closed source-operation registry and durable, automatically refreshed observations."""

from datetime import timedelta
from functools import lru_cache
import hashlib
import inspect
import json

from pydantic import validate_call, ConfigDict
from sqlalchemy import select, update

from market_data_service.control_models import (
    DataState,
    DatasetPolicy,
    Observation,
    SourceSubscription,
    utcnow,
)
from market_data_service.database import get_database
from market_data_service.jobs import publish
from market_data_service.providers.common import json_value
from market_data_service.providers.live import get_provider


@lru_cache
def registry():
    from market_data_service.providers import (
        financial_data,
        financials,
        financial_period,
        rss_reader,
    )

    provider = get_provider()
    result = {
        "kline": ("kline", provider.kline),
        "quotes": ("quotes", provider.quotes),
        "financials": ("financials", provider.financials),
        "news": ("news", provider.news),
        "announcements": ("announcements", provider.announcements),
        "financials.read_core_financial_indicators_ths": (
            "financials",
            financials.read_core_financial_indicators_ths,
        ),
        "financials.get_financial_bundle": (
            "financials",
            financial_data.get_financial_bundle,
        ),
        "financials.get_financial_section": (
            "financials",
            financial_data.get_financial_section,
        ),
        "financials.fetch_period": (
            "financials",
            financial_period.fetch_financial_period_snapshot,
        ),
        "rss.read_feed": ("rss", rss_reader.read_feed),
        "rss.read_item": ("rss", rss_reader.read_item),
    }
    from importlib import import_module
    from market_data_service.providers.operation_catalog import OPERATIONS
    from market_data_service.providers.rss_operations import RSS_OPERATIONS
    from market_data_service.providers.rss_transport import fetch_xml, fetch_json

    for operation, (dataset, module, name) in OPERATIONS.items():
        result[operation] = (
            dataset,
            getattr(import_module(f"market_data_service.providers.{module}"), name),
        )
    for operation, module, name in RSS_OPERATIONS:
        result[operation] = (
            "rss",
            getattr(import_module(f"market_data_service.providers.{module}"), name),
        )
    result["rss.transport._fetch_rss_feed"] = "rss", fetch_xml
    result["rss.transport._fetch_rss_feed_json"] = "rss", fetch_json
    return result


def identity(operation, arguments):
    if operation not in registry():
        raise ValueError(f"不支持的数据操作: {operation}")
    return hashlib.sha256(
        json.dumps([operation, arguments], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def register_request(operation, arguments):
    key = identity(operation, arguments)
    from market_data_service.contracts import operation_schema

    operation_schema(operation).model_validate(arguments)
    dataset, fn = registry()[operation]
    # Reject unexpected/missing arguments before enqueueing network work.
    try:
        inspect.signature(fn).bind(**arguments)
    except TypeError as exc:
        raise ValueError(f"数据请求参数不符合接口约定: {exc}") from exc
    database = get_database()
    with database.session_scope() as session:
        policy = session.get(DatasetPolicy, dataset)
        subscription = session.get(SourceSubscription, key)
        if subscription:
            subscription.last_requested_at = utcnow()
        else:
            database.upsert(
                session,
                SourceSubscription,
                [
                    {
                        "request_key": key,
                        "operation": operation,
                        "dataset": dataset,
                        "arguments": arguments,
                        "interval_seconds": policy.interval_seconds,
                        "next_run_at": utcnow(),
                        "last_requested_at": utcnow(),
                    }
                ],
                ["request_key"],
                update=False,
            )
    return key


def latest_observation(operation, arguments):
    key = identity(operation, arguments)
    with get_database().get_session() as session:
        exact = session.scalar(
            select(Observation)
            .where(Observation.request_key == key)
            .order_by(Observation.fetched_at.desc())
            .limit(1)
        )
        if exact:
            return exact
        if (
            operation == "kline"
            and not arguments.get("start_date")
            and not arguments.get("end_date")
        ):
            state = session.scalar(
                select(DataState).where(
                    DataState.dataset == "kline",
                    DataState.symbol == arguments.get("symbol"),
                )
            )
            observation = (
                session.get(Observation, state.version)
                if state and state.version
                else None
            )
            source = arguments.get("source_id", "auto")
            if (
                observation
                and not observation.arguments.get("end_date")
                and source in {"auto", observation.source}
            ):
                rows = observation.payload.get("data") or []
                if len(rows) >= int(arguments.get("count", 500)):
                    return observation
        return None


def dispatch_source(request_key):
    """Claim immediate delivery using the same durable due time as beat."""
    from market_data_service.worker import source_task

    now = utcnow()
    with get_database().session_scope() as session:
        claimed = session.execute(
            update(SourceSubscription)
            .where(
                SourceSubscription.request_key == request_key,
                SourceSubscription.next_run_at <= now,
            )
            .values(next_run_at=now + timedelta(seconds=180))
        )
        if not claimed.rowcount:
            return
    try:
        source_task.apply_async(args=[request_key], queue="market-data")
    except Exception:
        with get_database().session_scope() as session:
            session.execute(
                update(SourceSubscription)
                .where(SourceSubscription.request_key == request_key)
                .values(next_run_at=now)
            )


def observation_payload(observation, *, allow_stale=False, policy=None, now=None):
    if policy is None:
        with get_database().get_session() as session:
            policy = session.get(DatasetPolicy, observation.dataset)
    now = now or utcnow()
    age = max(0, (now - observation.fetched_at).total_seconds())
    payload = dict(observation.payload)
    status = (
        "stale"
        if age > policy.max_age_seconds or payload.get("is_stale") is True
        else "fresh"
    )
    if payload.get("_stale") is True:
        status = "stale"
    if (
        payload.get("freshness_unknown") is True
        and not payload.get("data_time_applicable") is False
    ):
        status = "unknown"
    if observation.operation == "financials":
        from types import SimpleNamespace
        from market_data_service.freshness import state_status

        values = payload.get("data") or {}
        partial = any(
            values.get(key) is None
            for key in (
                "revenue_ttm",
                "parent_net_profit_ttm",
                "deducted_net_profit_ttm",
                "debt_ratio",
            )
        )
        state = SimpleNamespace(
            dataset="financials",
            last_success_at=observation.fetched_at,
            status="partial" if partial else "ready",
            data_time=values.get("report_date"),
            error=None,
        )
        status = state_status(state, policy, now=now)
    if observation.dataset in {"quotes", "kline"}:
        from types import SimpleNamespace
        from market_data_service.freshness import state_status

        state = SimpleNamespace(
            dataset=observation.dataset,
            last_success_at=observation.fetched_at,
            status="ready",
            data_time=observation.data_time,
            error=None,
        )
        status = state_status(state, policy, now=now)
        if observation.dataset == "kline" and observation.arguments.get("end_date"):
            # Historical windows are not expected to contain today's bar.
            status = "stale" if payload.get("is_stale") is True else "fresh"
    if status != "fresh" and not allow_stale:
        return None
    payload["data_service"] = {
        "status": status,
        "version": observation.id,
        "checked_at": observation.fetched_at.isoformat() + "Z",
        "data_time": observation.data_time,
        "source": observation.source,
    }
    payload["_cached"] = True
    payload["_fetched_at"] = observation.fetched_at.isoformat() + "Z"
    payload.setdefault("data_time", observation.data_time)
    payload.setdefault(
        "data_time_provenance", "source" if observation.data_time else "unavailable"
    )
    payload.setdefault("freshness_unknown", status == "unknown")
    payload.setdefault("is_stale", status == "stale")
    if observation.dataset in {"kline", "quotes"}:
        payload["source_key"] = observation.source
        payload.setdefault(
            "source_scope",
            f"{observation.source}_daily_qfq_kline"
            if observation.dataset == "kline"
            else f"{observation.source}_realtime_quote",
        )
    if status != "fresh":
        payload["is_stale"] = status == "stale"
        payload["freshness_unknown"] = status == "unknown"
        payload["warnings"] = [
            *(payload.get("warnings") or []),
            "数据未达到最新要求，已明确使用历史快照",
        ]
    return payload


def refresh_source(request_key, *, force=False):
    """Redis' native lock supplies cross-process single-flight, not a home-made queue."""
    from redis import Redis
    from redis.exceptions import LockNotOwnedError
    from market_data_service.settings import get_settings

    redis = Redis.from_url(get_settings().broker_url)
    lock = redis.lock(
        f"market-data:source:{request_key}", timeout=190, blocking_timeout=0
    )
    if not lock.acquire(blocking=False):
        redis.close()
        return
    try:
        with get_database().get_session() as session:
            observation = session.scalar(
                select(Observation)
                .where(Observation.request_key == request_key)
                .order_by(Observation.fetched_at.desc())
                .limit(1)
            )
        if not force and observation and observation_payload(observation) is not None:
            return
        return _refresh_source(request_key)
    finally:
        try:
            lock.release()
        except LockNotOwnedError:
            pass
        redis.close()


def _refresh_source(request_key):
    database = get_database()
    with database.get_session() as session:
        subscription = session.get(SourceSubscription, request_key)
        if not subscription:
            return
        operation, arguments = subscription.operation, subscription.arguments
    dataset, fn = registry()[operation]
    started = utcnow()
    from market_data_service.providers.common import force_source_read

    refresh_context = force_source_read.set(True)
    try:
        arguments = dict(arguments)
        parameters = inspect.signature(fn).parameters
        if "use_cache" in parameters:
            arguments["use_cache"] = False
        if "force" in parameters:
            arguments["force"] = True
        if isinstance(arguments.get("body"), dict) and "force" in arguments["body"]:
            arguments["body"] = {**arguments["body"], "force": True}
        from market_data_service.settings import get_settings

        if get_settings().provider == "fixture" and operation not in {
            "kline",
            "financials",
            "quotes",
            "news",
            "announcements",
        }:
            payload = get_provider().operation(operation, arguments)
        elif operation == "kline":
            from market_data_service.acquisition import read_kline

            payload = read_kline(arguments)
        else:
            payload = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(
                fn
            )(**arguments)
        if operation == "financials.fetch_period":
            payload = {
                "success": bool(payload),
                "data": payload,
                "source": "Eastmoney",
                "data_time": arguments["period"],
            }
        if not isinstance(payload, dict):
            payload = {
                "success": True,
                "data": json_value(payload),
                "source": operation,
                "data_time": arguments.get("period"),
            }
        if payload.get("success") is False or (
            payload.get("success") is not True
            and payload.get("errors")
            and not any(
                payload.get(key) for key in ("items", "data", "records", "content_text")
            )
        ):
            raise ValueError("; ".join(payload.get("errors") or ["来源未返回有效数据"]))
        symbol = str(arguments.get("symbol") or "all")
        if symbol != "all" and dataset in {
            "kline",
            "financials",
            "quotes",
            "news",
            "announcements",
        }:
            from market_data_service.providers.common import bare_symbol

            symbol = bare_symbol(symbol)
        with database.session_scope() as session:
            publish(
                session,
                dataset,
                symbol,
                payload,
                started,
                operation=operation,
                arguments=subscription.arguments,
            )
            subscription = session.get(SourceSubscription, request_key)
            subscription.error = None
            from market_data_service.acquisition import next_source_run

            subscription.next_run_at = next_source_run(
                dataset, subscription.interval_seconds
            )
    except Exception as exc:
        with database.session_scope() as session:
            subscription = session.get(SourceSubscription, request_key)
            subscription.error = str(exc)[:1000]
            subscription.next_run_at = utcnow() + timedelta(
                seconds=min(300, subscription.interval_seconds)
            )
        raise
    finally:
        force_source_read.reset(refresh_context)
