"""LangChain chat-model adapter over the application's guarded LiteLLM client.

The application keeps its existing provider accounting, retry, circuit-breaker,
and lease logic.  This adapter only translates the standard LangChain message
and tool-call protocol, so ``create_agent`` owns the Agent loop instead of a
second, hand-written structured-output loop.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from operator import itemgetter
from typing import Any, Awaitable, Callable, Mapping

from langchain_core.language_models.chat_models import BaseChatModel, agenerate_from_stream
from langchain_core.messages import AIMessageChunk, BaseMessage, convert_to_openai_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.output_parsers.openai_tools import (
    JsonOutputKeyToolsParser,
    PydanticToolsParser,
)
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableMap, RunnablePassthrough
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.utils.pydantic import is_basemodel_subclass
from pydantic import ConfigDict, Field

from src.agent.model_runtime import GuardedModelRuntime
from src.llm.anthropic_gateway import build_litellm_kwargs


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _content(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [dict(item) if isinstance(item, Mapping) else {"type": "text", "text": str(item)} for item in value]
    if value is None:
        return ""
    return str(value)


def _response_metadata(response: Any) -> dict[str, Any]:
    choice = next(iter(_field(response, "choices") or []), None)
    usage = _field(response, "usage") or {}
    return {
        "model": str(_field(response, "model") or ""),
        "model_name": str(_field(response, "model") or ""),
        "finish_reason": str(_field(choice, "finish_reason") or ""),
        "usage": {
            key: _field(usage, key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if _field(usage, key) is not None
        },
    }


def _usage_metadata(response: Any) -> dict[str, Any] | None:
    usage = _field(response, "usage")
    if usage is None or _field(usage, "prompt_tokens") is None or _field(usage, "completion_tokens") is None:
        return None
    try:
        inputs, outputs = int(_field(usage, "prompt_tokens")), int(_field(usage, "completion_tokens"))
        total = int(_field(usage, "total_tokens") or inputs + outputs)
    except (TypeError, ValueError):
        return None
    if min(inputs, outputs, total) < 0:
        return None
    result: dict[str, Any] = {
        "input_tokens": inputs, "output_tokens": outputs,
        "total_tokens": total,
    }
    details = _field(usage, "prompt_tokens_details") or {}
    cached = _field(details, "cached_tokens")
    if isinstance(cached, int) and cached >= 0:
        result["input_token_details"] = {"cache_read": cached}
    reasoning = _field(_field(usage, "completion_tokens_details") or {}, "reasoning_tokens")
    if isinstance(reasoning, int) and reasoning >= 0:
        result["output_token_details"] = {"reasoning": reasoning}
    return result


def _tool_call_chunks(value: Any) -> list[dict[str, Any]]:
    """Translate provider tool-call deltas to LangChain's standard chunks."""
    chunks: list[dict[str, Any]] = []
    for position, raw_call in enumerate(value or []):
        function = _field(raw_call, "function") or {}
        name = _field(function, "name") or _field(raw_call, "name")
        arguments = _field(function, "arguments")
        if arguments is None:
            arguments = _field(raw_call, "arguments")
        if isinstance(arguments, Mapping):
            arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        raw_index = _field(raw_call, "index")
        try:
            index = int(raw_index) if raw_index is not None else position
        except (TypeError, ValueError):
            index = position
        chunks.append(
            {
                "name": str(name) if name else None,
                "args": str(arguments) if arguments is not None else "",
                "id": str(_field(raw_call, "id")) if _field(raw_call, "id") else None,
                "index": index,
            }
        )
    return chunks


def _generation_chunk(response: Any) -> ChatGenerationChunk:
    """Map one LiteLLM/OpenAI stream item to a native LangChain chunk.

    LiteLLM returns OpenAI-compatible objects, but different providers expose
    them as dictionaries or pydantic objects and may put reasoning in either
    ``reasoning_content`` or ``reasoning``.  Keeping this translation at the
    model adapter boundary lets LangGraph and LangChain use their normal
    streaming/callback machinery without an application-specific stream
    protocol.
    """
    choices = _field(response, "choices") or []
    choice = next(iter(choices), None)
    delta = _field(choice, "delta")
    message = _field(choice, "message") if delta is None else None
    payload = delta if delta is not None else (message or {})
    reasoning = _field(payload, "reasoning_content") or _field(payload, "reasoning")
    additional_kwargs: dict[str, Any] = {}
    if reasoning:
        additional_kwargs["reasoning_content"] = str(reasoning)

    raw_calls = _field(payload, "tool_calls")
    tool_call_chunks = _tool_call_chunks(raw_calls)
    # A non-stream response can still be returned by a proxy that ignores
    # ``stream=true``.  Convert its complete tool calls to the same chunk
    # representation so the standard LangChain merge path remains valid.
    if not tool_call_chunks and message is not None:
        tool_call_chunks = _tool_call_chunks(_field(message, "tool_calls"))

    metadata = _response_metadata(response)
    chunk_id = _field(response, "id") or _field(payload, "id")
    ai_chunk = AIMessageChunk(
        content=_content(_field(payload, "content")),
        additional_kwargs=additional_kwargs,
        response_metadata=metadata,
        id=str(chunk_id) if chunk_id else None,
        tool_call_chunks=tool_call_chunks,
        usage_metadata=_usage_metadata(response),
    )
    return ChatGenerationChunk(message=ai_chunk)


class LiteLLMGateway:
    """Run-scoped access to LiteLLM through the existing safety boundary."""

    def __init__(
        self,
        *,
        llm_config: Mapping[str, Any],
        database: Any | None,
        run_id: str,
        worker_id: str,
        completion: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.llm_config = dict(llm_config)
        self._completion = completion
        configured_model = str(self.llm_config.get("model") or "").strip()
        if not configured_model:
            raise ValueError("model must be provided by the configured model settings")

        def estimate(messages: list[dict[str, Any]], model: str) -> int:
            try:
                import litellm

                return int(litellm.token_counter(model=model, messages=messages))
            except Exception:
                characters = len(json.dumps(messages, ensure_ascii=False, default=str))
                return max(1, int(characters / 2.5))

        self.runtime = GuardedModelRuntime(
            database=database,
            run_id=run_id,
            worker_id=worker_id,
            model=configured_model,
            token_estimator=estimate,
        )

    async def complete(self, **kwargs: Any) -> Any:
        completion = self._completion
        if completion is None:
            import litellm

            completion = litellm.acompletion
        return await self.runtime.complete(completion, **kwargs)


class LiteLLMChatModel(BaseChatModel):
    """A native async chat model that speaks LangChain's tool protocol."""

    gateway: LiteLLMGateway = Field(exclude=True)
    llm_config: dict[str, Any] = Field(default_factory=dict, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Planning needs a provider-independent function-call contract.  The
    # standard BaseChatModel implementation only accepts ``method`` and
    # ``strict`` as extra arguments, so expose the small extension used by the
    # coordinator without making the rest of the LangChain model API custom.
    supports_exact_structured_output: bool = Field(default=True, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "application_litellm"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": str(self.llm_config.get("model") or "")}

    def get_num_tokens_from_messages(
        self,
        messages: Sequence[BaseMessage],
        tools: Sequence[Any] | None = None,
    ) -> int:
        """Count the same OpenAI-compatible payload used by the gateway."""
        payload = convert_to_openai_messages(messages)
        tool_payload = None
        if tools:
            tool_payload = [convert_to_openai_tool(tool) for tool in tools]
        try:
            import litellm

            return int(
                litellm.token_counter(
                    model=str(self.llm_config.get("model") or ""),
                    messages=payload,
                    tools=tool_payload,
                )
            )
        except Exception:
            message_tokens = count_tokens_approximately(messages)
            if not tool_payload:
                return message_tokens
            return message_tokens + max(
                1,
                len(json.dumps(tool_payload, ensure_ascii=False, default=str)) // 4,
            )

    def bind_tools(
        self,
        tools: list[BaseTool | dict[str, Any]] | list[Any],
        *,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        # Match LangChain's OpenAI-compatible adapter contract: ToolStrategy
        # uses "any", whereas the provider API uses "required". Passing "any"
        # through makes required structured output provider-dependent.
        if tool_choice is True or tool_choice == "any":
            tool_choice = "required"
        elif tool_choice is False:
            tool_choice = None
        elif isinstance(tool_choice, str) and tool_choice in {
            tool.get("function", {}).get("name") for tool in formatted_tools
        }:
            tool_choice = {"type": "function", "function": {"name": tool_choice}}
        return self.bind(
            tools=formatted_tools,
            tool_choice=tool_choice,
            **kwargs,
        )

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Use LangChain's native tool parser with an exact tool contract.

        The application gateway is OpenAI-compatible, but it is not listed in
        LiteLLM's static capability table.  We therefore keep the standard
        LangChain parser and make the transport explicit at this adapter
        boundary: callers may select the exact schema tool and request a
        non-streaming response.  ``include_raw`` is important for Planning so
        a missing tool call is diagnosable instead of becoming ``None``.
        """
        _ = kwargs.pop("method", None)
        _ = kwargs.pop("strict", None)
        tool_choice = kwargs.pop("tool_choice", "any")
        stream = kwargs.pop("stream", None)
        if kwargs:
            raise ValueError(f"Received unsupported arguments {kwargs}")

        tool_schema = convert_to_openai_tool(schema)
        tool_name = tool_schema["function"]["name"]
        bound_kwargs: dict[str, Any] = {
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,
            "ls_structured_output_format": {
                "kwargs": {"method": "function_calling"},
                "schema": schema,
            },
        }
        if stream is not None:
            # This private kwarg is consumed by _agenerate/_astream and never
            # leaks into the provider payload as an unknown option.
            bound_kwargs["_stream"] = bool(stream)
        llm = self.bind_tools([schema], **bound_kwargs)

        if isinstance(schema, type) and is_basemodel_subclass(schema):
            output_parser = PydanticToolsParser(tools=[schema], first_tool_only=True)
        else:
            output_parser = JsonOutputKeyToolsParser(
                key_name=tool_name,
                first_tool_only=True,
            )
        if include_raw:
            parser_assign = RunnablePassthrough.assign(
                parsed=itemgetter("raw") | output_parser,
                parsing_error=lambda _: None,
            )
            parser_none = RunnablePassthrough.assign(parsed=lambda _: None)
            parser_with_fallback = parser_assign.with_fallbacks(
                [parser_none], exception_key="parsing_error"
            )
            return RunnableMap(raw=llm) | parser_with_fallback
        return llm | output_parser

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("LiteLLMChatModel is async-only")

    def _request(
        self,
        messages: list[BaseMessage],
        *,
        stream: bool,
        stop: list[str] | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the provider request once for both invoke and stream paths."""
        options = dict(kwargs)
        request: dict[str, Any] = {
            "stream": stream,
            "messages": convert_to_openai_messages(messages),
            "temperature": options.pop("temperature", self.llm_config.get("temperature", 0.1)),
            "max_tokens": int(options.pop("max_tokens", self.llm_config.get("max_tokens", 8_000))),
        }
        if stream:
            request["stream_options"] = {"include_usage": True}
        if stop:
            request["stop"] = stop
        for field in ("tools", "tool_choice", "parallel_tool_calls", "response_format"):
            if field in options and options[field] is not None:
                request[field] = options[field]
        return request

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Use LangChain's standard async stream contract over LiteLLM.

        ``BaseChatModel.astream`` owns callback dispatch.  ``_agenerate``
        below dispatches the same callbacks for LangGraph's native
        ``model.ainvoke`` path, which is why graph ``messages`` streaming and
        ordinary LangChain streaming observe the same chunks.
        """
        del run_manager
        stream = bool(kwargs.pop("_stream", True))
        request = self._request(messages, stream=stream, stop=stop, kwargs=kwargs)
        response = await self.gateway.complete(
            **build_litellm_kwargs(self.llm_config, **request)
        )
        if hasattr(response, "__aiter__"):
            reported_usage = None
            model_name = str(self.llm_config.get("model") or "")
            model_emitted = False
            try:
                async for item in response:
                    if item is not None:
                        chunk = _generation_chunk(item)
                        if chunk.message.usage_metadata is not None:
                            reported_usage = chunk.message.usage_metadata
                            chunk.message.usage_metadata = None
                        model_name = str(_field(item, "model") or model_name)
                        if model_emitted:
                            chunk.message.response_metadata.pop("model_name", None)
                        else:
                            chunk.message.response_metadata["model_name"] = model_name
                            model_emitted = True
                        yield chunk
                if reported_usage is not None:
                    yield ChatGenerationChunk(message=AIMessageChunk(
                        content="", usage_metadata=reported_usage,
                    ))
            finally:
                closer = getattr(response, "aclose", None)
                if callable(closer):
                    await closer()
            return
        if response is not None:
            chunk = _generation_chunk(response)
            chunk.message.response_metadata["model_name"] = str(
                _field(response, "model") or self.llm_config.get("model") or ""
            )
            yield chunk

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        stream = bool(kwargs.pop("_stream", True))

        async def callback_stream() -> AsyncIterator[ChatGenerationChunk]:
            async for chunk in self._astream(messages, stop=stop, _stream=stream, **kwargs):
                if run_manager is not None:
                    await run_manager.on_llm_new_token(
                        chunk.message.content,
                        chunk=chunk,
                    )
                yield chunk

        return await agenerate_from_stream(callback_stream())


__all__ = ["LiteLLMChatModel", "LiteLLMGateway"]
