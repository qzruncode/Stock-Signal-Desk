# -*- coding: utf-8 -*-
"""liteLLM streaming, stream-part parsing and model-report streaming orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import litellm

from src.ai_caller import call_ai_structured
from src.config import get_config
from src.llm.anthropic_gateway import (
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)
from src.llm.generation_params import apply_litellm_generation_params
from src.storage import DatabaseManager, persist_llm_usage

from ._llm import (
    _validate_model_report,
    build_model_report_prompts,
    build_streaming_report_draft,
)
from ._context import build_report_evidence_pack, collect_context

logger = logging.getLogger(__name__)


def extract_json_object_from_text(raw_text: str) -> Optional[str]:
    text = (raw_text or "").strip()
    if not text:
        return None

    for start, ch in enumerate(text):
        if ch != "{":
            continue
        candidate = text[start:].strip()
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return candidate
    return None


def normalize_market_mainline_usage(raw_usage: Any) -> dict[str, Any]:
    if raw_usage is None:
        return {}

    def _read(name: str) -> int:
        if isinstance(raw_usage, dict):
            value = raw_usage.get(name)
        else:
            value = getattr(raw_usage, name, None)
        try:
            return int(value or 0)
        except Exception:
            return 0

    usage = {
        "prompt_tokens": _read("prompt_tokens"),
        "completion_tokens": _read("completion_tokens"),
        "total_tokens": _read("total_tokens"),
    }
    return usage if any(usage.values()) else {}


def extract_market_mainline_stream_parts(delta: Any) -> tuple[str, str]:
    if not delta:
        return "", ""

    content = getattr(delta, "content", None)
    if isinstance(delta, dict):
        content = delta.get("content")

    content_text = ""
    if isinstance(content, str):
        content_text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        content_text = "".join(parts)

    reasoning = getattr(delta, "reasoning_content", None)
    if isinstance(delta, dict):
        reasoning = delta.get("reasoning_content")
    reasoning_text = reasoning if isinstance(reasoning, str) else ""

    raw_text = "".join(part for part in (reasoning_text, content_text) if part)
    return raw_text, content_text


def stream_market_mainline_report_via_litellm(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    on_text: Optional[Callable[[str, str], None]] = None,
) -> tuple[str, str, str, dict[str, Any]]:
    llm_cfg = resolve_anthropic_gateway_config()
    config = get_config()
    thinking_enabled = bool(getattr(config, "llm_thinking_enabled", False))
    reasoning_effort = getattr(config, "llm_reasoning_effort", "auto")

    async def _run() -> tuple[str, str, str, dict[str, Any]]:
        call_kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
        )

        call_kwargs = apply_litellm_generation_params(
            call_kwargs,
            llm_cfg["model"],
            temperature,
        )

        if thinking_enabled and reasoning_effort != "auto":
            extra_body = call_kwargs.get("extra_body", {})
            extra_body["reasoning_effort"] = reasoning_effort
            call_kwargs["extra_body"] = extra_body

        response = await litellm.acompletion(**call_kwargs)
        raw_chunks: list[str] = []
        content_chunks: list[str] = []
        usage: dict[str, Any] = {}

        async for chunk in response:
            normalized_usage = normalize_market_mainline_usage(getattr(chunk, "usage", None))
            if normalized_usage:
                usage = normalized_usage

            delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
            raw_delta_text, content_delta_text = extract_market_mainline_stream_parts(delta)
            if not raw_delta_text and not content_delta_text:
                continue

            if raw_delta_text:
                raw_chunks.append(raw_delta_text)
            if content_delta_text:
                content_chunks.append(content_delta_text)
            if on_text:
                full_text = "".join(raw_chunks)
                on_text(raw_delta_text or content_delta_text, full_text)

        raw_response_text = "".join(raw_chunks).strip()
        response_text = "".join(content_chunks).strip()
        if not raw_response_text:
            raise RuntimeError(f"{llm_cfg['model']} stream returned empty response")

        return raw_response_text, response_text, llm_cfg["model"], usage

    return asyncio.run(_run())


def generate_model_report_stream(
    *,
    force: bool,
    task_queue: Any,
    task_id: str,
) -> dict[str, Any]:
    task_queue.update_task_progress(task_id, 5, "正在准备市场主线证据包")
    try:
        context = collect_context(force=force, include_rss=True)
    except Exception as exc:
        raise RuntimeError(f"市场数据采集失败: {exc}") from exc

    evidence_pack = build_report_evidence_pack(context)
    system_prompt, user_prompt = build_model_report_prompts(evidence_pack)
    task_queue.update_task_result(
        task_id,
        {
            "phase": "collecting",
            "stream_text": "",
            "report_draft": {
                "as_of_date": str((context.get("source_snapshot") or {}).get("market_status", {}).get("data_time") or ""),
            },
            "debug_input": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            },
        },
        progress=18,
        message="证据包已准备完成，等待模型连接",
    )

    task_queue.update_task_result(
        task_id,
        {
            "phase": "waiting_model",
            "debug_input": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "evidence_pack": evidence_pack,
            },
        },
        progress=24,
        message="正在连接模型服务",
    )

    try:
        result = build_llm_model_report_streaming(
            context,
            evidence_pack=evidence_pack,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_text=lambda accumulated_text, draft: task_queue.update_task_result(
                task_id,
                {
                    "phase": "generating",
                    "stream_text": accumulated_text,
                    "report_draft": draft,
                    "debug_input": {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "evidence_pack": evidence_pack,
                    },
                },
                progress=min(92, 24 + max(1, len(accumulated_text) // 120)),
                message="模型已连接，正在生成研判内容",
            ),
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    if not result:
        raise RuntimeError("模型研判生成失败")

    return {
        "phase": "completed",
        "stream_text": result.get("raw_stream_output") or result.get("raw_response") or result.get("full_report") or "",
        "report_draft": {
            "overview": result.get("overview"),
            "full_report": result.get("full_report"),
            "as_of_date": result.get("as_of_date"),
            "market_stage": result.get("market_stage"),
        },
        "debug_input": {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        },
        "report": result,
        "llm_used": result.get("llm_used", True),
        "model_used": result.get("model_used"),
    }


def build_llm_model_report_streaming(
    context: dict[str, Any],
    *,
    evidence_pack: Optional[dict[str, Any]] = None,
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    on_text: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    evidence_pack = evidence_pack or build_report_evidence_pack(context)
    if system_prompt is None or user_prompt is None:
        system_prompt, user_prompt = build_model_report_prompts(evidence_pack)

    accumulated_text = ""
    last_emitted_length = 0

    def _on_stream_text(_delta_text: str, full_text: str) -> None:
        nonlocal accumulated_text, last_emitted_length
        accumulated_text = full_text
        if on_text and (
            len(full_text) - last_emitted_length >= 180
            or len(full_text) < 180
        ):
            last_emitted_length = len(full_text)
            on_text(full_text, build_streaming_report_draft(full_text))

    try:
        raw_response_text, response_text, model_used, usage = stream_market_mainline_report_via_litellm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=8192,
            on_text=_on_stream_text,
        )
        persist_llm_usage(usage, model_used, call_type="market_mainline_report")
        json_payload_text = extract_json_object_from_text(raw_response_text) or response_text
        parsed = json.loads(json_payload_text)
        if not isinstance(parsed, dict):
            return None
        parsed = _validate_model_report(parsed, evidence_pack)
        parsed.setdefault("generated_at", context["generated_at"])
        parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
        parsed["llm_used"] = True
        parsed["model_used"] = model_used
        parsed.setdefault("current_mainlines", [])
        parsed.setdefault("future_mainlines", [])
        parsed.setdefault("action_summary", [])
        parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
        parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
        parsed["raw_stream_output"] = raw_response_text
        parsed["raw_response"] = raw_response_text
        parsed["debug_input"] = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        }
        if on_text and raw_response_text and len(raw_response_text) != last_emitted_length:
            on_text(raw_response_text, build_streaming_report_draft(raw_response_text))
        DatabaseManager.get_instance().save_market_mainline_report(
            report_key="market_mainline",
            as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
            mode="llm",
            payload=parsed,
            raw_response=raw_response_text,
            model_used=model_used,
        )
        return parsed
    except Exception:
        logger.warning(
            "market mainline direct stream failed, falling back to non-stream completion",
            exc_info=True,
        )
        if on_text and accumulated_text and len(accumulated_text) != last_emitted_length:
            on_text(accumulated_text, build_streaming_report_draft(accumulated_text))

    try:
        from src.analyzer import get_analyzer
    except Exception:
        logger.exception("market mainline report analyzer import failed")
        raise RuntimeError("模型服务初始化失败")

    analyzer = get_analyzer()
    if not getattr(analyzer, "is_available", lambda: False)():
        logger.info("market mainline model report skipped because analyzer is unavailable")
        raise RuntimeError("模型服务当前不可用")

    try:
        response_text, model_used, _usage = call_ai_structured(
            analyzer,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            call_type="market_mainline_report",
            temperature=0.2,
            max_tokens=8192,
            stream=False,
        )
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            return None
        parsed = _validate_model_report(parsed, evidence_pack)
        parsed.setdefault("generated_at", context["generated_at"])
        parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
        parsed["llm_used"] = True
        parsed["model_used"] = model_used
        parsed.setdefault("current_mainlines", [])
        parsed.setdefault("future_mainlines", [])
        parsed.setdefault("action_summary", [])
        parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
        parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
        parsed["raw_stream_output"] = accumulated_text or response_text
        parsed["raw_response"] = accumulated_text or response_text
        parsed["debug_input"] = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        }
        if on_text and response_text and len(response_text) != last_emitted_length:
            on_text(response_text, build_streaming_report_draft(response_text))
        DatabaseManager.get_instance().save_market_mainline_report(
            report_key="market_mainline",
            as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
            mode="llm",
            payload=parsed,
            raw_response=accumulated_text or response_text,
            model_used=model_used,
        )
        return parsed
    except Exception as exc:
        logger.exception("market mainline model report streaming generation failed")
        if on_text and accumulated_text and len(accumulated_text) != last_emitted_length:
            on_text(accumulated_text, build_streaming_report_draft(accumulated_text))
        raise RuntimeError(f"模型连接失败: {exc}") from exc
