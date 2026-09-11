# -*- coding: utf-8 -*-
"""Tests for backward-compatible config env aliases and TickFlow loading."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Config, setup_env


class ConfigEnvCompatibilityTestCase(unittest.TestCase):
    def tearDown(self):
        Config.reset_instance()

    @patch("src.config.setup_env")
    def test_load_from_env_uses_stable_fundamental_timeout_defaults(self, _mock_setup_env):
        with patch.dict(
            os.environ,
            {
                "STOCK_LIST": "600519",
            },
            clear=True,
        ):
            config = Config._load_from_env()

        self.assertEqual(config.fundamental_stage_timeout_seconds, 8.0)
        self.assertEqual(config.fundamental_fetch_timeout_seconds, 3.0)

    @patch("src.config.config_dataclass.setup_env")
    def test_load_from_env_uses_configured_wechat_webhook_url(
        self,
        _mock_setup_env,
    ) -> None:
        webhook_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key"

        with patch.dict(
            os.environ,
            {"WECHAT_WEBHOOK_URL": webhook_url},
            clear=True,
        ):
            config = Config._load_from_env()

        self.assertEqual(config.wechat_webhook_url, webhook_url)

    @patch("src.config.config_dataclass.setup_env")
    def test_load_from_env_does_not_enable_wechat_without_a_url(
        self,
        _mock_setup_env,
    ) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = Config._load_from_env()

        self.assertEqual(config.wechat_webhook_url, "")

    @patch("src.config.setup_env")
    def test_report_language_prefers_preexisting_process_env_over_env_file(
        self,
        _mock_setup_env,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("REPORT_LANGUAGE=zh\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "ENV_FILE": str(env_path),
                    "REPORT_LANGUAGE": "en",
                },
                clear=True,
            ):
                config = Config._load_from_env()

        self.assertEqual(config.report_language, "en")

    @patch("src.config.setup_env")
    def test_report_language_uses_env_file_when_process_env_is_absent(
        self,
        _mock_setup_env,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("REPORT_LANGUAGE=en\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "ENV_FILE": str(env_path),
                },
                clear=True,
            ):
                config = Config._load_from_env()

        self.assertEqual(config.report_language, "en")

    def test_stock_list_reloads_from_updated_env_file_after_runtime_refresh(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "STOCK_LIST=600519",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "ENV_FILE": str(env_path),
                    "STOCK_LIST": "600519",
                },
                clear=True,
            ):
                Config._load_from_env()
                env_path.write_text(
                    "\n".join(
                        [
                            "STOCK_LIST=300750,TSLA",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                Config.reset_instance()
                setup_env(override=True)
                config = Config._load_from_env()

        self.assertEqual(config.stock_list, ["300750", "TSLA"])

    def test_stock_list_prefers_process_env_when_values_differ(
        self,
    ) -> None:
        """When process env explicitly sets STOCK_LIST to a value
        that differs from .env (e.g. via docker-compose ``environment:``),
        the process env must win because ``_capture_bootstrap_runtime_env_overrides``
        runs before dotenv loads and the mismatch proves an intentional override.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "STOCK_LIST=300750,TSLA",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "ENV_FILE": str(env_path),
                    "STOCK_LIST": "600519,000001",
                },
                clear=True,
            ):
                config = Config._load_from_env()

        # Explicit process env overrides win when values differ from .env
        self.assertEqual(config.stock_list, ["600519", "000001"])

    def test_stock_list_uses_process_env_when_absent_from_file(
        self,
    ) -> None:
        """When STOCK_LIST exists only in process env (not in .env),
        it IS a genuine explicit override and must be honoured.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            # .env has no STOCK_LIST key.
            env_path.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "ENV_FILE": str(env_path),
                    "STOCK_LIST": "600519,000001",
                },
                clear=True,
            ):
                config = Config._load_from_env()

        self.assertEqual(config.stock_list, ["600519", "000001"])

    def test_parse_report_language_accepts_known_alias_without_warning(self) -> None:
        with self.assertNoLogs("src.config", level="WARNING"):
            parsed = Config._parse_report_language("zh-cn")

        self.assertEqual(parsed, "zh")
