"""Safe model-authored projections for the ordered assistant stream.

LangChain streams structured output as tool-call argument fragments.  This
module observes one explicitly named, user-facing contract field and forwards
only that field to the runtime display projection.  The typed answer itself
still goes through the normal parser, evidence ledger, and terminal publisher.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler


def safe_projection_text(value: Any, limit: int = 1_800) -> str:
    """Remove identifiers that must never be model-authored UI copy."""
    text = str(value or "")
    text = re.sub(r"https?://[^\s)\]}>,]+", "[链接已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"\bev_[A-Za-z0-9_.:-]+\b", "[证据编号已隐藏]", text)
    return text[:limit]


def partial_json_string_field(payload: str, field_name: str) -> str:
    """Read one JSON string field while the surrounding object is incomplete."""
    marker = re.search(rf'"{re.escape(field_name)}"\s*:\s*"', payload)
    if marker is None:
        return ""
    start = marker.end()
    index = start
    escaped = False
    end = len(payload)
    while index < len(payload):
        char = payload[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            end = index
            break
        index += 1
    fragment = payload[start:end]
    if not fragment:
        return ""
    try:
        return str(json.loads(f'"{fragment}"'))
    except (TypeError, ValueError, json.JSONDecodeError):
        # A provider may split an escape sequence across chunks.  Decode only
        # the common prose escapes; do not apply a broad unicode codec to an
        # incomplete model payload.
        return (
            fragment
            .replace(r"\\n", "\n")
            .replace(r"\\r", "\r")
            .replace(r"\\t", "\t")
            .replace(r'\"', '"')
            .replace(r"\\", "\\")
        )


class StructuredContractProjectionCallback(AsyncCallbackHandler):
    """Stream only an explicit progress field from one structured contract."""

    def __init__(
        self,
        context_or_events: Any,
        *,
        projection_id: str,
        scope: str,
        collaboration_id: str,
        agent_id: str,
        task_id: str,
        phase: str,
        kind: str,
        attempt: int = 0,
        target_tool_name: str = "",
        field_name: str = "progress_text",
        display_part_name: str = "team-model-projection",
    ) -> None:
        self.events = getattr(context_or_events, "events", context_or_events)
        self.projection_id = projection_id
        self.scope = scope
        self.collaboration_id = collaboration_id
        self.agent_id = agent_id
        self.task_id = task_id
        self.phase = phase
        self.kind = kind
        self.attempt = max(0, int(attempt or 0))
        self.target_tool_name = str(target_tool_name or "").strip()
        self.field_name = str(field_name or "progress_text").strip() or "progress_text"
        self.display_part_name = str(display_part_name or "team-model-projection").strip()
        self._args_by_index: dict[int, str] = {}
        self._names_by_index: dict[int, str] = {}
        self._published = ""

    def reset(self, attempt: int) -> None:
        self.attempt = max(0, int(attempt or 0))
        self._args_by_index.clear()
        self._names_by_index.clear()
        self._published = ""

    @staticmethod
    def _chunk_message(chunk: Any) -> Any:
        message = getattr(chunk, "message", None)
        return message if message is not None else chunk

    @staticmethod
    def _chunk_value(value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(key, default)
        return getattr(value, key, default)

    async def on_llm_new_token(self, token: str, *, chunk: Any = None, **_: Any) -> None:
        del token
        message = self._chunk_message(chunk)
        raw_chunks = getattr(message, "tool_call_chunks", None) or []
        for position, raw_chunk in enumerate(raw_chunks):
            index_value = self._chunk_value(raw_chunk, "index", position)
            try:
                index = int(index_value)
            except (TypeError, ValueError):
                index = position
            name = self._chunk_value(raw_chunk, "name", "")
            if name:
                self._names_by_index[index] = str(name)
            args = self._chunk_value(raw_chunk, "args", "")
            if args:
                self._args_by_index[index] = self._args_by_index.get(index, "") + str(args)

        for index in sorted(self._args_by_index):
            name = self._names_by_index.get(index, "")
            if self.target_tool_name and name != self.target_tool_name:
                continue
            progress = partial_json_string_field(
                self._args_by_index[index],
                self.field_name,
            )
            safe_progress = safe_projection_text(progress).strip()
            if not safe_progress or safe_progress == self._published:
                continue
            # Avoid putting an opening JSON fragment in the conversation. The
            # stable projection id lets later chunks extend the same item.
            if len(safe_progress) < 4 and not safe_progress.endswith(("。", ".", "！", "!", "？", "?")):
                continue
            if self._published and len(safe_progress) - len(self._published) < 12 and not safe_progress.endswith(
                ("。", ".", "！", "!", "？", "?")
            ):
                continue
            publish = getattr(self.events, "publish_model_projection", None)
            if not callable(publish):
                return
            publish(
                safe_progress,
                scope=self.scope,
                collaboration_id=self.collaboration_id,
                agent_id=self.agent_id,
                task_id=self.task_id,
                phase=self.phase,
                kind=self.kind,
                attempt=self.attempt,
                projection_id=self.projection_id,
                display_part_name=self.display_part_name,
            )
            self._published = safe_progress
            return


__all__ = [
    "StructuredContractProjectionCallback",
    "partial_json_string_field",
    "safe_projection_text",
]
