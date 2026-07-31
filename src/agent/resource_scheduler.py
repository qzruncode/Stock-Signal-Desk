# -*- coding: utf-8 -*-
"""Async facade over database-coordinated resource leases."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import uuid
from typing import AsyncIterator


class ResourceCapacityExceeded(RuntimeError):
    pass


@asynccontextmanager
async def agent_resource_lease(
    database,
    *,
    resource_name: str,
    slots: int,
    lease_seconds: float,
    wait: bool = True,
    run_id: str | None = None,
    step_id: str | None = None,
) -> AsyncIterator[str | None]:
    """Wait for a shared slot without a deadline and release it on every exit."""
    if database is None:
        yield None
        return
    lease_owner = uuid.uuid4().hex
    safe_lease_seconds = max(5.0, float(lease_seconds))
    lease_id = None
    while lease_id is None:
        lease_id = await asyncio.to_thread(
            database.try_acquire_agent_resource,
            resource_name=resource_name,
            lease_owner=lease_owner,
            slots=slots,
            lease_seconds=safe_lease_seconds,
            run_id=run_id,
            step_id=step_id,
        )
        if lease_id is not None:
            break
        if not wait:
            raise ResourceCapacityExceeded(f"resource capacity exhausted: {resource_name}")
        await asyncio.sleep(0.1)
    renew_task: asyncio.Task[None] | None = None

    async def renew_lease() -> None:
        while True:
            await asyncio.sleep(max(1.0, safe_lease_seconds / 3.0))
            renewed_id = await asyncio.to_thread(
                database.try_acquire_agent_resource,
                resource_name=resource_name,
                lease_owner=lease_owner,
                slots=slots,
                lease_seconds=safe_lease_seconds,
                run_id=run_id,
                step_id=step_id,
            )
            if renewed_id != lease_id:
                raise ResourceCapacityExceeded(
                    f"resource lease lost: {resource_name}"
                )

    renew_task = asyncio.create_task(renew_lease())
    try:
        yield lease_id
    finally:
        renew_task.cancel()
        await asyncio.gather(renew_task, return_exceptions=True)
        await asyncio.shield(
            asyncio.to_thread(
                database.release_agent_resource,
                lease_id,
                lease_owner=lease_owner,
            )
        )


__all__ = ["ResourceCapacityExceeded", "agent_resource_lease"]
