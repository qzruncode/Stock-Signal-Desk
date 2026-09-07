"""Durable scheduling, per-security freshness, and immutable observation versions."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from market_data_service.models import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TradingDay(Base):
    __tablename__ = "md_trading_day"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)


class ImportIssue(Base):
    __tablename__ = "md_import_issue"
    id: Mapped[int] = mapped_column(primary_key=True)
    __table_args__ = (UniqueConstraint("table_name", "symbol", "field"),)
    table_name: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(16))
    field: Mapped[str] = mapped_column(String(64))
    raw_value: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(String(200))


class DatasetPolicy(Base):
    __tablename__ = "md_dataset_policy"
    dataset: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    max_age_seconds: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SyncJob(Base):
    __tablename__ = "md_sync_job"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(40), index=True)
    active_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(24), default="manual")
    mode: Mapped[str] = mapped_column(String(24), default="stale")
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    retry_of: Mapped[str | None] = mapped_column(String(36))
    total: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="等待采集进程领取")
    error: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class SyncItem(Base):
    __tablename__ = "md_sync_item"
    __table_args__ = (UniqueConstraint("job_id", "symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("md_sync_job.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DataState(Base):
    __tablename__ = "md_data_state"
    __table_args__ = (UniqueConstraint("dataset", "symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    data_time: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(24), default="missing")
    source: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)


class Observation(Base):
    __tablename__ = "md_observation"
    __table_args__ = (
        Index("ix_md_observation_request_fetched", "request_key", "fetched_at"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    operation: Mapped[str] = mapped_column(String(100), index=True)
    request_key: Mapped[str] = mapped_column(String(64), index=True)
    arguments: Mapped[dict] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(128))
    data_time: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class SourceSubscription(Base):
    __tablename__ = "md_source_subscription"
    __table_args__ = (
        Index("ix_md_subscription_dataset_requested", "dataset", "last_requested_at"),
    )
    request_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(100))
    dataset: Mapped[str] = mapped_column(String(40), index=True)
    arguments: Mapped[dict] = mapped_column(JSON)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    error: Mapped[str | None] = mapped_column(Text)


class ServiceHeartbeat(Base):
    __tablename__ = "md_service_heartbeat"
    component: Mapped[str] = mapped_column(String(40), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    detail: Mapped[str] = mapped_column(String(200), default="")


class ChangeOutbox(Base):
    """Transactional change notification; database triggers cover every writer."""

    __tablename__ = "md_change_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24))
    dataset: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)


class CoverageProjection(Base):
    """Small, indexed read model; never stores observation bodies."""

    __tablename__ = "md_coverage_projection"
    __table_args__ = (
        Index("ix_md_coverage_dataset_status_symbol", "dataset", "status", "symbol"),
        Index("ix_md_coverage_due", "next_check_at"),
    )
    dataset: Mapped[str] = mapped_column(String(40), primary_key=True)
    entity: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24))
    data_time: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime)


class DatasetProjection(Base):
    __tablename__ = "md_dataset_projection"
    dataset: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
