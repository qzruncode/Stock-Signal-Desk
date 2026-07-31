"""Notification field metadata group 3."""

from src.notification_routing import ROUTABLE_NOTIFICATION_CHANNELS

_FIELD_DEFINITIONS = {
    "TELEGRAM_BOT_TOKEN": {
        "title": "Telegram Bot Token",
        "description": "Telegram bot token (from @BotFather).",
        "category": "notification",
        "data_type": "string",
        "ui_control": "password",
        "is_sensitive": True,
        "is_required": False,
        "is_editable": True,
        "default_value": None,
        "options": [],
        "validation": {},
        "display_order": 17,
        "help_key": "settings.notification.telegram",
        "examples": ["TELEGRAM_BOT_TOKEN=123456:ABC-DEF", "TELEGRAM_CHAT_ID=-1001234567890"],
        "docs": [
            {
                "label": "完整指南：Telegram",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#telegram",
            }
        ],
        "warning_codes": ["secret_value"],
    },
    "TELEGRAM_CHAT_ID": {
        "title": "Telegram Chat ID",
        "description": "Telegram chat/group ID to send messages to.",
        "category": "notification",
        "data_type": "string",
        "ui_control": "text",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": None,
        "options": [],
        "validation": {},
        "display_order": 18,
        "help_key": "settings.notification.telegram",
        "examples": ["TELEGRAM_CHAT_ID=-1001234567890", "TELEGRAM_MESSAGE_THREAD_ID=123"],
        "docs": [
            {
                "label": "完整指南：Telegram",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#telegram",
            }
        ],
        "warning_codes": [],
    },
    "TELEGRAM_MESSAGE_THREAD_ID": {
        "title": "Telegram Thread ID",
        "description": "Telegram topic/thread ID for group messages (optional).",
        "category": "notification",
        "data_type": "string",
        "ui_control": "text",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": None,
        "options": [],
        "validation": {},
        "display_order": 19,
    },
    "WEBHOOK_VERIFY_SSL": {
        "title": "Webhook SSL Verify",
        "description": "Verify HTTPS certificates for webhook requests. Set to false ONLY for self-signed certs in trusted internal networks. WARNING: Disabling allows MITM attacks—do NOT use on public networks.",
        "category": "notification",
        "data_type": "boolean",
        "ui_control": "switch",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "true",
        "options": [],
        "validation": {},
        "display_order": 53,
        "help_key": "settings.notification.WEBHOOK_VERIFY_SSL",
        "examples": ["WEBHOOK_VERIFY_SSL=true", "WEBHOOK_VERIFY_SSL=false"],
        "docs": [
            {
                "label": "完整指南：自定义 Webhook",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#自定义-webhook",
            }
        ],
        "warning_codes": ["disabling_ssl_verify_is_risky"],
    },
    "WECHAT_WEBHOOK_URL": {
        "title": "企业微信 Webhook URL",
        "description": "在企业微信群中添加群机器人后，粘贴机器人的 Webhook 地址。URL 已包含发送通知所需的密钥。",
        "category": "notification",
        "data_type": "string",
        "ui_control": "password",
        "is_sensitive": True,
        "is_required": False,
        "is_editable": True,
        "default_value": None,
        "options": [],
        "validation": {
            "item_type": "url",
            "allowed_schemes": ["https"],
        },
        "display_order": 10,
        "help_key": "settings.notification.webhooks",
        "examples": ["WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx"],
        "docs": [
            {
                "label": "完整指南：通知渠道配置",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#通知渠道详细配置",
            }
        ],
        "warning_codes": ["webhook_secret_value"],
    },
}
