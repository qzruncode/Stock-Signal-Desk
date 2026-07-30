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
    wait_timeout_seconds: float,
    run_id: str | None = None,
    step_id: str | None = None,
) -> AsyncIterator[str | None]:
    """Wait boundedly for a shared slot and release it on every exit path."""
    if database is None:
        yield None
        return
    lease_owner = uuid.uuid4().hex
    deadline = asyncio.get_running_loop().time() + max(
        0.1,
        float(wait_timeout_seconds),
    )
    lease_id = None
    while lease_id is None:
        lease_id = await asyncio.to_thread(
            database.try_acquire_agent_resource,
            resource_name=resource_name,
            lease_owner=lease_owner,
            slots=slots,
            lease_seconds=lease_seconds,
            run_id=run_id,
            step_id=step_id,
        )
        if lease_id is not None:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise ResourceCapacityExceeded(
                f"resource capacity exhausted: {resource_name}"
            )
        await asyncio.sleep(0.1)
    try:
        yield lease_id
    finally:
        await asyncio.shield(asyncio.to_thread(
            database.release_agent_resource,
            lease_id,
            lease_owner=lease_owner,
        ))


__all__ = ["ResourceCapacityExceeded", "agent_resource_lease"]
