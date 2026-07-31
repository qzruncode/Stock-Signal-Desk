# -*- coding: utf-8 -*-
"""Visible reasoning normalization and stage trace helpers."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Mapping, Optional

from src.agent.orchestrator_v2.contracts import AgentStage, AgentStageEventV2, StageStatus

ControllerLike = Any

def _extract_model_reasoning_delta(delta: Any) -> str:
    """Read provider reasoning text without treating arbitrary model metadata as text."""
    if delta is None:
        return ""

    values: List[str] = []
    for field in ("reasoning_content", "reasoning"):
        value = delta.get(field) if isinstance(delta, dict) else getattr(delta, field, None)
        if isinstance(value, str) and value:
            values.append(value)

    # LiteLLM keeps provider-specific fields in model_extra for some response
    # models instead of exposing them as normal attributes.
    model_extra = delta.get("model_extra") if isinstance(delta, dict) else getattr(delta, "model_extra", None)
    if isinstance(model_extra, dict):
        for field in ("reasoning_content", "reasoning"):
            value = model_extra.get(field)
            if isinstance(value, str) and value and value not in values:
                values.append(value)
    return "".join(values)


_VISIBLE_REASONING_LANGUAGE_INSTRUCTION = (
    "所有通过供应商独立 reasoning 或 reasoning_content 字段返回、并展示给用户的"
    "分析过程都必须使用简体中文；除专有名词、证券代码和必要英文缩写外，不得输出"
    "英文句子。最终 content 的既有格式与结构化输出契约保持不变。"
)


def _with_chinese_visible_reasoning(
    messages: Any,
) -> List[Dict[str, Any]]:
    """Add one system-level language contract without mutating caller messages."""
    normalized = [
        dict(message) if isinstance(message, dict) else message
        for message in (messages if isinstance(messages, list) else [])
    ]
    for index, message in enumerate(normalized):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if _VISIBLE_REASONING_LANGUAGE_INSTRUCTION not in content:
            normalized[index] = {
                **message,
                "content": (content.rstrip() + "\n\n" + _VISIBLE_REASONING_LANGUAGE_INSTRUCTION),
            }
        return normalized
    return [
        {
            "role": "system",
            "content": _VISIBLE_REASONING_LANGUAGE_INSTRUCTION,
        },
        *normalized,
    ]


def _append_model_reasoning(controller: ControllerLike, reasoning_delta: str) -> None:
    """Emit model-provided reasoning when the active stream controller supports it."""
    if not reasoning_delta:
        return
    append_reasoning = getattr(controller, "append_reasoning", None)
    if callable(append_reasoning):
        append_reasoning(reasoning_delta)


def _append_process_reasoning(
    controller: ControllerLike,
    message: str,
) -> None:
    text = str(message or "").strip()
    if text:
        _append_model_reasoning(controller, text + "\n")


def _visible_reasoning_uses_chinese(text: str) -> bool:
    """Fail closed when a provider ignores the visible-language contract."""
    cjk_chars = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text or ""))
    latin_chars = len(re.findall(r"[A-Za-z]", text or ""))
    return cjk_chars >= 2 and cjk_chars * 3 >= latin_chars


class _BufferedReasoningEmitter:
    """Coalesce provider token deltas before crossing the UI stream boundary."""

    def __init__(
        self,
        controller: ControllerLike,
        *,
        label: Optional[str] = None,
        flush_chars: int = 512,
        flush_seconds: float = 0.35,
    ) -> None:
        self._controller = controller
        self._label = label
        self._flush_chars = flush_chars
        self._flush_seconds = flush_seconds
        self._parts: List[str] = []
        self._chars = 0
        self._last_flush = time.monotonic()
        self._emitted = False

    @property
    def emitted(self) -> bool:
        return self._emitted

    def append(self, text: str) -> None:
        if not text:
            return
        self._parts.append(text)
        self._chars += len(text)
        if self._chars >= self._flush_chars or time.monotonic() - self._last_flush >= self._flush_seconds:
            self.flush()

    def flush(self) -> None:
        if not self._parts:
            return
        text = "".join(self._parts)
        self._parts.clear()
        self._chars = 0
        self._last_flush = time.monotonic()
        if not _visible_reasoning_uses_chinese(text):
            return
        if not self._emitted and self._label:
            _append_process_reasoning(self._controller, self._label)
        _append_model_reasoning(self._controller, text)
        self._emitted = True


_STAGE_TRACE_LABELS = {
    AgentStage.OUTLINE: "理解任务",
    AgentStage.PARAMETERIZATION: "确认业务条件",
    AgentStage.NORMALIZATION: "确定执行口径",
    AgentStage.RESOURCE_BINDING: "选择并绑定来源",
    AgentStage.COMPILATION: "生成执行流程",
    AgentStage.POLICY: "安全与权限校验",
    AgentStage.EXECUTION: "执行任务",
    AgentStage.BENEFIT_OUTLINE: "拆解产业受益链",
    AgentStage.CATALOG_LOADING: "载入实时板块目录",
    AgentStage.CATALOG_MAPPING: "匹配真实板块",
    AgentStage.RESULT_VALIDATION: "生成证据并核对覆盖",
    AgentStage.RESOURCE_PUBLISHED: "发布板块集合",
    AgentStage.SYNTHESIS: "进入分析",
    AgentStage.COMPLETED: "完成",
}

_STAGE_TRACE_STATUS = {
    StageStatus.STARTED: "进行中",
    StageStatus.SUCCEEDED: "已完成",
    StageStatus.FAILED: "失败",
    StageStatus.BLOCKED: "已阻断",
    StageStatus.CANCELLED: "已取消",
}


def _agent_stage_reasoning_line(event: AgentStageEventV2) -> str:
    label = _STAGE_TRACE_LABELS.get(event.stage, event.stage.value)
    status = _STAGE_TRACE_STATUS.get(event.status, event.status.value)
    task = f" · {event.task_id}" if event.task_id else ""
    error = f" · {event.error_code.value}" if event.error_code is not None else ""
    detail = f"：{event.summary}" if event.summary else ""
    return f"[{label}{task}] {status}{error}{detail}"


def _trace_json_preview(value: Any, *, max_chars: int = 320) -> str:
    sensitive = {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }

    def redact(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): ("[redacted]" if str(key).replace("-", "_").lower() in sensitive else redact(child))
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [redact(child) for child in item]
        return item

    text = json.dumps(
        redact(value),
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return text if len(text) <= max_chars else text[:max_chars] + "…"


def _response_field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)



__all__ = ["_BufferedReasoningEmitter", "_extract_model_reasoning_delta", "_with_chinese_visible_reasoning", "_append_model_reasoning", "_append_process_reasoning", "_visible_reasoning_uses_chinese", "_agent_stage_reasoning_line", "_trace_json_preview", "_response_field"]
