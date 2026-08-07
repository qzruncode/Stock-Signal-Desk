"""Model boundary for planning, reflection, synthesis, and verification."""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Mapping, TypeVar

from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from src.agent.model_runtime import GuardedModelRuntime
from src.llm.anthropic_gateway import build_litellm_kwargs


ContractT = TypeVar("ContractT", bound=BaseModel)


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = repair_json(text, return_objects=True)
    if not isinstance(parsed, dict):
        raise ValueError("model response must contain one JSON object")
    return parsed


def _response_payload(response: Any, function_name: str) -> dict[str, Any]:
    contents: list[Any] = []
    for choice in _field(response, "choices") or []:
        message = _field(choice, "message")
        if message is None:
            continue
        for call in _field(message, "tool_calls") or []:
            function = _field(call, "function")
            if str(_field(function, "name") or "") != function_name:
                continue
            arguments = _field(function, "arguments")
            if arguments is not None:
                return _json_object(arguments)
        content = _field(message, "content")
        if content:
            contents.append(content)
    errors: list[Exception] = []
    for content in contents:
        try:
            return _json_object(content)
        except Exception as exc:  # pragma: no cover - only last error matters
            errors.append(exc)
    if errors:
        raise errors[0]
    raise ValueError(f"model returned no {function_name} payload")


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for choice in _field(response, "choices") or []:
        message = _field(choice, "message")
        content = _field(message, "content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                text = _field(item, "text")
                if text:
                    parts.append(str(text))
    return "".join(parts).strip()


def _fallback_token_estimator(messages: list[dict[str, Any]], _model: str) -> int:
    characters = sum(
        len(json.dumps(message, ensure_ascii=False, default=str))
        for message in messages
    )
    return max(1, int(characters / 2.5))


class StructuredModelClient:
    """One run-scoped client with provider budgets and exact contracts."""

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
                return _fallback_token_estimator(messages, model)

        self.runtime = GuardedModelRuntime(
            database=database,
            run_id=run_id,
            worker_id=worker_id,
            model=str(self.llm_config.get("model") or "default"),
            token_estimator=estimate,
        )

    async def _complete(self, **kwargs: Any) -> Any:
        completion = self._completion
        if completion is None:
            import litellm

            completion = litellm.acompletion
        return await self.runtime.complete(completion, **kwargs)

    async def structured(
        self,
        contract: type[ContractT],
        *,
        function_name: str,
        description: str,
        system_prompt: str,
        payload: Mapping[str, Any],
        max_tokens: int = 4_000,
        validator: Callable[[ContractT], None] | None = None,
    ) -> ContractT:
        """Request one exact object and perform one targeted repair."""
        schema = contract.model_json_schema()
        first_payload: dict[str, Any] | None = None
        first_error: Exception | None = None
        for attempt in range(2):
            request_payload = dict(payload)
            if attempt:
                request_payload["targeted_repair"] = {
                    "invalid_payload": first_payload,
                    "validation_error": str(first_error),
                    "instruction": "仅修复无效字段，返回符合完整 Schema 的对象。",
                }
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": description,
                            "state": request_payload,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ]
            kwargs = build_litellm_kwargs(
                self.llm_config,
                stream=False,
                messages=messages,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": function_name,
                            "description": description,
                            "parameters": schema,
                        },
                    }
                ],
                tool_choice={
                    "type": "function",
                    "function": {"name": function_name},
                },
                temperature=0,
                max_tokens=max(256, int(max_tokens)),
            )
            raw_payload: dict[str, Any] | None = None
            try:
                response = await self._complete(**kwargs)
                raw_payload = _response_payload(response, function_name)
                result = contract.model_validate(raw_payload)
                if validator is not None:
                    validator(result)
                return result
            except (ValidationError, ValueError, TypeError) as exc:
                if attempt == 0:
                    first_payload = raw_payload
                    first_error = exc
                    continue
                raise RuntimeError(
                    f"{function_name} remained invalid after one repair: {exc}"
                ) from exc
        raise AssertionError("unreachable")

    async def text(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int = 8_000,
        temperature: float = 0.1,
    ) -> str:
        kwargs = build_litellm_kwargs(
            self.llm_config,
            stream=False,
            messages=messages,
            temperature=temperature,
            max_tokens=max(256, int(max_tokens)),
        )
        response = await self._complete(**kwargs)
        text = _response_text(response)
        if not text:
            raise RuntimeError("model returned an empty answer")
        return text


__all__ = ["StructuredModelClient"]
