"""Send one explicitly supplied notification through configured channels."""

from __future__ import annotations

from typing import Any

from src.notification import get_notification_service
from src.tools._workflow import envelope
from src.tools.base import ToolSpec, object_schema


def send_custom_notification(message: str, title: str = "") -> dict[str, Any]:
    """Deliver exactly one caller-supplied message to the configured channel."""
    content = str(message or "").strip()
    if not content:
        raise ValueError("通知正文不能为空")
    clean_title = str(title or "").strip()
    body = f"# {clean_title}\n\n{content}" if clean_title else content
    sent = bool(get_notification_service().send(body))
    if not sent:
        raise RuntimeError("企业微信通知发送失败，请检查设置页渠道配置")
    return envelope(
        channel="wechat",
        sent=True,
        title=clean_title or None,
        message="通知已发送",
    )


TOOLS = (
    ToolSpec(
        name="send_custom_notification",
        description=(
            "将明确给出的正文通过已配置的企业微信发送一次。只能在用户明确要求发送且内容已确认时使用；"
            "服务端会在审批后执行。不会读取报告、批量任务或其他数据源。"
        ),
        parameters=object_schema(
            {
                "message": {"type": "string", "minLength": 1, "description": "待发送的完整正文"},
                "title": {"type": "string", "description": "可选标题"},
            },
            required=("message",),
        ),
        executor=send_custom_notification,
        category="action",
        effect="side_effect",
        max_attempts=1,
        sensitive_fields=("message",),
    ),
)


__all__ = ["TOOLS", "send_custom_notification"]
