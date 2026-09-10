# -*- coding: utf-8 -*-
"""Shared model-provider reliability boundary for Agent runs."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any, Awaitable, Callable

from src.agent.resource_scheduler import agent_resource_lease


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
        "connect",
        "network",
        "rate limit",
        "ratelimit",
        "service unavailable",
        "temporarily unavailable",
        "internal server",
        "overloaded",
    )
    return any(marker in name or marker in text for marker in markers)


def _is_provider_reported_timeout(error: BaseException) -> bool:
    name = type(error).__name__.lower()
    text = str(error).lower()
    return "timeout" in name or "timeout" in text or "timed out" in text


def _is_context_window_error(error: BaseException) -> bool:
    """Recognize provider errors that mean the request cannot fit its window."""
    name = type(error).__name__.lower()
    text = str(error).lower()
    markers = (
        "context window",
        "context_window",
        "contextwindow",
        "context length",
        "maximum context",
        "max context",
        "prompt is too long",
        "prompt too long",
        "too many tokens",
        "input tokens exceed",
        "exceeds the model's maximum",
    )
    return any(marker in name or marker in text for marker in markers)


class ModelProviderReportedTimeoutError(RuntimeError):
    """The upstream provider reported a timeout; no local deadline is applied."""


class ModelProviderUnavailableError(RuntimeError):
    """The upstream provider or its circuit is temporarily unavailable.

    This deliberately represents an upstream availability result, not a local
    analysis deadline.  The graph can therefore close safely as ``partial``
    without pretending that a completed answer was produced.
    """


class ModelContextWindowExceededError(RuntimeError):
    """The model request cannot fit the configured input and output budget."""

    def __init__(
        self,
        *,
        context_window: int,
        estimated_input_tokens: int,
        message_count: int,
    ) -> None:
        self.context_window = int(context_window)
        self.estimated_input_tokens = int(estimated_input_tokens)
        self.message_count = int(message_count)
        super().__init__(
            "model context window exceeded "
            f"(window={self.context_window}, estimated_input={self.estimated_input_tokens}, "
            f"messages={self.message_count})"
        )


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
        model_name = str(model or "").strip()
        if not model_name:
            raise ValueError("model must be provided by the configured model settings")
        self.model = model_name
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
                    raise ModelProviderUnavailableError("model provider circuit is open")

            lease_manager = agent_resource_lease(
                self.database,
                resource_name=provider_resource,
                slots=_env_int(
                    "AGENT_PROVIDER_GLOBAL_CONCURRENCY",
                    4,
                    minimum=1,
                    maximum=64,
                ),
                lease_seconds=120.0,
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
                        if error is None or isinstance(error, asyncio.CancelledError):
                            await asyncio.to_thread(
                                self.database.record_agent_circuit_success,
                                provider_resource,
                            )
                        elif _is_transient_provider_error(error):
                            await asyncio.to_thread(
                                self.database.record_agent_circuit_failure,
                                provider_resource,
                                error=f"{type(error).__name__}: {error}",
                            )
                        else:
                            # Validation/configuration and other deterministic
                            # failures do not prove provider unavailability.
                            # They must not accumulate until the provider
                            # circuit blocks otherwise healthy requests.
                            await asyncio.to_thread(
                                self.database.record_agent_circuit_success,
                                provider_resource,
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
                if _is_context_window_error(exc):
                    error = ModelContextWindowExceededError(
                        context_window=0,
                        estimated_input_tokens=0,
                        message_count=0,
                    )
                    last_error = error
                    await finalize(error)
                    raise error from exc
                error: BaseException = (
                    ModelProviderReportedTimeoutError(
                        "upstream model provider reported timeout"
                    )
                    if _is_provider_reported_timeout(exc)
                    else exc
                )
                last_error = error
                await finalize(error)
                if attempt >= max_attempts or not _is_transient_provider_error(exc):
                    if isinstance(error, ModelProviderReportedTimeoutError):
                        raise error from exc
                    if _is_transient_provider_error(exc):
                        raise ModelProviderUnavailableError(
                            "model provider is temporarily unavailable"
                        ) from exc
                    if error is not exc:
                        raise error from exc
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
        """Record provider usage without making model completion a token gate.

        Tool-call/graph safeguards bound runaway work at the orchestration
        layer.  Provider token and cost estimates are accounting telemetry,
        not a reason to terminate an otherwise valid answer halfway through.
        The database API is still used so existing run usage reporting remains
        intact; omitting the optional maxima deliberately disables the old
        aggregate token/cost rejection path.
        """
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
        budget = await asyncio.to_thread(
            self.database.reserve_agent_run_budget,
            self.run_id,
            provider_calls=1,
            estimated_tokens=estimated_tokens,
            estimated_cost_micros=int(estimated_tokens / 1000.0 * micros_per_1k),
        )
        # A run can finish between the provider request and this accounting
        # write.  That historical race is harmless; provider availability and
        # graph/tool limits remain the actual execution guards.
        if not budget.get("allowed") and budget.get("reason") != "run_not_found":
            return


__all__ = [
    "GuardedModelRuntime",
    "ManagedModelStream",
    "ModelContextWindowExceededError",
    "ModelProviderReportedTimeoutError",
    "ModelProviderUnavailableError",
]
