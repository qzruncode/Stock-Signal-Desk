# -*- coding: utf-8 -*-
"""Shared model-provider reliability boundary for Agent runs."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any, Awaitable, Callable

from src.agent.resource_scheduler import agent_resource_lease
from src.agent.runtime_safety import get_agent_runtime_limits


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _is_transient_provider_error(error: BaseException) -> bool:
    status = getattr(error, "status_code", None)
    if status in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    name = type(error).__name__.lower()
    text = str(error).lower()
    markers = (
        "timeout",
        "connection",
        "rate limit",
        "ratelimit",
        "service unavailable",
        "temporarily unavailable",
        "internal server",
        "overloaded",
    )
    return any(marker in name or marker in text for marker in markers)


class ManagedModelStream:
    """Release provider capacity and update its circuit when a stream ends."""

    def __init__(
        self,
        response: Any,
        *,
        on_close: Callable[[BaseException | None], Any],
    ) -> None:
        self._iterator = response.__aiter__()
        self._on_close = on_close
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await anext(self._iterator)
        except StopAsyncIteration:
            await self._close(None)
            raise
        except BaseException as exc:
            await self._close(exc)
            raise

    async def _close(self, error: BaseException | None) -> None:
        if self._closed:
            return
        self._closed = True
        result = self._on_close(error)
        if inspect.isawaitable(result):
            await result

    async def aclose(self) -> None:
        closer = getattr(self._iterator, "aclose", None)
        try:
            if callable(closer):
                await closer()
        finally:
            await self._close(None)


class GuardedModelRuntime:
    """Budget, retry, shared capacity and circuit policy for one Agent run."""

    def __init__(
        self,
        *,
        database: Any | None,
        run_id: str,
        worker_id: str,
        model: str,
        token_estimator: Callable[[list[dict[str, Any]], str], int],
    ) -> None:
        self.database = database
        self.run_id = run_id
        self.worker_id = worker_id
        self.model = model or "default"
        self.token_estimator = token_estimator

    async def complete(
        self,
        completion: Callable[..., Awaitable[Any]],
        **kwargs: Any,
    ) -> Any:
        """Start a model response, retrying only pre-stream transient failures."""
        provider_resource = f"llm:{self.model[:120]}"
        max_attempts = _env_int(
            "AGENT_PROVIDER_MAX_ATTEMPTS",
            2,
            minimum=1,
            maximum=4,
        )
        backoff = _env_float(
            "AGENT_PROVIDER_RETRY_BACKOFF_SECONDS",
            0.5,
            minimum=0.0,
            maximum=30.0,
        )
        last_error: BaseException | None = None

        for attempt in range(1, max_attempts + 1):
            await self._reserve_budget(kwargs)
            if self.database is not None:
                circuit = await asyncio.to_thread(
                    self.database.agent_circuit_before_request,
                    provider_resource,
                    worker_id=self.worker_id,
                )
                if not circuit.get("allowed"):
                    raise RuntimeError("model provider circuit is open")

            stream_timeout = _env_float(
                "AGENT_MODEL_STREAM_TIMEOUT_SECONDS",
                180.0,
                minimum=1.0,
                maximum=14_400.0,
            )
            lease_manager = agent_resource_lease(
                self.database,
                resource_name=provider_resource,
                slots=_env_int(
                    "AGENT_PROVIDER_GLOBAL_CONCURRENCY",
                    4,
                    minimum=1,
                    maximum=64,
                ),
                lease_seconds=stream_timeout + 15.0,
                wait_timeout_seconds=_env_float(
                    "AGENT_PROVIDER_CAPACITY_WAIT_SECONDS",
                    30.0,
                    minimum=0.1,
                    maximum=300.0,
                ),
                run_id=self.run_id,
                step_id=f"model:{attempt}",
            )
            await lease_manager.__aenter__()
            released = False

            async def finalize(error: BaseException | None) -> None:
                nonlocal released
                if released:
                    return
                released = True
                try:
                    if self.database is not None:
                        if error is None or isinstance(
                            error,
                            asyncio.CancelledError,
                        ):
                            await asyncio.to_thread(
                                self.database.record_agent_circuit_success,
                                provider_resource,
                            )
                        else:
                            await asyncio.to_thread(
                                self.database.record_agent_circuit_failure,
                                provider_resource,
                                error=f"{type(error).__name__}: {error}",
                            )
                finally:
                    await lease_manager.__aexit__(
                        type(error) if error is not None else None,
                        error,
                        error.__traceback__ if error is not None else None,
                    )

            try:
                response = await completion(**kwargs)
            except asyncio.CancelledError as exc:
                await finalize(exc)
                raise
            except BaseException as exc:
                last_error = exc
                await finalize(exc)
                if attempt >= max_attempts or not _is_transient_provider_error(exc):
                    raise
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))
                continue

            if hasattr(response, "__aiter__"):
                return ManagedModelStream(response, on_close=finalize)
            await finalize(None)
            return response

        assert last_error is not None
        raise last_error

    async def _reserve_budget(self, kwargs: dict[str, Any]) -> None:
        if self.database is None:
            return
        estimated_tokens = self.token_estimator(
            list(kwargs.get("messages") or []),
            self.model,
        ) + max(0, int(kwargs.get("max_tokens") or 0))
        micros_per_1k = _env_int(
            "AGENT_ESTIMATED_MICROS_PER_1K_TOKENS",
            5_000,
            minimum=0,
            maximum=10_000_000,
        )
        limits = get_agent_runtime_limits()
        budget = await asyncio.to_thread(
            self.database.reserve_agent_run_budget,
            self.run_id,
            provider_calls=1,
            estimated_tokens=estimated_tokens,
            estimated_cost_micros=int(estimated_tokens / 1000.0 * micros_per_1k),
            max_provider_calls=limits.max_provider_calls,
            max_estimated_tokens=limits.max_estimated_tokens,
            max_estimated_cost_micros=limits.max_estimated_cost_micros,
        )
        if not budget.get("allowed") and budget.get("reason") != "run_not_found":
            raise RuntimeError("Agent provider budget exceeded: " f"{budget.get('reason')}")


__all__ = [
    "GuardedModelRuntime",
    "ManagedModelStream",
]
