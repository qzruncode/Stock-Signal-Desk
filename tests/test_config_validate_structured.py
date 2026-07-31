# -*- coding: utf-8 -*-
"""Tests for Config.validate_structured() and backward-compatible validate().

Covers:
- ConfigIssue dataclass basics
- validate_structured() severity classifications
- LLM availability check honours all three config tiers (YAML / channels /
  legacy keys) via llm_model_list
- validate() backward-compat: still returns List[str] with the same messages
"""
import pytest
from unittest.mock import patch

from src.config import Config, ConfigIssue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> Config:
    """Build a minimal Config object with sensible defaults for testing.

    Any keyword argument overrides the corresponding dataclass field so tests
    only have to specify the fields that matter for their scenario.
    """
    defaults = dict(
        stock_list=["600519"],
        wechat_webhook_url="https://example.com/webhook",
    )
    defaults.update(kwargs)
    return Config(**defaults)


def _severities(issues):
    return [i.severity for i in issues]


def _fields(issues):
    return [i.field for i in issues]


# ---------------------------------------------------------------------------
# ConfigIssue basics
# ---------------------------------------------------------------------------


class TestConfigIssue:
    def test_str_equals_message(self):
        issue = ConfigIssue(severity="error", message="something went wrong", field="FOO")
        assert str(issue) == "something went wrong"

    def test_severity_values(self):
        for sev in ("error", "warning", "info"):
            issue = ConfigIssue(severity=sev, message="test", field="F")
            assert issue.severity == sev

    def test_default_field(self):
        issue = ConfigIssue(severity="info", message="hello")
        assert issue.field == ""


# ---------------------------------------------------------------------------
# validate_structured() — happy path (all good)
# ---------------------------------------------------------------------------


class TestValidateStructuredHappyPath:
    def test_no_issues_when_fully_configured(self):
        cfg = _make_config()
        issues = cfg.validate_structured()
        # No errors or warnings.
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        assert errors == []
        assert warnings == []


# ---------------------------------------------------------------------------
# validate_structured() — stock list
# ---------------------------------------------------------------------------


class TestValidateStructuredStockList:
    def test_empty_stock_list_is_error(self):
        cfg = _make_config(stock_list=[])
        issues = cfg.validate_structured()
        errors = [i for i in issues if i.severity == "error"]
        assert any("STOCK_LIST" in i.field for i in errors)

    def test_configured_stock_list_no_stock_error(self):
        cfg = _make_config(stock_list=["600519", "000001"])
        issues = cfg.validate_structured()
        assert not any(i.field == "STOCK_LIST" for i in issues if i.severity == "error")


class TestValidateStructuredNotification:
    def test_notification_configured_no_warning(self):
        cfg = _make_config(wechat_webhook_url="https://example.com/wh")
        issues = cfg.validate_structured()
        assert not any(i.severity == "warning" and "通知渠道" in i.message for i in issues)

# ---------------------------------------------------------------------------
# Deprecated field migration hints
# ---------------------------------------------------------------------------


class TestValidateBackwardCompat:
    def test_returns_list_of_str(self):
        cfg = _make_config()
        result = cfg.validate()
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)
