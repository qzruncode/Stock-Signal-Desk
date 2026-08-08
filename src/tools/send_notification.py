"""Send one explicitly supplied notification through configured channels.

The model-facing action intentionally sends only caller-supplied text.  Reading
a report and delivering it are separate actions, so a notification call cannot
hide a report lookup, report rendering, or provider-specific batch workflow.
"""

from __future__ import annotations

from typing import Any

from src.notification import get_notification_service
from src.services.history_service import HistoryService
from src.tools._workflow import envelope, model_dump, require_confirmation, run_async
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


def send_notification(
    content_type: str,
    message: str = "",
    record_id: str = "",
    batch_run_id: str = "",
    title: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    require_confirmation(confirmed, "发送外部通知")
    if content_type == "batch_report":
        from api.v1.endpoints.batches.run import notify_batch_run

        if not batch_run_id:
            raise ValueError("发送批量报告必须提供 batch_run_id")
        result = model_dump(run_async(notify_batch_run(batch_run_id)))
        return envelope(
            content_type=content_type,
            channel="wechat",
            batch_run_id=batch_run_id,
            sent=True,
            **result,
        )
    if content_type == "analysis_report":
        if not record_id:
            raise ValueError("发送分析报告必须提供 record_id")
        content = HistoryService().get_markdown_report(record_id)
        if not content:
            raise ValueError("分析报告不存在或内容为空")
    elif content_type == "custom":
        content = message.strip()
        if not content:
            raise ValueError("自定义通知内容不能为空")
    else:
        raise ValueError("content_type 必须是 analysis_report、batch_report 或 custom")
    body = f"# {title.strip()}\n\n{content}" if title.strip() else content
    sent = bool(get_notification_service().send(body))
    if not sent:
        raise RuntimeError("企业微信通知发送失败，请检查设置页渠道配置")
    return envelope(
        content_type=content_type,
        channel="wechat",
        record_id=record_id or None,
        title=title.strip() or None,
        sent=True,
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


__all__ = ["TOOLS", "send_custom_notification", "send_notification"]
