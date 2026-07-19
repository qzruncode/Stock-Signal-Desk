"""Shared helpers for stateful Agent workflow tools."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def envelope(**payload: Any) -> dict[str, Any]:
    return {
        "success": True,
        "partial": False,
        **payload,
        "data_time": now_iso(),
        "is_stale": False,
        "freshness_unknown": False,
        "errors": [],
        "warnings": [],
    }


def model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def run_async(awaitable: Awaitable[Any]) -> Any:
    """Workflow tools run in a worker thread, so a private event loop is safe."""
    return asyncio.run(awaitable)


def require_confirmation(confirmed: bool, action: str) -> None:
    if not confirmed:
        raise ValueError(f"{action} 是有副作用的操作，必须在用户明确确认后传 confirmed=true")


__all__ = ["envelope", "model_dump", "now_iso", "require_confirmation", "run_async"]
