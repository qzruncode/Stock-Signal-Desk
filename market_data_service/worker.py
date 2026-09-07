"""Celery beat/worker deployment, independent of the business application's lifespan."""

from datetime import timedelta
import logging

from celery import Celery
from celery.signals import worker_ready
from sqlalchemy import select, update

from market_data_service.control_models import (
    DatasetPolicy,
    SourceSubscription,
    SyncJob,
    utcnow,
)
from market_data_service.database import get_database
from market_data_service.jobs import enqueue, run_job
from market_data_service.models import StockMeta
from market_data_service.schemas import SyncRequest
from market_data_service.settings import get_settings

settings = get_settings()
celery_app = Celery("market_data", broker=settings.broker_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_default_queue="market-data",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_soft_shutdown_timeout=20.0,
    worker_enable_soft_shutdown_on_idle=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 25200},
    visibility_timeout=25200,
    task_ignore_result=True,
    beat_schedule={
        "maintain-market-data": {
            "task": "market_data.tick",
            "schedule": settings.scheduler_seconds,
        },
        "sync-worker-health": {
            "task": "market_data.sync_heartbeat",
            "schedule": 30,
            "options": {"queue": "market-data-sync"},
        },
    },
)


@worker_ready.connect
def initialize_worker(sender=None, **_):
    get_database().initialize()
    component = "sync-worker" if sender and "sync" in sender.hostname else "worker"
    get_database().heartbeat(component, "采集进程已启动")


@celery_app.task(name="market_data.sync_heartbeat")
def sync_heartbeat():
    get_database().heartbeat("sync-worker", "同步队列可用")


@celery_app.task(
    name="market_data.sync",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=21540,
    time_limit=21600,
)
def sync_task(job_id):
    run_job(job_id)


@celery_app.task(
    name="market_data.source",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
    soft_time_limit=170,
    time_limit=180,
)
def source_task(request_key):
    from market_data_service.sources import refresh_source

    get_database().heartbeat("worker", "按需来源刷新")
    # Beat dispatches at the configured interval, before max_age expires.
    refresh_source(request_key, force=True)


@celery_app.task(name="market_data.tick", soft_time_limit=50, time_limit=60)
def tick():
    """Reconcile durable work and policies; no business process must be running."""
    database, now = get_database(), utcnow()
    database.heartbeat("scheduler", "自动维护运行中")
    database.heartbeat("worker", "采集进程可用")
    with database.get_session() as session:
        policies = list(
            session.scalars(
                select(DatasetPolicy).where(
                    DatasetPolicy.enabled.is_(True), DatasetPolicy.next_run_at <= now
                )
            )
        )
        has_master = session.scalar(select(StockMeta.code).limit(1)) is not None
        subscribed = set(
            session.scalars(
                select(SourceSubscription.dataset)
                .where(SourceSubscription.last_requested_at >= now - timedelta(days=7))
                .distinct()
            )
        )
    with database.session_scope() as session:
        session.execute(
            update(SyncJob)
            .where(
                SyncJob.status == "running",
                SyncJob.cancel_requested.is_(True),
                SyncJob.lease_until < now,
            )
            .values(
                status="cancelled",
                active_key=None,
                finished_at=now,
                message="取消已完成；中断前写入的数据已保留",
            )
        )
    for policy in policies:
        if not has_master and policy.dataset not in {"calendar", "securities"}:
            continue
        try:
            if (
                policy.dataset in {"calendar", "securities", "financials", "kline"}
                or policy.dataset in subscribed
            ):
                enqueue(SyncRequest(dataset=policy.dataset), trigger="scheduled")
            with database.session_scope() as session:
                session.execute(
                    update(DatasetPolicy)
                    .where(
                        DatasetPolicy.dataset == policy.dataset,
                        DatasetPolicy.next_run_at == policy.next_run_at,
                    )
                    .values(
                        next_run_at=now + timedelta(seconds=policy.interval_seconds)
                    )
                )
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not schedule dataset %s", policy.dataset
            )
    with database.get_session() as session:
        jobs = list(
            session.scalars(
                select(SyncJob).where(
                    (
                        (SyncJob.status == "queued")
                        & (
                            (SyncJob.dispatched_at.is_(None))
                            | (SyncJob.dispatched_at < now - timedelta(seconds=120))
                        )
                    )
                    | ((SyncJob.status == "running") & (SyncJob.lease_until < now))
                )
            )
        )
    for job in jobs:
        sync_task.apply_async(args=[job.id], task_id=job.id, queue="market-data-sync")
        with database.session_scope() as session:
            session.execute(
                update(SyncJob).where(SyncJob.id == job.id).values(dispatched_at=now)
            )
    with database.session_scope() as session:
        subscriptions = list(
            session.scalars(
                select(SourceSubscription)
                .join(
                    DatasetPolicy, DatasetPolicy.dataset == SourceSubscription.dataset
                )
                .where(
                    DatasetPolicy.enabled.is_(True),
                    SourceSubscription.next_run_at <= now,
                    SourceSubscription.last_requested_at >= now - timedelta(days=7),
                )
                .order_by(SourceSubscription.next_run_at)
                .limit(100)
            )
        )
        for subscription in subscriptions:
            claimed = session.execute(
                update(SourceSubscription)
                .where(
                    SourceSubscription.request_key == subscription.request_key,
                    SourceSubscription.next_run_at == subscription.next_run_at,
                )
                .values(next_run_at=now + timedelta(seconds=180))
            )
            if claimed.rowcount:
                source_task.apply_async(
                    args=[subscription.request_key], queue="market-data"
                )
