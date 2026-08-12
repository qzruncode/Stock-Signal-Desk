# -*- coding: utf-8 -*-
"""Unit tests for system configuration service."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import Mock, patch

import requests

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.config import Config
from src.core.config_manager import ConfigManager
from src.services.system_config_service import ConfigConflictError, ConfigImportError, SystemConfigService


class SystemConfigServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519,000001",
                    "GEMINI_API_KEY=secret-key-value",
                    "SCHEDULE_TIME=18:00",
                    "LOG_LEVEL=INFO",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        Config.reset_instance()

        self.manager = ConfigManager(env_path=self.env_path)
        self.service = SystemConfigService(manager=self.manager)

    def tearDown(self) -> None:
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        self.temp_dir.cleanup()

    def _rewrite_env(self, *lines: str) -> None:
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        Config.reset_instance()
        self.manager = ConfigManager(env_path=self.env_path)
        self.service = SystemConfigService(manager=self.manager)

    @staticmethod
    def _mock_completion_response(content: str = "OK", tool_calls=None):
        message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_get_config_masks_sensitive_values_server_side(self) -> None:
        payload = self.service.get_config(include_schema=True)
        items = {item["key"]: item for item in payload["items"]}

        self.assertIn("GEMINI_API_KEY", items)
        self.assertEqual(items["GEMINI_API_KEY"]["value"], "******")
        self.assertTrue(items["GEMINI_API_KEY"]["is_masked"])
        self.assertTrue(items["GEMINI_API_KEY"]["raw_value_exists"])
        self.assertNotIn("secret-key-value", repr(payload))

    def test_config_manager_hardens_rewritten_env_permissions(self) -> None:
        old_version = self.manager.get_config_version()
        self.service.update(
            config_version=old_version,
            items=[{"key": "STOCK_LIST", "value": "600519,300750"}],
            reload_now=False,
        )

        self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)

    def test_get_config_uses_switch_default_for_missing_report_model_toggle(self) -> None:
        payload = self.service.get_config(include_schema=True)
        items = {item["key"]: item for item in payload["items"]}

        self.assertEqual(items["REPORT_SHOW_LLM_MODEL"]["value"], "true")
        self.assertFalse(items["REPORT_SHOW_LLM_MODEL"]["raw_value_exists"])

        self._rewrite_env(
            "STOCK_LIST=600519,000001",
            "GEMINI_API_KEY=secret-key-value",
            "SCHEDULE_TIME=18:00",
            "LOG_LEVEL=INFO",
            "REPORT_SHOW_LLM_MODEL=false",
        )

        payload = self.service.get_config(include_schema=True)
        items = {item["key"]: item for item in payload["items"]}

        self.assertEqual(items["REPORT_SHOW_LLM_MODEL"]["value"], "false")
        self.assertTrue(items["REPORT_SHOW_LLM_MODEL"]["raw_value_exists"])

    def test_get_config_preserves_explicit_empty_switch_value(self) -> None:
        self._rewrite_env(
            "STOCK_LIST=600519,000001",
            "GEMINI_API_KEY=secret-key-value",
            "SCHEDULE_TIME=18:00",
            "LOG_LEVEL=INFO",
            "WEBHOOK_VERIFY_SSL=",
        )

        payload = self.service.get_config(include_schema=True)
        items = {item["key"]: item for item in payload["items"]}

        self.assertEqual(items["WEBHOOK_VERIFY_SSL"]["value"], "")
        self.assertTrue(items["WEBHOOK_VERIFY_SSL"]["raw_value_exists"])

    def test_get_config_preserves_explicit_empty_report_show_llm_model_value(self) -> None:
        self._rewrite_env(
            "STOCK_LIST=600519,000001",
            "GEMINI_API_KEY=secret-key-value",
            "SCHEDULE_TIME=18:00",
            "LOG_LEVEL=INFO",
            "REPORT_SHOW_LLM_MODEL=",
        )

        payload = self.service.get_config(include_schema=True)
        items = {item["key"]: item for item in payload["items"]}

        self.assertEqual(items["REPORT_SHOW_LLM_MODEL"]["value"], "")
        self.assertTrue(items["REPORT_SHOW_LLM_MODEL"]["raw_value_exists"])

    def test_get_setup_status_reports_required_gaps_for_empty_config(self) -> None:
        self._rewrite_env("")

        with patch.dict(os.environ, {}, clear=True):
            status = self.service.get_setup_status()

        self.assertFalse(status["is_complete"])
        self.assertFalse(status["ready_for_smoke"])
        self.assertEqual(status["next_step_key"], "llm_primary")
        self.assertIn("llm_primary", status["required_missing_keys"])
        self.assertIn("stock_list", status["required_missing_keys"])

    def test_get_setup_status_marks_minimal_config_complete(self) -> None:
        self._rewrite_env(
            "ANTHROPIC_BASE_URL=https://gw.example.com",
            "ANTHROPIC_AUTH_TOKEN=secret-token",
            "ANTHROPIC_MODEL=claude-sonnet-4-6",
            "STOCK_LIST=600519",
        )

        with patch.dict(os.environ, {}, clear=True):
            status = self.service.get_setup_status()

        checks = {check["key"]: check for check in status["checks"]}
        self.assertTrue(status["is_complete"])
        self.assertTrue(status["ready_for_smoke"])
        self.assertEqual(checks["llm_primary"]["status"], "configured")
        self.assertEqual(checks["llm_agent"]["status"], "inherited")
        self.assertEqual(checks["stock_list"]["status"], "configured")
        self.assertEqual(checks["notification"]["status"], "optional")

    def test_get_setup_status_uses_runtime_env_without_reloading_singletons(self) -> None:
        self._rewrite_env("")

        with (
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_BASE_URL": "https://gw.example.com",
                    "ANTHROPIC_AUTH_TOKEN": "runtime-secret",
                    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
                    "STOCK_LIST": "600519",
                },
                clear=True,
            ),
            patch("src.services.system_config_service.Config.reset_instance") as mock_reset,
            patch("src.services.system_config_service.setup_env") as mock_setup_env,
        ):
            status = self.service.get_setup_status()

        self.assertTrue(status["is_complete"])
        mock_reset.assert_not_called()
        mock_setup_env.assert_not_called()

    def test_get_setup_status_storage_check_does_not_create_database_parent(self) -> None:
        missing_parent = Path(self.temp_dir.name) / "missing-data"
        db_path = missing_parent / "stock_analysis.db"
        self._rewrite_env(
            "ANTHROPIC_BASE_URL=https://gw.example.com",
            "ANTHROPIC_AUTH_TOKEN=secret-token",
            "ANTHROPIC_MODEL=claude-sonnet-4-6",
            "STOCK_LIST=600519",
            f"DATABASE_PATH={db_path}",
        )

        with patch.dict(os.environ, {}, clear=True):
            status = self.service.get_setup_status()

        storage_check = next(check for check in status["checks"] if check["key"] == "storage")
        self.assertEqual(storage_check["status"], "configured")
        self.assertFalse(missing_parent.exists())

    def test_update_preserves_masked_secret(self) -> None:
        old_version = self.manager.get_config_version()
        response = self.service.update(
            config_version=old_version,
            items=[
                {"key": "GEMINI_API_KEY", "value": "******"},
                {"key": "STOCK_LIST", "value": "600519,300750"},
            ],
            mask_token="******",
            reload_now=False,
        )

        self.assertTrue(response["success"])
        self.assertEqual(response["applied_count"], 1)
        self.assertEqual(response["skipped_masked_count"], 1)
        self.assertIn("STOCK_LIST", response["updated_keys"])

        current_map = self.manager.read_config_map()
        self.assertEqual(current_map["STOCK_LIST"], "600519,300750")
        self.assertEqual(current_map["GEMINI_API_KEY"], "secret-key-value")

    def test_validate_reports_invalid_time(self) -> None:
        validation = self.service.validate(items=[{"key": "SCHEDULE_TIME", "value": "25:70"}])
        self.assertFalse(validation["valid"])
        self.assertTrue(any(issue["code"] == "invalid_format" for issue in validation["issues"]))

    def test_validate_reports_invalid_feishu_webhook_url(self) -> None:
        validation = self.service.validate(items=[{"key": "FEISHU_WEBHOOK_URL", "value": "feishu-hook-without-scheme"}])
        self.assertFalse(validation["valid"])
        self.assertTrue(any(issue["code"] == "invalid_url" for issue in validation["issues"]))

    def test_validate_warns_daily_digest_is_reserved(self) -> None:
        validation = self.service.validate(items=[{"key": "NOTIFICATION_DAILY_DIGEST_ENABLED", "value": "true"}])

        self.assertTrue(validation["valid"])
        self.assertTrue(
            any(
                issue["key"] == "NOTIFICATION_DAILY_DIGEST_ENABLED"
                and issue["code"] == "reserved_notification_daily_digest"
                and issue["severity"] == "warning"
                for issue in validation["issues"]
            )
        )

    def test_validate_warns_when_feishu_app_credentials_are_used_without_webhook(self) -> None:
        validation = self.service.validate(
            items=[
                {"key": "FEISHU_APP_ID", "value": "cli_xxx"},
                {"key": "FEISHU_APP_SECRET", "value": "secret_xxx"},
            ]
        )
        self.assertTrue(validation["valid"])
        self.assertTrue(
            any(
                issue["code"] == "feishu_mode_mismatch" and issue["severity"] == "warning"
                for issue in validation["issues"]
            )
        )

    def test_validate_no_warning_when_feishu_cloud_doc_credentials_without_webhook(self) -> None:
        validation = self.service.validate(
            items=[
                {"key": "FEISHU_APP_ID", "value": "cli_xxx"},
                {"key": "FEISHU_APP_SECRET", "value": "secret_xxx"},
                {"key": "FEISHU_FOLDER_TOKEN", "value": "folder_xxx"},
            ]
        )
        self.assertTrue(validation["valid"])
        self.assertFalse(
            any(
                issue["code"] == "feishu_mode_mismatch" and issue["severity"] == "warning"
                for issue in validation["issues"]
            )
        )

    def test_validate_warns_when_only_folder_token_cleared_with_app_credentials(self) -> None:
        """Clearing FEISHU_FOLDER_TOKEN while app credentials remain should trigger mismatch."""
        old_version = self.manager.get_config_version()
        self.service.update(
            config_version=old_version,
            items=[
                {"key": "FEISHU_APP_ID", "value": "cli_xxx"},
                {"key": "FEISHU_APP_SECRET", "value": "secret_xxx"},
            ],
        )
        validation = self.service.validate(
            items=[
                {"key": "FEISHU_FOLDER_TOKEN", "value": ""},
            ]
        )
        self.assertTrue(validation["valid"])
        self.assertTrue(
            any(
                issue["code"] == "feishu_mode_mismatch" and issue["severity"] == "warning"
                for issue in validation["issues"]
            )
        )

    def test_validate_accepts_report_language_english(self) -> None:
        validation = self.service.validate(items=[{"key": "REPORT_LANGUAGE", "value": "en"}])

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["issues"], [])

    @staticmethod
    def _mock_http_response(status_code: int, json_body: Optional[Dict[str, Any]] = None):
        response = Mock()
        response.status_code = status_code
        response.text = "ok" if status_code == 200 else "error"
        response.json.return_value = json_body or {"errcode": 0}
        return response

    def _notification_test_env(self):
        return patch.dict(os.environ, {"ENV_FILE": str(self.env_path)}, clear=True)

    def test_update_with_reload_applies_updated_env_file_when_process_env_is_stale(self) -> None:
        os.environ["STOCK_LIST"] = "600519,000001"

        response = self.service.update(
            config_version=self.manager.get_config_version(),
            items=[{"key": "STOCK_LIST", "value": "300750,TSLA"}],
            reload_now=True,
        )

        self.assertTrue(response["success"])
        self.assertEqual(Config.get_instance().stock_list, ["300750", "TSLA"])

    def test_update_with_reload_applies_wechat_webhook_url(self) -> None:
        webhook_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=updated-key"

        with self._notification_test_env():
            response = self.service.update(
                config_version=self.manager.get_config_version(),
                items=[{"key": "WECHAT_WEBHOOK_URL", "value": webhook_url}],
                reload_now=True,
            )

            self.assertTrue(response["success"])
            self.assertEqual(Config.get_instance().wechat_webhook_url, webhook_url)

    def test_update_raises_conflict_for_stale_version(self) -> None:
        with self.assertRaises(ConfigConflictError):
            self.service.update(
                config_version="stale-version",
                items=[{"key": "STOCK_LIST", "value": "600519"}],
                reload_now=False,
            )

    def test_update_appends_news_window_explainability_warning(self) -> None:
        response = self.service.update(
            config_version=self.manager.get_config_version(),
            items=[
                {"key": "NEWS_STRATEGY_PROFILE", "value": "ultra_short"},
                {"key": "NEWS_MAX_AGE_DAYS", "value": "7"},
            ],
            reload_now=False,
        )

        self.assertTrue(response["success"])
        joined = " | ".join(response["warnings"])
        self.assertIn("effective_days=1", joined)
        self.assertIn("min(profile_days, NEWS_MAX_AGE_DAYS)", joined)

    def test_update_appends_max_workers_warning(self) -> None:
        response = self.service.update(
            config_version=self.manager.get_config_version(),
            items=[{"key": "MAX_WORKERS", "value": "1"}],
            reload_now=False,
        )

        self.assertTrue(response["success"])
        joined = " | ".join(response["warnings"])
        self.assertIn("MAX_WORKERS=1", joined)
        self.assertIn("reload_now=false", joined)

    def test_update_appends_mode_specific_startup_warnings(self) -> None:
        response = self.service.update(
            config_version=self.manager.get_config_version(),
            items=[
                {"key": "RUN_IMMEDIATELY", "value": "false"},
                {"key": "SCHEDULE_ENABLED", "value": "true"},
                {"key": "SCHEDULE_RUN_IMMEDIATELY", "value": "true"},
            ],
            reload_now=True,
        )

        self.assertTrue(response["success"])
        run_warning = next(warning for warning in response["warnings"] if "RUN_IMMEDIATELY 已写入 .env" in warning)
        schedule_warning = next(warning for warning in response["warnings"] if "SCHEDULE_ENABLED" in warning)

        self.assertIn("非 schedule 模式", run_warning)
        self.assertNotIn("以 schedule 模式", run_warning)
        self.assertIn("SCHEDULE_RUN_IMMEDIATELY", schedule_warning)
        self.assertIn("不会因为本次保存启动、停止或重建 scheduler", schedule_warning)
        self.assertIn("以 schedule 模式重新启动后生效", schedule_warning)
        self.assertNotIn("它属于启动期单次运行配置", schedule_warning)

    def test_update_appends_schedule_time_runtime_rebind_warning(self) -> None:
        response = self.service.update(
            config_version=self.manager.get_config_version(),
            items=[{"key": "SCHEDULE_TIME", "value": "09:30"}],
            reload_now=True,
        )

        self.assertTrue(response["success"])
        schedule_time_warning = next(
            warning for warning in response["warnings"] if "SCHEDULE_TIME=09:30 已写入 .env" in warning
        )

        self.assertIn("已经以 schedule 模式运行", schedule_time_warning)
        self.assertIn("自动重建 daily job", schedule_time_warning)
        self.assertIn("不会启动 scheduler", schedule_time_warning)
        self.assertNotIn("重启当前进程", schedule_time_warning)
        self.assertNotIn("不会因为本次保存启动、停止或重建 scheduler", schedule_time_warning)

    def test_update_schedule_time_blank_warning_reports_effective_default(self) -> None:
        response = self.service.update(
            config_version=self.manager.get_config_version(),
            items=[{"key": "SCHEDULE_TIME", "value": "   "}],
            reload_now=True,
        )

        self.assertTrue(response["success"])
        self.assertTrue(
            any("SCHEDULE_TIME=18:00 已写入 .env" in warning for warning in response["warnings"]),
            response["warnings"],
        )

    def test_update_appends_webui_bind_restart_warning(self) -> None:
        response = self.service.update(
            config_version=self.manager.get_config_version(),
            items=[
                {"key": "WEBUI_HOST", "value": "0.0.0.0"},
                {"key": "WEBUI_PORT", "value": "18000"},
            ],
            reload_now=True,
        )

        self.assertTrue(response["success"])
        bind_warning = next(
            warning for warning in response["warnings"] if "WEBUI_HOST" in warning and "WEBUI_PORT" in warning
        )

        self.assertIn("启动期监听配置", bind_warning)
        self.assertIn("不会因为本次保存重新绑定监听地址或端口", bind_warning)
        self.assertIn("重启当前进程、Docker 容器或服务管理器后生效", bind_warning)
