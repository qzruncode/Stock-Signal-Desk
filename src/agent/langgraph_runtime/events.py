"""Presentation adapter from generic graph activity to assistant-stream chunks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


class GraphEventBridge:
    def __init__(self, controller: Any | None, *, run_id: str) -> None:
        self.controller = controller
        self.run_id = run_id

    def stage(
        self,
        stage: str,
        status: str,
        summary: str,
        *,
        action_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "event": "agent_stage",
            "engine": "langgraph",
            "run_id": self.run_id,
            "stage": stage,
            "status": status,
            "action_id": action_id,
            "error_code": error_code,
            "summary": summary,
            "occurred_at": datetime.now().astimezone().isoformat(),
        }
        if self.controller is not None:
            self.controller.add_data(payload)
        return payload

    def approval_required(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = {
            "event": "approval_required",
            "engine": "langgraph",
            **dict(payload),
        }
        if self.controller is not None:
            self.controller.add_data(event)
        return event

    def reasoning(self, text: str) -> None:
        if self.controller is not None and text:
            self.controller.append_reasoning(text)

    def text(self, text: str) -> None:
        if self.controller is not None and text:
            self.controller.append_text(text)

    def error(self, text: str) -> None:
        if self.controller is not None and text:
            self.controller.add_error(text)


def redact_arguments(
    arguments: Mapping[str, Any],
    *,
    sensitive_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    sensitive = set(sensitive_fields)
    return {
        str(key): ("***" if str(key) in sensitive else value)
        for key, value in arguments.items()
        if str(key) != "confirmed"
    }


__all__ = ["GraphEventBridge", "redact_arguments"]
