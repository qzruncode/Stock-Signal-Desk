# -*- coding: utf-8 -*-
"""Tests for the active configuration registry surface."""

import unittest

from src.core.config_registry import build_schema_response, get_field_definition, get_registered_field_keys


class TestActiveConfigRegistry(unittest.TestCase):
    def test_notification_registry_only_exposes_supported_settings(self):
        notification = get_field_definition("WECHAT_WEBHOOK_URL")
        self.assertEqual(notification["category"], "notification")
        self.assertTrue(notification["is_sensitive"])
        self.assertEqual(notification["ui_control"], "password")

        schema = build_schema_response()
        category = next(item for item in schema["categories"] if item["category"] == "notification")
        self.assertEqual({field["key"] for field in category["fields"]}, {"REPORT_LANGUAGE", "WECHAT_WEBHOOK_URL"})

    def test_removed_notification_and_queue_fields_are_not_registered(self):
        removed_keys = {
            "MAX_WORKERS",
            "REPORT_TYPE",
            "REPORT_SUMMARY_ONLY",
            "REPORT_SHOW_LLM_MODEL",
            "REPORT_HISTORY_COMPARE_N",
            "REPORT_RENDERER_ENABLED",
            "REPORT_TEMPLATES_DIR",
            "NOTIFICATION_DAILY_DIGEST_ENABLED",
            "NOTIFICATION_REPORT_CHANNELS",
            "NOTIFICATION_ALERT_CHANNELS",
            "NOTIFICATION_SYSTEM_ERROR_CHANNELS",
            "FEISHU_APP_ID",
            "TELEGRAM_BOT_TOKEN",
            "SLACK_BOT_TOKEN",
        }
        self.assertTrue(removed_keys.isdisjoint(get_registered_field_keys()))

    def test_representative_active_fields_have_help_metadata(self):
        for key in ("STOCK_LIST", "LLM_TEMPERATURE", "NEWS_STRATEGY_PROFILE", "WECHAT_WEBHOOK_URL"):
            field = get_field_definition(key)
            self.assertTrue(field.get("help_key"), key)
            self.assertTrue(field.get("examples"), key)
            self.assertTrue(field.get("docs"), key)

    def test_sensitive_fields_use_password_control(self):
        schema = build_schema_response()
        violations = [
            field["key"]
            for category in schema["categories"]
            for field in category["fields"]
            if field.get("is_sensitive") and field.get("ui_control") != "password"
        ]
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
