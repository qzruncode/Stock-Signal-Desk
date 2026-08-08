"""LangChain chat-model adapter over the application's guarded LiteLLM client.

The application keeps its existing provider accounting, retry, circuit-breaker,
and lease logic.  This adapter only translates the standard LangChain message
and tool-call protocol, so ``create_agent`` owns the Agent loop instead of a
second, hand-written structured-output loop.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Mapping

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
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


def _tool_calls(message: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index, raw_call in enumerate(_field(message, "tool_calls") or []):
        function = _field(raw_call, "function") or {}
        name = str(_field(function, "name") or _field(raw_call, "name") or "").strip()
        raw_arguments = _field(function, "arguments")
        if raw_arguments is None:
            raw_arguments = _field(raw_call, "arguments")
        if isinstance(raw_arguments, Mapping):
            arguments = dict(raw_arguments)
        else:
            try:
                parsed = json.loads(str(raw_arguments or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = {}
            arguments = dict(parsed) if isinstance(parsed, Mapping) else {}
        if not name:
            continue
        calls.append(
            {
                "name": name,
                "args": arguments,
                "id": str(_field(raw_call, "id") or f"call_{index}"),
                "type": "tool_call",
            }
        )
    return calls


def _response_metadata(response: Any) -> dict[str, Any]:
    choice = next(iter(_field(response, "choices") or []), None)
    usage = _field(response, "usage") or {}
    return {
        "model": str(_field(response, "model") or ""),
        "finish_reason": str(_field(choice, "finish_reason") or ""),
        "usage": {
            key: _field(usage, key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if _field(usage, key) is not None
        },
    }


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
            model=str(self.llm_config.get("model") or "default"),
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

    @property
    def _llm_type(self) -> str:
        return "application_litellm"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": str(self.llm_config.get("model") or "default")}

    def bind_tools(
        self,
        tools: list[BaseTool | dict[str, Any]] | list[Any],
        *,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        return self.bind(
            tools=[convert_to_openai_tool(tool) for tool in tools],
            tool_choice=tool_choice,
            **kwargs,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("LiteLLMChatModel is async-only")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        request: dict[str, Any] = {
            "stream": False,
            "messages": convert_to_openai_messages(messages),
            "temperature": kwargs.pop("temperature", self.llm_config.get("temperature", 0.1)),
            "max_tokens": int(kwargs.pop("max_tokens", self.llm_config.get("max_tokens", 8_000))),
        }
        if stop:
            request["stop"] = stop
        for field in ("tools", "tool_choice", "parallel_tool_calls", "response_format"):
            if field in kwargs and kwargs[field] is not None:
                request[field] = kwargs[field]
        response = await self.gateway.complete(
            **build_litellm_kwargs(self.llm_config, **request)
        )
        choice = next(iter(_field(response, "choices") or []), None)
        message = _field(choice, "message") or {}
        additional_kwargs: dict[str, Any] = {}
        reasoning = _field(message, "reasoning_content")
        if reasoning:
            additional_kwargs["reasoning_content"] = str(reasoning)
        ai_message = AIMessage(
            content=_content(_field(message, "content")),
            tool_calls=_tool_calls(message),
            additional_kwargs=additional_kwargs,
            response_metadata=_response_metadata(response),
        )
        return ChatResult(generations=[ChatGeneration(message=ai_message)])


__all__ = ["LiteLLMChatModel", "LiteLLMGateway"]
