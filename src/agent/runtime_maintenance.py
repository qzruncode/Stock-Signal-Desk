# -*- coding: utf-8 -*-
"""Periodic recovery and retention maintenance for the durable Agent runtime."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import os
from typing import Awaitable, Callable

from src.agent.resource_scheduler import (
    ResourceCapacityExceeded,
    agent_resource_lease,
)


logger = logging.getLogger(__name__)


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int((os.getenv(name) or str(default)).strip()))
    except (TypeError, ValueError):
        return default


async def run_agent_runtime_maintenance(
    database,
    recover_callback: Callable[..., Awaitable[int]],
) -> None:
    """Continuously reclaim crashed workers and prune bounded old runtime data."""
    recovery_interval = _positive_int(
        "AGENT_RECOVERY_INTERVAL_SECONDS",
        15,
        minimum=5,
    )
    retention_days = _positive_int("AGENT_RUN_RETENTION_DAYS", 7)
    trace_retention_days = _positive_int("AGENT_TRACE_RETENTION_DAYS", 7)
    prune_interval = _positive_int(
        "AGENT_RETENTION_INTERVAL_SECONDS",
        3600,
        minimum=60,
    )
    next_prune_at = 0.0
    while True:
        try:
            recovered = await recover_callback(database, limit=20)
            if recovered:
                logger.info("[AgentMaintenance] recovered %s interrupted run(s)", recovered)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[AgentMaintenance] recovery sweep failed")

        now_monotonic = asyncio.get_running_loop().time()
        if now_monotonic >= next_prune_at:
            now = datetime.now()
            try:
                async with agent_resource_lease(
                    database,
                    resource_name="maintenance:agent-retention",
                    slots=1,
                    lease_seconds=120.0,
                    wait_timeout_seconds=0.2,
                ):
                    result = await asyncio.to_thread(
                        database.prune_agent_runtime_data,
                        finished_before=now - timedelta(days=retention_days),
                        trace_before=now - timedelta(days=trace_retention_days),
                        limit=1000,
                    )
                if any(result.values()):
                    logger.info("[AgentMaintenance] retention pruned %s", result)
            except ResourceCapacityExceeded:
                # Another worker owns this maintenance interval.
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[AgentMaintenance] retention sweep failed")
            next_prune_at = now_monotonic + prune_interval

        await asyncio.sleep(recovery_interval)


__all__ = ["run_agent_runtime_maintenance"]
