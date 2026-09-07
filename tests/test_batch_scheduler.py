from __future__ import annotations

import asyncio
from datetime import datetime

from src.services import batch_scheduler as scheduler
from types import SimpleNamespace
from unittest.mock import Mock


def test_normalize_times_filters_invalid_and_deduplicates():
    assert scheduler._normalize_times(["09:30", "09:30", " 18:00 ", "25:00", None]) == ["09:30", "18:00"]


def test_scheduler_uses_calendar_and_fails_closed(monkeypatch):
    import src.tools._trading_calendar as calendar
    monkeypatch.setattr(scheduler, "get_config", lambda: SimpleNamespace(trading_day_check_enabled=True))
    monkeypatch.setattr(calendar, "trade_dates", lambda: [])
    assert scheduler._is_trading_day() is False
    monkeypatch.setattr(calendar, "trade_dates", lambda: [datetime.now().date()])
    assert scheduler._is_trading_day() is True
    monkeypatch.setattr(calendar, "trade_dates", Mock(side_effect=RuntimeError("offline")))
    assert scheduler._is_trading_day() is False


def test_due_schedule_slot_catches_a_short_polling_delay_but_not_a_late_start():
    assert scheduler._due_schedule_slot(datetime(2026, 8, 12, 18, 1, 20), ["18:00"]) == (
        "20260812-18:00"
    )
    assert scheduler._due_schedule_slot(datetime(2026, 8, 12, 18, 1, 31), ["18:00"]) is None


def test_scheduler_triggers_environment_startup_once(monkeypatch):
    calls: list[str] = []
    finished: list[tuple[str, bool]] = []
    stop_event = asyncio.Event()

    monkeypatch.setattr(
        scheduler,
        "_load_effective_schedule",
        lambda: {
            "enabled": True,
            "times": ["00:00"],
            "template_id": "template-1",
            "source": "environment",
            "run_immediately": True,
        },
    )
    monkeypatch.setattr(scheduler, "_is_trading_day", lambda: True)
    monkeypatch.setattr(scheduler, "_claim_schedule_slot", lambda slot: True)
    monkeypatch.setattr(
        scheduler,
        "_finish_schedule_slot",
        lambda slot, *, success, message: finished.append((slot, success)),
    )

    async def trigger(template_id: str):
        calls.append(template_id)
        stop_event.set()

    asyncio.run(
        scheduler.run_batch_scheduler(
            stop_event=stop_event,
            trigger=trigger,
            poll_seconds=1,
        )
    )

    assert calls == ["template-1"]
    assert finished and finished[0][1] is True


def test_scheduler_does_not_run_on_weekend(monkeypatch):
    stop_event = asyncio.Event()
    calls: list[str] = []

    monkeypatch.setattr(
        scheduler,
        "_load_effective_schedule",
        lambda: {
            "enabled": True,
            "times": [datetime.now().strftime("%H:%M")],
            "template_id": "template-1",
            "source": "database",
            "run_immediately": False,
        },
    )
    monkeypatch.setattr(scheduler, "_is_trading_day", lambda: False)

    async def trigger(template_id: str):
        calls.append(template_id)

    async def stop_soon():
        await asyncio.sleep(0.01)
        stop_event.set()

    async def run_test():
        await asyncio.gather(
            scheduler.run_batch_scheduler(stop_event=stop_event, trigger=trigger, poll_seconds=1),
            stop_soon(),
        )

    asyncio.run(run_test())
    assert calls == []
