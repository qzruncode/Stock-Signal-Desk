"""Single elected relay: PostgreSQL NOTIFY -> projections -> Redis Streams.

Notifications are just wake-ups. The transactional outbox is authoritative and
survives process/Redis outages. No HTTP client causes an outbox or database poll.
"""

import logging
import signal
import threading
import time
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, func, select, update

from market_data_service.control_models import (
    ChangeOutbox,
    CoverageProjection,
    DatasetProjection,
    utcnow,
)
from market_data_service.database import get_database
from market_data_service.events import publish_event
from market_data_service.projections import SUBSCRIBED, refresh_projections
from market_data_service.schemas import DATASETS
from market_data_service.settings import get_settings

logger = logging.getLogger(__name__)


def schedule_transitions(database):
    """Persist time-driven changes before changing their projection deadlines."""
    now = utcnow()
    with database.session_scope() as session:
        missing = set(DATASETS) - set(
            session.scalars(select(DatasetProjection.dataset))
        )
        due = session.execute(
            select(CoverageProjection.dataset, CoverageProjection.entity).where(
                CoverageProjection.next_check_at <= now
            )
        ).all()
        session.add_all(
            [
                ChangeOutbox(
                    kind="policy", dataset=dataset, entity=dataset, created_at=now
                )
                for dataset in missing
            ]
        )
        session.add_all(
            [
                ChangeOutbox(
                    kind="subscription" if dataset in SUBSCRIBED else "state",
                    dataset=dataset,
                    entity=entity,
                    created_at=now,
                )
                for dataset, entity in due
            ]
        )
    return now


def drain(database, *, publisher=publish_event):
    now = schedule_transitions(database)
    with database.get_session() as session:
        events = list(
            session.scalars(
                select(ChangeOutbox)
                .where(ChangeOutbox.dispatched_at.is_(None))
                .order_by(ChangeOutbox.id)
                .limit(10000)
            )
        )
    if not events:
        return False
    # The same cutoff is used for staging and projection. A deadline crossing
    # between these steps must not be applied without a durable outbox record.
    payload = refresh_projections(database, events, now=now)
    states, observations = defaultdict(set), defaultdict(set)
    for event in events:
        if event.kind == "calendar" or (
            event.dataset == "calendar" and event.kind in {"policy", "state"}
        ):
            for dataset in DATASETS:
                states[dataset].add("*")
        elif event.kind == "state":
            states[event.dataset].add(event.entity)
        elif event.kind == "observation":
            observations[event.dataset].add(event.entity)
        elif event.kind in {"calendar", "policy"}:
            states[event.dataset].add("*")
    compact = lambda mapping: {
        key: sorted(values) if len(values) <= 200 else ["*"]
        for key, values in mapping.items()
    }
    from market_data_service.api import health

    payload.update(
        states=compact(states), observations=compact(observations), service=health()
    )
    publisher(payload)  # If delivery fails, committed outbox records remain pending.
    with database.session_scope() as session:
        session.execute(
            update(ChangeOutbox)
            .where(ChangeOutbox.id.in_([event.id for event in events]))
            .values(dispatched_at=utcnow())
        )
    return len(events) == 10000


def next_deadline(database):
    with database.get_session() as session:
        due = session.scalar(select(func.min(CoverageProjection.next_check_at)))
    return min(30, max(0.1, (due - utcnow()).total_seconds())) if due else 30


def run():
    import psycopg

    settings, database, stop = get_settings(), get_database(), threading.Event()
    database.initialize()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    backoff = 1
    while not stop.is_set():
        try:
            if database._is_sqlite_engine:
                # SQLite is development-only; cross-process LISTEN requires PG.
                # Its single relay checks the outbox, never one loop per reader.
                while not stop.is_set():
                    drain(database)
                    database.heartbeat("events", "开发模式事件维护")
                    stop.wait(max(1, settings.event_batch_seconds))
                return
            url = database.engine.url.set(drivername="postgresql").render_as_string(
                hide_password=False
            )
            with psycopg.connect(
                url,
                autocommit=True,
                connect_timeout=5,
                application_name="market-data-event-relay",
            ) as connection:
                if not connection.execute(
                    "SELECT pg_try_advisory_lock(738190165)"
                ).fetchone()[0]:
                    stop.wait(5)
                    continue
                # LISTEN commits before the initial outbox read, closing the
                # subscribe/snapshot race documented by PostgreSQL.
                connection.execute("LISTEN md_changes")
                last_health, last_cleanup = 0.0, 0.0
                while not stop.is_set():
                    now = time.monotonic()
                    if now - last_health >= 30:
                        database.heartbeat("events", "变更推送与查询汇总运行中")
                        last_health = now
                    if now - last_cleanup >= 3600:
                        with database.session_scope() as session:
                            session.execute(
                                delete(ChangeOutbox).where(
                                    ChangeOutbox.dispatched_at
                                    < utcnow() - timedelta(days=7)
                                )
                            )
                        last_cleanup = now
                    more = drain(database)
                    backoff = 1
                    if more:
                        continue
                    # A deadline wakes only the shared expiry/health maintenance.
                    # Database commits wake this listener immediately otherwise.
                    for _ in connection.notifies(
                        timeout=next_deadline(database), stop_after=1
                    ):
                        break
                    stop.wait(settings.event_batch_seconds)
        except Exception:
            logger.exception("事件投递暂不可用；保留待发送记录并退避恢复")
            stop.wait(backoff)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
