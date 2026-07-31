"""Return redacted notification-channel readiness."""

from __future__ import annotations

from typing import Any

from src.services.system_config_service import SystemConfigService
from src.tools._workflow import envelope
from src.tools.base import ToolSpec, object_schema


def get_notification_status() -> dict[str, Any]:
    config = SystemConfigService().get_config(include_schema=True)
    fields = {
        item.get("key"): item
        for item in config.get("items") or []
        if (item.get("schema") or {}).get("category") == "notification"
    }
    webhook = fields.get("WECHAT_WEBHOOK_URL") or {}
    configured = bool(webhook.get("raw_value_exists"))
    return envelope(
        channel_count=1,
        channels=[
            {
                "channel": "wechat",
                "name": "企业微信",
                "configured": configured,
                "enabled": configured,
                "test_supported": True,
            }
        ],
        configured_count=1 if configured else 0,
        settings_path="/setting?section=notification",
        message="企业微信通知已配置" if configured else "请先在设置页配置企业微信 Webhook",
    )


TOOL = ToolSpec(
    name="get_notification_status",
    description="检查通知渠道是否已配置，只返回脱敏状态，不读取或暴露 Webhook、Token 等敏感值。",
    parameters=object_schema(),
    executor=get_notification_status,
    category="action",
)


__all__ = ["TOOL", "get_notification_status"]
