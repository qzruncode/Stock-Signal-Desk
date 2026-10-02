"""LangChain Anthropic chat adapter over the application's guarded runtime.

The application keeps its existing provider accounting, retry, circuit-breaker,
and lease logic.  This adapter only translates the standard LangChain message
and tool-call protocol, so ``create_agent`` owns the Agent loop instead of a
second, hand-written structured-output loop.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from operator import itemgetter
from typing import Any, Awaitable, Callable, ClassVar, Mapping

from anthropic import omit
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import agenerate_from_stream
from langchain_core.messages import BaseMessage, convert_to_openai_messages
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
from pydantic import ConfigDict, Field, model_validator

from src.agent.model_runtime import GuardedModelRuntime


class GuardedModelGateway:
    """Run-scoped provider capacity and accounting for chat model calls."""

    def __init__(
        self,
        *,
        llm_config: Mapping[str, Any],
        database: Any | None,
        run_id: str,
        worker_id: str,
    ) -> None:
        self.llm_config = dict(llm_config)
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

    async def complete(
        self,
        completion: Callable[..., Awaitable[Any]],
        **kwargs: Any,
    ) -> Any:
        return await self.runtime.complete(completion, **kwargs)


_NO_CHUNK = object()


class _PrefetchedAsyncIterator:
    """Replay the first provider chunk after starting a guarded stream."""

    def __init__(self, iterator: AsyncIterator[Any], first: Any = _NO_CHUNK) -> None:
        self._iterator = iterator
        self._first = first

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._first is not _NO_CHUNK:
            first, self._first = self._first, _NO_CHUNK
            return first
        return await anext(self._iterator)

    async def aclose(self) -> None:
        closer = getattr(self._iterator, "aclose", None)
        if callable(closer):
            await closer()


class GuardedAnthropicChatModel(ChatAnthropic):
    """LangChain's Anthropic adapter with explicit no-timeout transport."""

    gateway: GuardedModelGateway = Field(exclude=True)
    llm_config: dict[str, Any] = Field(default_factory=dict, exclude=True)
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    # Planning needs a provider-independent function-call contract.  The
    # standard BaseChatModel implementation only accepts ``method`` and
    # ``strict`` as extra arguments, so expose the small extension used by the
    # coordinator without making the rest of the LangChain model API custom.
    supports_exact_structured_output: ClassVar[bool] = True

    @model_validator(mode="before")
    @classmethod
    def set_default_max_tokens(cls, values: Any) -> Any:
        """Do not inherit LangChain's fallback generation cap for our gateway.

        The configured Anthropic-compatible gateway accepts an omitted output
        limit. An explicitly supplied limit remains opt-in; neither the input
        headroom estimate nor an unknown model profile sets a generation cap.
        """
        return values

    def __init__(
        self,
        *,
        gateway: GuardedModelGateway,
        llm_config: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        config = dict(llm_config)
        model_name = str(config.get("model") or "").strip()
        if not model_name:
            raise ValueError("model must be provided by the configured model settings")
        super().__init__(
            gateway=gateway,
            llm_config=config,
            model_name=model_name,
            api_key=str(config.get("api_key") or ""),
            base_url=str(config.get("api_base") or ""),
            default_headers=dict(config.get("extra_headers") or {}),
            max_tokens=config.get("max_tokens"),
            temperature=config.get("temperature", 0.1),
            output_config=config.get("output_config"),
            # Explicit None disables the Anthropic SDK's transport timeout.
            # Retry behavior stays in GuardedModelRuntime.
            timeout=None,
            max_retries=0,
            **kwargs,
        )

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # The SDK requires the Python keyword even when the compatible
        # gateway permits omission in JSON. Its native sentinel removes the
        # field on the wire, rather than replacing it with another fixed cap.
        payload.setdefault("max_tokens", omit)
        return payload

    def _runtime_request(
        self,
        messages: Sequence[BaseMessage],
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        allowance = kwargs.get("max_tokens", self.max_tokens)
        max_tokens = int(allowance) if allowance else None
        return {
            "messages": convert_to_openai_messages(list(messages)),
            "max_tokens": max_tokens,
        }

    def get_num_tokens_from_messages(
        self,
        messages: Sequence[BaseMessage],
        tools: Sequence[Any] | None = None,
    ) -> int:
        """Use LiteLLM token accounting with a LangChain approximation fallback."""
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
        if tool_choice is True or (
            isinstance(tool_choice, str) and tool_choice in {"any", "required"}
        ):
            tool_choice = "any"
        elif tool_choice is False:
            tool_choice = None
        elif isinstance(tool_choice, Mapping) and tool_choice.get("type") == "function":
            function = tool_choice.get("function")
            if isinstance(function, Mapping) and function.get("name"):
                tool_choice = {"type": "tool", "name": str(function["name"])}
        return super().bind_tools(
            tools,
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
        """Use LangChain's tool parser with the native Anthropic request shape."""
        method = kwargs.pop("method", None)
        strict = kwargs.pop("strict", None)
        tool_choice = kwargs.pop("tool_choice", None)
        stream = kwargs.pop("stream", None)
        if method not in {None, "function_calling"}:
            raise ValueError("the configured Anthropic-compatible gateway uses function calling")
        if kwargs:
            raise ValueError(f"Received unsupported arguments {kwargs}")

        tool_schema = convert_to_openai_tool(schema)
        tool_name = tool_schema["function"]["name"]
        bound_kwargs: dict[str, Any] = {
            "tool_choice": tool_choice or tool_name,
            "parallel_tool_calls": False,
            "ls_structured_output_format": {
                "kwargs": {"method": "function_calling"},
                "schema": schema,
            },
        }
        if strict is not None:
            # Use the installed Anthropic adapter's schema conversion. It
            # marks every tool field required instead of silently accepting
            # provider omissions through Pydantic defaults.
            bound_kwargs["strict"] = strict
        if stream is not None:
            # This private kwarg is consumed by _agenerate/_astream and never
            # leaks into the provider payload as an unknown option.
            bound_kwargs["_stream"] = bool(stream)
        contract_model = self
        if stream is False:
            # LangGraph's message callback can implicitly select _astream
            # before _agenerate sees our private _stream flag. The native
            # hard opt-out applies to both callback protocols; copying keeps
            # ordinary chat streaming enabled on the run-scoped model.
            contract_model = self.model_copy(update={"disable_streaming": True})
        llm = contract_model.bind_tools([schema], **bound_kwargs)

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
        raise NotImplementedError("GuardedAnthropicChatModel is async-only")

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Stream with no application or HTTP-client response deadline."""
        del run_manager
        options = dict(kwargs)
        options.pop("_stream", None)
        options.pop("stream", None)
        options.pop("timeout", None)
        provider_stream = super()._astream

        async def start_stream(**_runtime_kwargs: Any) -> AsyncIterator[Any]:
            iterator = provider_stream(
                messages,
                stop=stop,
                run_manager=None,
                **options,
            )
            try:
                first = await anext(iterator)
            except StopAsyncIteration:
                return _PrefetchedAsyncIterator(iterator)
            return _PrefetchedAsyncIterator(iterator, first)

        response = await self.gateway.complete(
            start_stream,
            **self._runtime_request(messages, options),
        )
        model_name_emitted = False
        async for chunk in response:
            metadata = getattr(chunk.message, "response_metadata", None)
            if isinstance(metadata, dict):
                if model_name_emitted:
                    metadata.pop("model_name", None)
                else:
                    metadata.setdefault("model_name", self.model)
                    model_name_emitted = True
            yield chunk

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        stream = bool(kwargs.pop("_stream", True))
        kwargs.pop("stream", None)
        kwargs.pop("timeout", None)

        if not stream:
            provider_call = super()._agenerate

            async def complete(**_runtime_kwargs: Any) -> ChatResult:
                result = await provider_call(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    **kwargs,
                )
                for generation in result.generations:
                    metadata = getattr(generation.message, "response_metadata", None)
                    if isinstance(metadata, dict):
                        metadata.setdefault("model_name", self.model)
                return result

            return await self.gateway.complete(
                complete,
                **self._runtime_request(messages, kwargs),
            )

        async def callback_stream() -> AsyncIterator[ChatGenerationChunk]:
            async for chunk in self._astream(messages, stop=stop, **kwargs):
                if run_manager is not None:
                    await run_manager.on_llm_new_token(
                        chunk.message.content,
                        chunk=chunk,
                    )
                yield chunk

        return await agenerate_from_stream(callback_stream())


__all__ = ["GuardedAnthropicChatModel", "GuardedModelGateway"]
