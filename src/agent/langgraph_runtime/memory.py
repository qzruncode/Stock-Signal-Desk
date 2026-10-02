"""Run-scoped binding of LangChain's maintained summarization middleware."""

import logging
import os
from copy import deepcopy
from typing import Any

from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.messages.base import merge_content
from langchain_core.messages.utils import get_buffer_string, trim_messages

from .context import (
    DEFAULT_SAFETY_TOKENS,
    model_context_window,
    model_output_reserve,
    model_token_count,
)


_DEFAULT_SUMMARY_TRIGGER_TOKENS = 60_000
_DEFAULT_SUMMARY_KEEP_MESSAGES = 20
_DEFAULT_SUMMARY_PROMPT_RESERVE_TOKENS = 2_048
_SUMMARY_OMISSION = "\n\n[中间内容已省略]\n\n"
_SUMMARY_PROMPT = """请压缩下面这段历史对话，供同一个会话后续继续使用。

必须保留：
1. 用户明确提出的目标、约束、偏好和授权边界；
2. 已经确认的决定、未完成事项和下一步；
3. 已核验的事实、数据时间、证据 ID、来源引用及其不确定性；
4. 工具调用形成的关键结论，以及仍然需要读取或核验的内容。

不要补充历史中没有的信息，不要输出隐藏推理过程，不要删除会影响后续回答的否定条件。
用简洁的结构化文本输出，保留原始标识符和数字口径。

历史对话：
{messages}
"""


class SummaryFailureCallback(BaseCallbackHandler):
    """Observe the public model callback and reject empty native summaries."""

    failed = False
    generated = False

    def on_llm_error(self, error, **kwargs):
        self.failed = True

    def on_llm_end(self, response, **kwargs):
        if not response.generations or not response.generations[0]:
            self.failed = True
            return
        message = getattr(response.generations[0][0], "message", None)
        text = str(getattr(message, "text", "") or "").strip() if message is not None else ""
        if not text or text.startswith("Error generating summary:"):
            self.failed = True
        else:
            self.generated = True


def _configured_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _summary_trigger(model) -> int | None:
    configured = str(os.getenv("AGENT_SUMMARY_TRIGGER_TOKENS", "") or "").strip()
    if configured:
        try:
            value = int(configured)
        except ValueError:
            logging.getLogger(__name__).warning(
                "Invalid AGENT_SUMMARY_TRIGGER_TOKENS; leaving messages intact"
            )
            return None
        if value <= 0:
            return 0
        return max(1, min(1_000_000, value))
    window = model_context_window(model)
    return max(8_000, min(_DEFAULT_SUMMARY_TRIGGER_TOKENS, int(window * 0.30)))


def _summary_keep() -> tuple[str, int]:
    keep_tokens = str(os.getenv("AGENT_SUMMARY_KEEP_TOKENS", "") or "").strip()
    if keep_tokens:
        return (
            "tokens",
            _configured_int("AGENT_SUMMARY_KEEP_TOKENS", 12_000, minimum=1_000, maximum=200_000),
        )
    return (
        "messages",
        _configured_int(
            "AGENT_SUMMARY_KEEP_MESSAGES",
            _DEFAULT_SUMMARY_KEEP_MESSAGES,
            minimum=2,
            maximum=100,
        ),
    )


def _summary_input_budget(model: Any) -> int:
    context_window = model_context_window(model)
    safety_tokens = _configured_int(
        "AGENT_CONTEXT_SAFETY_TOKENS",
        DEFAULT_SAFETY_TOKENS,
        minimum=256,
        maximum=32_000,
    )
    prompt_reserve = _configured_int(
        "AGENT_SUMMARY_PROMPT_RESERVE_TOKENS",
        _DEFAULT_SUMMARY_PROMPT_RESERVE_TOKENS,
        minimum=256,
        maximum=16_000,
    )
    return max(
        0,
        context_window - model_output_reserve(model) - safety_tokens - prompt_reserve,
    )


def _summary_trim_limit(model: Any) -> int:
    dynamic_limit = _summary_input_budget(model)
    raw = str(os.getenv("AGENT_SUMMARY_TRIM_TOKENS", "") or "").strip()
    if not raw:
        return dynamic_limit
    try:
        value = int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Invalid AGENT_SUMMARY_TRIM_TOKENS; using the model-window budget"
        )
        return dynamic_limit
    if value <= 0:
        return dynamic_limit
    return min(dynamic_limit, max(256, min(200_000, value)))


class ContextAwareSummarizationMiddleware(SummarizationMiddleware):
    """Keep an early and a recent slice for the native summary call.

    LangChain owns the trigger, cutoff, reducer, and summary generation.  The
    only application-specific policy here is selecting both ends of the
    evicted segment so an early user constraint is not silently discarded.
    """

    def _summary_prompt(self, messages: list[AnyMessage]) -> str:
        """Render the same XML prompt shape used by native summarization."""
        formatted_messages = get_buffer_string(messages, format="xml")
        return self.summary_prompt.format(messages=formatted_messages).rstrip()

    def _summary_prompt_budget(self) -> int:
        context_window = model_context_window(self.model)
        safety_tokens = _configured_int(
            "AGENT_CONTEXT_SAFETY_TOKENS",
            DEFAULT_SAFETY_TOKENS,
            minimum=256,
            maximum=32_000,
        )
        return max(0, context_window - model_output_reserve(self.model) - safety_tokens)

    def _summary_prompt_tokens(self, messages: list[AnyMessage]) -> int:
        return model_token_count(
            self.model,
            [HumanMessage(content=self._summary_prompt(messages))],
        )

    def _select_head_and_tail(
        self,
        messages: list[AnyMessage],
        limit: int,
    ) -> list[AnyMessage]:
        if limit <= 0:
            return []
        try:
            # Reserve the join marker before selecting slices. With a small
            # budget a 256-character chunk can consume the entire head quota;
            # the native trimmer's callable splitter supports character-level
            # boundaries and owns both token counting and binary search.
            content_budget = limit - self.token_counter([HumanMessage(content=_SUMMARY_OMISSION)])
            if content_budget <= 0:
                return []
            head_budget = max(1, content_budget // 4)
            head = list(
                trim_messages(
                    messages,
                    max_tokens=head_budget,
                    token_counter=self.token_counter,
                    strategy="first",
                    allow_partial=True,
                    text_splitter=list,
                )
            )
            head_tokens = self.token_counter(head)
            tail = list(
                trim_messages(
                    messages,
                    max_tokens=max(1, content_budget - head_tokens),
                    token_counter=self.token_counter,
                    strategy="last",
                    allow_partial=True,
                    text_splitter=list,
                )
            )
            # These excerpts are serialized into a summary prompt, not sent
            # as chat history. Do not drop a recent tool result to force a
            # human boundary, and never summarize just one end on failure.
            if not head or not tail:
                return []
            positions = {_message_key(message): index for index, message in enumerate(messages)}
            selected: dict[tuple[str, str | int], AnyMessage] = {}
            for message in [*head, *tail]:
                key = _message_key(message)
                previous = selected.get(key)
                selected[key] = (
                    message
                    if previous is None
                    else _merge_message_slices(previous, message)
                )
            merged = [
                message
                for _, message in sorted(
                    (
                        (positions.get(message_id, len(messages)), message)
                        for message_id, message in selected.items()
                    ),
                    key=lambda item: item[0],
                )
            ]
            if merged and self.token_counter(merged) <= limit:
                return merged
        except Exception:
            logging.getLogger(__name__).warning("Unable to select both summary excerpts", exc_info=True)
        return []

    def _trim_messages_for_summary(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        if not messages:
            return []
        history_tokens = self.token_counter(messages)
        limit = self.trim_tokens_to_summarize
        prompt_budget = self._summary_prompt_budget()
        if prompt_budget <= 0:
            return []
        if (
            (limit is None or history_tokens <= limit)
            and self._summary_prompt_tokens(messages) <= prompt_budget
        ):
            return messages

        upper = history_tokens if limit is None else min(limit, history_tokens)
        best: list[AnyMessage] = []
        lower = 1
        while lower <= upper:
            candidate_limit = (lower + upper) // 2
            candidate = self._select_head_and_tail(messages, candidate_limit)
            if not candidate:
                # An empty selection means neither edge fits this quota, not
                # that the rendered prompt overflowed. Try a larger quota.
                lower = candidate_limit + 1
            elif self._summary_prompt_tokens(candidate) <= prompt_budget:
                best = candidate
                lower = candidate_limit + 1
            else:
                upper = candidate_limit - 1
        return best

def _message_key(message: AnyMessage) -> tuple[str, str | int]:
    if message.id is not None:
        return ("id", str(message.id))
    return ("object", id(message))


def _merge_message_slices(first: AnyMessage, second: AnyMessage) -> AnyMessage:
    """Join head/tail partials that came from one long source message."""
    first_content = getattr(first, "content", "")
    second_content = getattr(second, "content", "")
    if first_content == second_content or not second_content:
        return first
    if not first_content:
        return second
    # Native partial trimming may return text at one edge and content blocks
    # at the other. Its content API handles all combinations without losing
    # an edge; copy first because merge_content can extend a supplied list.
    content = merge_content(deepcopy(first_content), _SUMMARY_OMISSION, second_content)
    return first.model_copy(update={"content": content})


class ConversationMemoryMiddleware(AgentMiddleware):
    async def abefore_model(self, state, runtime):
        if state.get("planning_enabled"):
            # Planning resolves follow-up context into its goal/report ledger
            # and projects only the current request plus the active step's
            # tool-call pairs before each model call. Summarizing the full
            # checkpoint first is redundant and can add a slow model call over
            # large prior answers or PDF observations.
            return None
        model = getattr(getattr(runtime, "context", None), "model", None)
        if model is None:
            logging.getLogger(__name__).warning("Summary middleware has no run-scoped model; leaving messages intact")
            return None
        threshold = _summary_trigger(model)
        if threshold is None or threshold <= 0:
            return None
        summary_limit = _summary_trim_limit(model)
        if summary_limit <= 0:
            logging.getLogger(__name__).warning(
                "Model window has no safe budget for a summary call; leaving messages intact"
            )
            return None
        # Evidence and claim ledgers remain separate checkpoint channels. Native
        # summarization preserves valid tool-call/result message boundaries.
        observer = SummaryFailureCallback()
        callbacks = runtime.context.model.callbacks
        if callbacks is None or isinstance(callbacks, list):
            callbacks = [*(callbacks or []), observer]
        else:
            callbacks = callbacks.copy()
            callbacks.add_handler(observer)
        middleware = ContextAwareSummarizationMiddleware(
            # The native middleware remains the source of truth for summary
            # lifecycle; this subclass only bounds and selects its input.
            model=runtime.context.model.model_copy(update={"callbacks": callbacks}),
            trigger=("tokens", threshold),
            keep=_summary_keep(),
            summary_prompt=_SUMMARY_PROMPT,
            trim_tokens_to_summarize=summary_limit,
        )
        try:
            update = await middleware.abefore_model(state, runtime)
        except Exception as exc:
            # Older LangChain versions may propagate a failed summary call,
            # while newer versions skip it. In either case, summarization is
            # optional: keep the original history and let the primary model
            # answer this turn. Do not log provider exception text or payloads.
            logging.getLogger(__name__).warning(
                "Conversation summary failed; leaving messages intact (%s)",
                type(exc).__name__,
            )
            return None
        # Native summarization catches provider errors. Never apply its history
        # replacement when no successful summary was generated.
        return None if observer.failed or not observer.generated else update
