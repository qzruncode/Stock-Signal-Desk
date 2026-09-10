"""Context-budget helpers for the native LangChain Agent loop.

The checkpointer keeps the durable working conversation.  This module only
changes the transient request sent to one model call, so context compaction
does not erase the canonical transcript or the evidence ledger.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.utils.function_calling import convert_to_openai_tool

from src.agent.model_runtime import ModelContextWindowExceededError


DEFAULT_CONTEXT_WINDOW = 200_000
DEFAULT_OUTPUT_TOKENS = 8_000
DEFAULT_SAFETY_TOKENS = 4_096
DEFAULT_RESPONSE_SCHEMA_TOKENS = 4_096


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _profile_value(model: Any, key: str) -> int | None:
    profile = getattr(model, "profile", None)
    if isinstance(profile, Mapping):
        value = profile.get(key)
    else:
        value = getattr(profile, key, None)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def model_context_window(model: Any) -> int:
    """Resolve the input window from the model profile or gateway config."""
    profile_window = _profile_value(model, "max_input_tokens")
    if profile_window is not None:
        return profile_window
    config = getattr(model, "llm_config", None)
    if isinstance(config, Mapping):
        try:
            configured = int(config.get("context_window") or 0)
        except (TypeError, ValueError):
            configured = 0
        if configured > 0:
            return configured
    return _bounded_int(
        "AGENT_CONTEXT_WINDOW_TOKENS",
        DEFAULT_CONTEXT_WINDOW,
        minimum=8_192,
        maximum=2_000_000,
    )


def model_output_reserve(model: Any) -> int:
    """Reserve the provider output allowance before fitting input context."""
    config = getattr(model, "llm_config", None)
    configured = config.get("max_tokens") if isinstance(config, Mapping) else None
    try:
        value = int(configured or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        value = _bounded_int(
            "AGENT_CONTEXT_OUTPUT_TOKENS",
            DEFAULT_OUTPUT_TOKENS,
            minimum=256,
            maximum=128_000,
        )
        # LiteLLMChatModel uses 8,000 as its hard default when the gateway
        # config does not provide max_tokens.  Do not let a smaller reserve
        # make the preflight less conservative than the actual request.
        return max(DEFAULT_OUTPUT_TOKENS, value)
    return value


def model_token_count(
    model: Any,
    messages: Sequence[BaseMessage],
    tools: Sequence[Any] | None = None,
) -> int:
    """Use the model tokenizer when available, with a bounded fallback."""
    try:
        counter = getattr(type(model), "get_num_tokens_from_messages", None)
        if counter is BaseChatModel.get_num_tokens_from_messages:
            raise LookupError("model uses LangChain's optional tokenizer fallback")
        count = int(model.get_num_tokens_from_messages(list(messages), tools))
    except Exception:
        count = count_tokens_approximately(list(messages))
        if tools:
            try:
                encoded_tools = json.dumps(
                    [convert_to_openai_tool(tool) for tool in tools],
                    ensure_ascii=False,
                    default=str,
                )
                count += max(1, len(encoded_tools) // 4)
            except (TypeError, ValueError):
                count += len(tools) * 256
    return max(0, count)


def response_schema_reserve(request: Any) -> int:
    """Count a ToolStrategy schema that is bound after middleware runs."""
    response_format = getattr(request, "response_format", None)
    specs = getattr(response_format, "schema_specs", None)
    if not specs:
        return 0
    payload = []
    for spec in specs:
        schema = getattr(spec, "json_schema", None)
        if schema is not None:
            payload.append(schema)
    if not payload:
        return DEFAULT_RESPONSE_SCHEMA_TOKENS
    try:
        return max(
            256,
            count_tokens_approximately(
                [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_RESPONSE_SCHEMA_TOKENS


@dataclass(frozen=True)
class ContextBudget:
    context_window: int
    output_tokens: int
    safety_tokens: int
    response_schema_tokens: int

    @property
    def input_tokens(self) -> int:
        """Maximum provider input after reserving output and safety space."""
        return max(
            1,
            self.context_window
            - self.output_tokens
            - self.safety_tokens
        )

    @property
    def message_tokens(self) -> int:
        """Maximum messages after reserving the structured-output schema."""
        return max(1, self.input_tokens - self.response_schema_tokens)


class ContextBudgetMiddleware(AgentMiddleware):
    """Fit the final model request without changing persisted graph state."""

    name = "context_budget"

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        model = request.model
        system_messages = [request.system_message] if request.system_message else []
        messages = list(request.messages or [])
        budget = ContextBudget(
            context_window=model_context_window(model),
            output_tokens=model_output_reserve(model),
            safety_tokens=_bounded_int(
                "AGENT_CONTEXT_SAFETY_TOKENS",
                DEFAULT_SAFETY_TOKENS,
                minimum=256,
                maximum=32_000,
            ),
            response_schema_tokens=response_schema_reserve(request),
        )
        input_limit = budget.input_tokens
        total_tokens = model_token_count(
            model,
            [*system_messages, *messages],
            request.tools,
        ) + budget.response_schema_tokens

        if total_tokens <= input_limit:
            return await handler(request)

        fixed_tokens = model_token_count(model, system_messages, request.tools)
        message_budget = budget.message_tokens - fixed_tokens
        trimmed = self._trim_messages(model, messages, message_budget)
        trimmed_total = model_token_count(
            model,
            [*system_messages, *trimmed],
            request.tools,
        ) + budget.response_schema_tokens

        if trimmed_total > input_limit:
            # A second pass closes tokenizer rounding differences and provider
            # schema overhead without inventing a separate message parser.
            trimmed = self._trim_messages(model, trimmed, max(1, message_budget - (trimmed_total - input_limit)))
            trimmed_total = model_token_count(
                model,
                [*system_messages, *trimmed],
                request.tools,
            ) + budget.response_schema_tokens

        if not trimmed or trimmed_total > input_limit:
            raise ModelContextWindowExceededError(
                context_window=budget.context_window,
                estimated_input_tokens=total_tokens,
                message_count=len(messages),
            )

        runtime = getattr(request, "runtime", None)
        events = getattr(getattr(runtime, "context", None), "events", None)
        if events is not None:
            events.stage(
                "context",
                "compacted",
                "已按模型上下文预算压缩本次请求",
                details={
                    "context_window": budget.context_window,
                    "input_budget": input_limit,
                    "estimated_before": total_tokens,
                    "estimated_after": trimmed_total,
                    "messages_before": len(messages),
                    "messages_after": len(trimmed),
                },
            )
        return await handler(request.override(messages=trimmed))

    @staticmethod
    def _trim_messages(model: Any, messages: list[BaseMessage], max_tokens: int) -> list[BaseMessage]:
        if not messages or max_tokens <= 0:
            return []
        summary_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if _is_summarization_message(messages[index])
            ),
            None,
        )
        if summary_index is not None:
            summary = messages[summary_index]
            summary_tokens = model_token_count(model, [summary])
            if summary_tokens < max_tokens:
                tail = _trim_message_tail(
                    model,
                    messages[summary_index + 1 :],
                    max_tokens - summary_tokens,
                )
                anchored = [summary, *tail]
                if tail and model_token_count(model, anchored) <= max_tokens:
                    return anchored

        return _trim_message_tail(model, messages, max_tokens)


def _is_summarization_message(message: BaseMessage) -> bool:
    additional_kwargs = getattr(message, "additional_kwargs", None)
    return isinstance(additional_kwargs, Mapping) and additional_kwargs.get("lc_source") == "summarization"


def _trim_message_tail(model: Any, messages: Sequence[BaseMessage], max_tokens: int) -> list[BaseMessage]:
    if not messages or max_tokens <= 0:
        return []
    return list(
        trim_messages(
            list(messages),
            max_tokens=max_tokens,
            token_counter=lambda candidate: model_token_count(model, candidate),
            strategy="last",
            allow_partial=False,
            start_on="human",
            end_on=("human", "tool"),
        )
    )


__all__ = [
    "ContextBudget",
    "ContextBudgetMiddleware",
    "model_context_window",
    "model_output_reserve",
    "model_token_count",
]
