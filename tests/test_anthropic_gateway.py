# -*- coding: utf-8 -*-
"""Tests for the shared Anthropic gateway helper (src/llm/anthropic_gateway).

Covers: full-config shape, each-missing-field raises, [1m] context-window
suffix stripping, ANSI/SGR fragment cleaning, and kwargs assembly defaults.
"""
import unittest
from unittest import mock

from src.llm.anthropic_gateway import (
    AnthropicGatewayConfigError,
    build_litellm_kwargs,
    resolve_anthropic_gateway_config,
)


_FULL_ENV = {
    "ANTHROPIC_BASE_URL": "https://gw.example.com",
    "ANTHROPIC_AUTH_TOKEN": "sk-test-token",
    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
}


class TestResolveGatewayConfig(unittest.TestCase):
    """resolve_anthropic_gateway_config: env → config dict."""

    @mock.patch.dict("os.environ", _FULL_ENV, clear=False)
    def test_full_config_shape(self):
        cfg = resolve_anthropic_gateway_config()
        self.assertEqual(cfg["model"], "claude-sonnet-4-6")
        self.assertEqual(cfg["custom_llm_provider"], "anthropic")
        self.assertEqual(cfg["api_key"], "sk-test-token")
        self.assertEqual(cfg["api_base"], "https://gw.example.com")
        self.assertEqual(cfg["extra_headers"], {"authorization": "Bearer sk-test-token"})
        self.assertEqual(cfg["context_window"], 200_000)

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_all_missing_raises(self):
        with self.assertRaises(AnthropicGatewayConfigError) as ctx:
            resolve_anthropic_gateway_config()
        msg = str(ctx.exception)
        # 三个缺失字段都应在报错文案中
        self.assertIn("ANTHROPIC_BASE_URL", msg)
        self.assertIn("ANTHROPIC_AUTH_TOKEN", msg)
        self.assertIn("ANTHROPIC_MODEL", msg)
        self.assertIn("设置 - 模型设置", msg)

    @mock.patch.dict(
        "os.environ",
        {
            "ANTHROPIC_BASE_URL": "https://gw.example.com",
            "ANTHROPIC_AUTH_TOKEN": "sk-test-token",
        },
        clear=True,
    )
    def test_missing_model_raises(self):
        with self.assertRaises(AnthropicGatewayConfigError) as ctx:
            resolve_anthropic_gateway_config()
        self.assertIn("主模型(ANTHROPIC_MODEL)", str(ctx.exception))

    @mock.patch.dict(
        "os.environ",
        {
            "ANTHROPIC_AUTH_TOKEN": "sk-test-token",
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        },
        clear=True,
    )
    def test_missing_base_url_raises(self):
        with self.assertRaises(AnthropicGatewayConfigError) as ctx:
            resolve_anthropic_gateway_config()
        self.assertIn("接入地址(ANTHROPIC_BASE_URL)", str(ctx.exception))

    @mock.patch.dict(
        "os.environ",
        {
            "ANTHROPIC_BASE_URL": "https://gw.example.com",
            "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        },
        clear=True,
    )
    def test_missing_token_raises(self):
        with self.assertRaises(AnthropicGatewayConfigError) as ctx:
            resolve_anthropic_gateway_config()
        self.assertIn("鉴权令牌(ANTHROPIC_AUTH_TOKEN)", str(ctx.exception))

    @mock.patch.dict(
        "os.environ",
        {
            **_FULL_ENV,
            "ANTHROPIC_MODEL": "claude-sonnet-4-6 [1m]",
        },
        clear=True,
    )
    def test_1m_suffix_strips_and_sets_window(self):
        cfg = resolve_anthropic_gateway_config()
        self.assertEqual(cfg["model"], "claude-sonnet-4-6")
        self.assertEqual(cfg["context_window"], 1_000_000)

    @mock.patch.dict(
        "os.environ",
        {
            **_FULL_ENV,
            "ANTHROPIC_MODEL": "claude-sonnet-4-6 [1M]",
        },
        clear=True,
    )
    def test_1m_suffix_case_insensitive(self):
        cfg = resolve_anthropic_gateway_config()
        self.assertEqual(cfg["model"], "claude-sonnet-4-6")
        self.assertEqual(cfg["context_window"], 1_000_000)

    @mock.patch.dict(
        "os.environ",
        {
            **_FULL_ENV,
            # 含 ANSI 转义(\x1b[1m ... \x1b[0m) + 尾部字面 SGR 残片 [0m]
            "ANTHROPIC_MODEL": "\x1b[1mclaude-sonnet-4-6\x1b[0m[0m]",
        },
        clear=True,
    )
    def test_ansi_escape_cleaned(self):
        cfg = resolve_anthropic_gateway_config()
        self.assertEqual(cfg["model"], "claude-sonnet-4-6")

    @mock.patch.dict(
        "os.environ",
        {
            "ANTHROPIC_BASE_URL": "  https://gw.example.com  ",
            "ANTHROPIC_AUTH_TOKEN": "  sk-test-token  ",
            "ANTHROPIC_MODEL": "  claude-sonnet-4-6  ",
        },
        clear=True,
    )
    def test_surrounding_whitespace_stripped(self):
        cfg = resolve_anthropic_gateway_config()
        self.assertEqual(cfg["api_base"], "https://gw.example.com")
        self.assertEqual(cfg["api_key"], "sk-test-token")
        self.assertEqual(cfg["model"], "claude-sonnet-4-6")

    @mock.patch.dict(
        "os.environ",
        {
            **_FULL_ENV,
            "ANTHROPIC_MODEL": "openai/glm-5.2",
        },
        clear=True,
    )
    def test_provider_prefix_kept(self):
        """provider/model 前缀原样保留，custom_llm_provider 仍强制 anthropic。"""
        cfg = resolve_anthropic_gateway_config()
        self.assertEqual(cfg["model"], "openai/glm-5.2")
        self.assertEqual(cfg["custom_llm_provider"], "anthropic")

    @mock.patch.dict(
        "os.environ",
        {
            **_FULL_ENV,
            "ANTHROPIC_MODEL": "[1m]model",
        },
        clear=True,
    )
    def test_1m_suffix_only_at_tail(self):
        """[1m] 仅在末尾才算窗口后缀；在开头不算。"""
        cfg = resolve_anthropic_gateway_config()
        self.assertEqual(cfg["model"], "[1m]model")
        self.assertEqual(cfg["context_window"], 200_000)


class TestParseModelContextWindow(unittest.TestCase):
    """_parse_model_context_window: 后缀解析边界（独立单元测试）。"""

    def _parse(self, name):
        from src.llm.anthropic_gateway import _parse_model_context_window

        return _parse_model_context_window(name)

    def test_1m_suffix_strips_and_sets_window(self):
        self.assertEqual(self._parse("openai/glm-5.2[1m]"), ("openai/glm-5.2", 1_000_000))

    def test_no_suffix_default_window(self):
        self.assertEqual(self._parse("openai/glm-5.2"), ("openai/glm-5.2", 200_000))

    def test_case_insensitive(self):
        self.assertEqual(self._parse("GLM[1M]"), ("GLM", 1_000_000))

    def test_internal_spaces_tolerated(self):
        self.assertEqual(self._parse("model[ 1m ]"), ("model", 1_000_000))

    def test_suffix_in_middle_not_stripped(self):
        self.assertEqual(self._parse("[1m]model"), ("[1m]model", 200_000))


class TestBuildLitellmKwargs(unittest.TestCase):
    """build_litellm_kwargs: config → litellm call kwargs."""

    def _cfg(self):
        return {
            "model": "claude-sonnet-4-6",
            "custom_llm_provider": "anthropic",
            "api_key": "sk-test-token",
            "api_base": "https://gw.example.com",
            "extra_headers": {"authorization": "Bearer sk-test-token"},
            "context_window": 200_000,
        }

    def test_stream_flag_set(self):
        kwargs = build_litellm_kwargs(self._cfg(), stream=True, messages=[{"role": "user", "content": "hi"}])
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["model"], "claude-sonnet-4-6")
        self.assertEqual(kwargs["api_key"], "sk-test-token")
        self.assertEqual(kwargs["api_base"], "https://gw.example.com")
        self.assertEqual(kwargs["custom_llm_provider"], "anthropic")
        self.assertEqual(kwargs["extra_headers"], {"authorization": "Bearer sk-test-token"})
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "hi"}])

    def test_stream_false(self):
        kwargs = build_litellm_kwargs(self._cfg(), stream=False)
        self.assertFalse(kwargs["stream"])

    def test_extra_kwargs_merged(self):
        kwargs = build_litellm_kwargs(
            self._cfg(),
            stream=False,
            messages=[],
            tools=[{"type": "function", "function": {"name": "f"}}],
            tool_choice="auto",
            max_tokens=1024,
        )
        self.assertEqual(kwargs["tool_choice"], "auto")
        self.assertEqual(kwargs["max_tokens"], 1024)
        self.assertEqual(len(kwargs["tools"]), 1)

    def test_missing_auth_fields_omitted(self):
        cfg = {"model": "claude-sonnet-4-6"}  # 无 api_key/api_base 等
        kwargs = build_litellm_kwargs(cfg, stream=False, messages=[])
        self.assertEqual(kwargs["model"], "claude-sonnet-4-6")
        self.assertNotIn("api_key", kwargs)
        self.assertNotIn("api_base", kwargs)
        self.assertNotIn("custom_llm_provider", kwargs)
        self.assertNotIn("extra_headers", kwargs)


if __name__ == "__main__":
    unittest.main()
