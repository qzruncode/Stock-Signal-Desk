"""Send an explicitly requested notification through configured channels."""

from __future__ import annotations

from typing import Any

from src.notification import get_notification_service
from src.services.history_service import HistoryService
from src.tools._workflow import envelope, model_dump, require_confirmation, run_async
from src.tools.base import ToolSpec, object_schema


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


TOOL = ToolSpec(
    name="send_notification",
    description=(
        "通过设置页已配置的企业微信发送正式报告、批量报告或用户指定内容。只有用户明确说发送/通知且目标内容清晰时"
        "才能调用并传 confirmed=true；生成报告、分析或推荐本身不代表同意发送。优先传报告 ID，避免重新拼写报告。"
    ),
    parameters=object_schema(
        {
            "content_type": {"type": "string", "enum": ["analysis_report", "batch_report", "custom"]},
            "message": {"type": "string", "description": "custom 时的正文"},
            "record_id": {"type": "string", "description": "analysis_report 的历史记录 ID"},
            "batch_run_id": {"type": "string", "description": "batch_report 的批次 ID"},
            "title": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "用户本轮是否明确要求发送", "default": False},
        },
        required=("content_type", "confirmed"),
    ),
    executor=send_notification,
    category="action",
)


__all__ = ["TOOL", "send_notification"]
