# -*- coding: utf-8 -*-
"""Field metadata for the supported notification settings."""

from __future__ import annotations

from typing import Any, Dict


_FIELD_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "REPORT_LANGUAGE": {
        "title": "Report Language",
        "description": "Default language for generated analysis text. Supported values: zh, en.",
        "category": "notification",
        "data_type": "string",
        "ui_control": "select",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "zh",
        "options": [{"label": "Chinese", "value": "zh"}, {"label": "English", "value": "en"}],
        "validation": {"enum": ["zh", "en"]},
        "display_order": 20,
        "help_key": "settings.notification.report_output",
        "examples": ["REPORT_LANGUAGE=zh", "REPORT_LANGUAGE=en"],
        "docs": [
            {
                "label": "完整指南：环境变量完整列表",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#环境变量完整列表",
            }
        ],
        "warning_codes": [],
    },
    "WECHAT_WEBHOOK_URL": {
        "title": "企业微信 Webhook URL",
        "description": "企业微信群机器人 Webhook 地址，用于发送 Agent 通知。",
        "category": "notification",
        "data_type": "string",
        "ui_control": "password",
        "is_sensitive": True,
        "is_required": False,
        "is_editable": True,
        "default_value": None,
        "options": [],
        "validation": {"item_type": "url", "allowed_schemes": ["https"]},
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
