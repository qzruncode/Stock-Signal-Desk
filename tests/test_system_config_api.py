# -*- coding: utf-8 -*-
"""Integration tests for system configuration API endpoints."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from api.v1.endpoints import system_config
from api.v1.schemas.system_config import (
    TestNotificationChannelRequest as NotificationChannelTestRequest,
    UpdateSystemConfigRequest,
)
import src.auth as auth
from src.config import Config
from src.core.config_manager import ConfigManager
from src.services.system_config_service import SystemConfigService


class SystemConfigApiTestCase(unittest.TestCase):
    """System config API tests in isolation without loading the full app."""

    def setUp(self) -> None:
        auth._auth_enabled = None
        auth._session_secret = None
        auth._password_hash_salt = None
        auth._password_hash_stored = None
        auth._rate_limit = {}

        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519,000001",
                    "GEMINI_API_KEY=secret-key-value",
                    "SCHEDULE_TIME=18:00",
                    "LOG_LEVEL=INFO",
                    "ADMIN_AUTH_ENABLED=true",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self._orig_dsa_desktop_mode = os.environ.get("DSA_DESKTOP_MODE")
        self._orig_database_path = os.environ.get("DATABASE_PATH")
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(Path(self.temp_dir.name) / "system_config_api_test.db")
        Config.reset_instance()

        self.manager = ConfigManager(env_path=self.env_path)
        self.service = SystemConfigService(manager=self.manager)

    def tearDown(self) -> None:
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        if self._orig_dsa_desktop_mode is None:
            os.environ.pop("DSA_DESKTOP_MODE", None)
        else:
            os.environ["DSA_DESKTOP_MODE"] = self._orig_dsa_desktop_mode
        if self._orig_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self._orig_database_path
        self.temp_dir.cleanup()

    def test_get_config_masks_secret_value(self) -> None:
        payload = system_config.get_system_config(include_schema=True, service=self.service).model_dump(by_alias=True)
        item_map = {item["key"]: item for item in payload["items"]}
        self.assertEqual(item_map["GEMINI_API_KEY"]["value"], "******")
        self.assertTrue(item_map["GEMINI_API_KEY"]["is_masked"])
        self.assertNotIn("secret-key-value", repr(payload))

    def test_get_config_schema_includes_help_metadata(self) -> None:
        payload = system_config.get_system_config(include_schema=True, service=self.service).model_dump(by_alias=True)
        item_map = {item["key"]: item for item in payload["items"]}
        stock_schema = item_map["STOCK_LIST"]["schema"]

        self.assertEqual(stock_schema["help_key"], "settings.base.STOCK_LIST")
        self.assertTrue(stock_schema["examples"])
        self.assertTrue(stock_schema["docs"])

    def test_get_config_schema_includes_notification_noise_fields(self) -> None:
        payload = system_config.get_system_config(include_schema=True, service=self.service).model_dump(by_alias=True)
        item_map = {item["key"]: item for item in payload["items"]}

        self.assertEqual(item_map["NOTIFICATION_DEDUP_TTL_SECONDS"]["schema"]["data_type"], "integer")
        self.assertEqual(item_map["NOTIFICATION_COOLDOWN_SECONDS"]["schema"]["data_type"], "integer")
        self.assertEqual(item_map["NOTIFICATION_DAILY_DIGEST_ENABLED"]["schema"]["data_type"], "boolean")
        min_severity_schema = item_map["NOTIFICATION_MIN_SEVERITY"]["schema"]
        self.assertEqual(min_severity_schema["options"][0]["value"], "")
        self.assertIn("", min_severity_schema["validation"]["enum"])
        self.assertIn("warning", min_severity_schema["validation"]["enum"])

    def test_get_setup_status_returns_readiness_payload(self) -> None:
        self.env_path.write_text(
            "\n".join(
                [
                    "ANTHROPIC_BASE_URL=https://gw.example.com",
                    "ANTHROPIC_AUTH_TOKEN=secret-token",
                    "ANTHROPIC_MODEL=claude-sonnet-4-6",
                    "STOCK_LIST=600519",
                    "ADMIN_AUTH_ENABLED=false",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {}, clear=True):
            payload = system_config.get_setup_status(service=self.service).model_dump()

        self.assertTrue(payload["is_complete"])
        self.assertTrue(payload["ready_for_smoke"])
        self.assertEqual(payload["required_missing_keys"], [])
        check_map = {check["key"]: check for check in payload["checks"]}
        self.assertEqual(check_map["llm_primary"]["status"], "configured")
        self.assertEqual(check_map["llm_agent"]["status"], "inherited")

    def test_put_config_updates_secret_and_plain_field(self) -> None:
        current = system_config.get_system_config(include_schema=False, service=self.service).model_dump()
        payload = system_config.update_system_config(
            request=UpdateSystemConfigRequest(
                config_version=current["config_version"],
                mask_token="******",
                reload_now=False,
                items=[
                    {"key": "GEMINI_API_KEY", "value": "new-secret-value"},
                    {"key": "STOCK_LIST", "value": "600519,300750"},
                ],
            ),
            service=self.service,
        ).model_dump()

        self.assertEqual(payload["applied_count"], 2)
        self.assertEqual(payload["skipped_masked_count"], 0)

        env_content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("STOCK_LIST=600519,300750", env_content)
        self.assertIn("GEMINI_API_KEY=new-secret-value", env_content)

    def test_put_config_returns_conflict_when_version_is_stale(self) -> None:
        with self.assertRaises(HTTPException) as context:
            system_config.update_system_config(
                request=UpdateSystemConfigRequest(
                    config_version="stale-version",
                    items=[{"key": "STOCK_LIST", "value": "600519"}],
                ),
                service=self.service,
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail["error"], "config_version_conflict")

    def test_put_config_preserves_comments_and_blank_lines(self) -> None:
        self.env_path.write_text(
            "\n".join(
                [
                    "# Base settings",
                    "STOCK_LIST=600519,000001",
                    "",
                    "# Secrets",
                    "GEMINI_API_KEY=secret-key-value",
                    "ADMIN_AUTH_ENABLED=false",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        current = system_config.get_system_config(include_schema=False, service=self.service).model_dump()
        payload = system_config.update_system_config(
            request=UpdateSystemConfigRequest(
                config_version=current["config_version"],
                mask_token="******",
                reload_now=False,
                items=[{"key": "STOCK_LIST", "value": "600519,300750"}],
            ),
            service=self.service,
        ).model_dump()

        self.assertTrue(payload["success"])
        env_content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("# Base settings\n", env_content)
        self.assertIn("\n\n# Secrets\n", env_content)
        self.assertIn("STOCK_LIST=600519,300750\n", env_content)

    def test_put_config_returns_startup_only_schedule_warning(self) -> None:
        current = system_config.get_system_config(include_schema=False, service=self.service).model_dump()
        payload = system_config.update_system_config(
            request=UpdateSystemConfigRequest(
                config_version=current["config_version"],
                reload_now=True,
                items=[
                    {"key": "RUN_IMMEDIATELY", "value": "false"},
                    {"key": "SCHEDULE_RUN_IMMEDIATELY", "value": "true"},
                ],
            ),
            service=self.service,
        ).model_dump()

        self.assertTrue(payload["success"])
        run_warning = next(warning for warning in payload["warnings"] if "RUN_IMMEDIATELY 已写入 .env" in warning)
        schedule_warning = next(warning for warning in payload["warnings"] if "SCHEDULE_RUN_IMMEDIATELY" in warning)

        self.assertIn("非 schedule 模式", run_warning)
        self.assertNotIn("以 schedule 模式", run_warning)
        self.assertIn("不会因为本次保存启动、停止或重建 scheduler", schedule_warning)
        self.assertIn("以 schedule 模式重新启动后生效", schedule_warning)
        self.assertNotIn("它属于启动期单次运行配置", schedule_warning)

    def test_test_notification_channel_endpoint_returns_service_payload(self) -> None:
        with patch.object(
            self.service,
            "test_notification_channel",
            return_value={
                "success": True,
                "message": "notification ok",
                "error_code": None,
                "stage": "notification_send",
                "retryable": False,
                "latency_ms": 42,
                "attempts": [
                    {
                        "channel": "wechat",
                        "success": True,
                        "message": "sent",
                        "target": "https://qyapi.example.com/cgi-bin/webhook/send?key=***",
                        "error_code": None,
                        "stage": "notification_send",
                        "retryable": False,
                        "latency_ms": 42,
                        "http_status": 200,
                    }
                ],
            },
        ) as mock_test:
            payload = system_config.test_notification_channel(
                request=NotificationChannelTestRequest(
                    channel="wechat",
                    items=[{"key": "WECHAT_WEBHOOK_URL", "value": "https://example.com/hook"}],
                    title="DSA 通知测试",
                    content="hello",
                    timeout_seconds=5,
                ),
                service=self.service,
            ).model_dump()

        self.assertTrue(payload["success"])
        self.assertEqual(payload["attempts"][0]["channel"], "wechat")
        self.assertEqual(payload["attempts"][0]["latency_ms"], 42)
        mock_test.assert_called_once()
        self.assertEqual(mock_test.call_args.kwargs["channel"], "wechat")
        self.assertEqual(mock_test.call_args.kwargs["timeout_seconds"], 5)

    def test_test_notification_channel_schema_accepts_p6_channels(self) -> None:
        ntfy_request = NotificationChannelTestRequest(
            channel="ntfy",
            items=[{"key": "NTFY_URL", "value": "https://ntfy.sh/dsa-topic"}],
            title="DSA 通知测试",
            content="hello",
            timeout_seconds=5,
        )
        gotify_request = NotificationChannelTestRequest(
            channel="gotify",
            items=[
                {"key": "GOTIFY_URL", "value": "https://gotify.example"},
                {"key": "GOTIFY_TOKEN", "value": "app-token"},
            ],
            title="DSA 通知测试",
            content="hello",
            timeout_seconds=5,
        )

        self.assertEqual(ntfy_request.channel, "ntfy")
        self.assertEqual(gotify_request.channel, "gotify")


if __name__ == "__main__":
    unittest.main()
