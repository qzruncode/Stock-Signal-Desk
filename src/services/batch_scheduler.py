# -*- coding: utf-8 -*-
"""Small in-process scheduler for the persisted daily batch configuration.

The scheduler is intentionally hosted by the API lifespan rather than by a
request thread.  The persisted time-slot marker makes multiple API workers
safe: only one worker can claim a given local date/time slot.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Awaitable, Callable, Optional

from sqlalchemy.exc import IntegrityError

from src.config import get_config
from src.prompt_templates import get_prompt_template_store
from src.storage import DataMaintenanceJob, DatabaseManager

logger = logging.getLogger(__name__)

_SCHEDULE_DATASET = "batch_schedule"
_SCHEDULE_TARGET = "daily_slot"
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DEFAULT_POLL_SECONDS = 20.0
_SCHEDULE_GRACE_SECONDS = 90


def _normalize_times(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({str(value).strip() for value in values if _TIME_RE.fullmatch(str(value).strip())})


def _due_schedule_slot(now: datetime, times: object) -> str | None:
    """Return the most recent schedule slot still inside the catch-up window.

    Matching only ``now.strftime('%H:%M')`` loses a run when the process is
    started or briefly blocked just after the configured minute. The window
    is intentionally short so a process started much later in the day does
    not unexpectedly replay an old daily run.
    """
    candidates: list[tuple[float, str]] = []
    for value in _normalize_times(times):
        hour, minute = (int(part) for part in value.split(":"))
        scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elapsed = (now - scheduled_at).total_seconds()
        if 0 <= elapsed <= _SCHEDULE_GRACE_SECONDS:
            candidates.append((elapsed, value))
    if not candidates:
        return None
    _, value = min(candidates, key=lambda item: item[0])
    return f"{now:%Y%m%d}-{value}"


def _load_effective_schedule() -> Optional[dict]:
    """Load the UI schedule, falling back to the environment schedule."""
    db = DatabaseManager.get_instance()
    persisted = db.get_batch_schedule()
    if persisted is not None:
        if not persisted.get("enabled"):
            return None
        return {
            "enabled": True,
            "times": _normalize_times(persisted.get("times")),
            "template_id": str(persisted.get("template_id") or "").strip(),
            "source": "database",
            "run_immediately": False,
        }

    config = get_config()
    if not config.schedule_enabled:
        return None
    template = get_prompt_template_store().get_default()
    return {
        "enabled": True,
        "times": _normalize_times([config.schedule_time]),
        "template_id": str((template or {}).get("id") or "").strip(),
        "source": "environment",
        "run_immediately": bool(config.schedule_run_immediately),
    }


def _claim_schedule_slot(slot_key: str) -> bool:
    """Claim one date/time slot using the existing durable uniqueness guard."""
    db = DatabaseManager.get_instance()
    now = datetime.now()
    with db.get_session() as session:
        existing = (
            session.query(DataMaintenanceJob)
            .filter(
                DataMaintenanceJob.dataset == _SCHEDULE_DATASET,
                DataMaintenanceJob.scope_key == slot_key,
                DataMaintenanceJob.target_data_time == _SCHEDULE_TARGET,
            )
            .one_or_none()
        )
        if existing is not None:
            return False
        try:
            session.add(
                DataMaintenanceJob(
                    id=f"schedule-{slot_key}",
                    dataset=_SCHEDULE_DATASET,
                    scope_key=slot_key,
                    target_data_time=_SCHEDULE_TARGET,
                    trigger="scheduler",
                    status="running",
                    message="已领取定时跑批时间槽",
                    started_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False


def _finish_schedule_slot(slot_key: str, *, success: bool, message: str) -> None:
    try:
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            session.query(DataMaintenanceJob).filter(
                DataMaintenanceJob.dataset == _SCHEDULE_DATASET,
                DataMaintenanceJob.scope_key == slot_key,
                DataMaintenanceJob.target_data_time == _SCHEDULE_TARGET,
            ).update(
                {
                    "status": "success" if success else "failed",
                    "message": message,
                    "error": None if success else message,
                    "finished_at": datetime.now(),
                    "updated_at": datetime.now(),
                }
            )
            session.commit()
    except Exception:
        logger.warning("Failed to persist schedule slot result: %s", slot_key, exc_info=True)


def _is_trading_day() -> bool:
    config = get_config()
    if not config.trading_day_check_enabled:
        return True
    from src.tools._trading_calendar import trade_dates

    try:
        return datetime.now().date() in trade_dates()
    except Exception:
        logger.warning("交易日历不可用，跳过定时跑批", exc_info=True)
        return False


async def run_batch_scheduler(
    *,
    stop_event: Optional[asyncio.Event] = None,
    trigger: Optional[Callable[[str], Awaitable[object]]] = None,
    poll_seconds: float = _DEFAULT_POLL_SECONDS,
) -> None:
    """Poll and trigger the configured batch schedule until cancelled."""
    stop_event = stop_event or asyncio.Event()
    immediate_checked = False

    async def trigger_scheduled(template_id: str) -> object:
        if trigger is not None:
            return await trigger(template_id)
        from api.v1.endpoints.batches.run import BatchRunTriggerRequest, trigger_batch_run

        return await trigger_batch_run(
            BatchRunTriggerRequest(
                stock_codes=[],
                template_id=template_id,
                analysis_mode="template",
                triggered_by="scheduled",
            )
        )

    while not stop_event.is_set():
        try:
            schedule = await asyncio.to_thread(_load_effective_schedule)
            if schedule and schedule.get("times") and await asyncio.to_thread(_is_trading_day):
                now = datetime.now()
                due_slot = _due_schedule_slot(now, schedule["times"])
                if (
                    due_slot is None
                    and schedule.get("source") == "environment"
                    and schedule.get("run_immediately")
                    and not immediate_checked
                ):
                    due_slot = f"{now:%Y%m%d}-startup"

                if due_slot:
                    immediate_checked = True
                    claimed = await asyncio.to_thread(_claim_schedule_slot, due_slot)
                    if claimed:
                        template_id = str(schedule.get("template_id") or "").strip()
                        if not template_id:
                            message = "定时跑批未找到可用提示词模板"
                            await asyncio.to_thread(_finish_schedule_slot, due_slot, success=False, message=message)
                        else:
                            try:
                                await trigger_scheduled(template_id)
                            except Exception as exc:
                                logger.warning("Scheduled batch trigger failed: %s", exc, exc_info=True)
                                await asyncio.to_thread(
                                    _finish_schedule_slot,
                                    due_slot,
                                    success=False,
                                    message=str(exc),
                                )
                            else:
                                await asyncio.to_thread(
                                    _finish_schedule_slot,
                                    due_slot,
                                    success=True,
                                    message="定时跑批已启动",
                                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Batch scheduler iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(1.0, float(poll_seconds)))
        except asyncio.TimeoutError:
            continue


__all__ = [
    "run_batch_scheduler",
    "_claim_schedule_slot",
    "_due_schedule_slot",
    "_finish_schedule_slot",
    "_load_effective_schedule",
    "_normalize_times",
]
