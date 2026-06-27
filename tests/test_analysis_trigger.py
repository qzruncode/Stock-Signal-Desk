# -*- coding: utf-8 -*-
"""Analysis trigger helper tests — input validation and normalization.

Covers api.v1.endpoints.analysis.trigger pure helpers:
- _is_obviously_invalid_analysis_input
- _resolve_and_normalize_input (normal / invalid / name-resolution paths)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api.v1.endpoints.analysis.trigger import (
    _is_obviously_invalid_analysis_input,
    _resolve_and_normalize_input,
)


# ---------------------------------------------------------------------------
# _is_obviously_invalid_analysis_input
# ---------------------------------------------------------------------------

def test_invalid_input_empty_string_is_not_obviously_invalid():
    # empty short-circuits to False (handled by caller)
    assert _is_obviously_invalid_analysis_input("") is False


def test_invalid_input_code_like_is_not_obviously_invalid():
    # code-like inputs are validated elsewhere
    with patch(
        "api.v1.endpoints.analysis.trigger.is_code_like", return_value=True
    ):
        assert _is_obviously_invalid_analysis_input("600519") is False


def test_invalid_input_mixed_letters_and_digits_is_invalid():
    with patch(
        "api.v1.endpoints.analysis.trigger.is_code_like", return_value=False
    ):
        assert _is_obviously_invalid_analysis_input("abc123") is True


def test_invalid_input_pure_chinese_name_is_not_obviously_invalid():
    with patch(
        "api.v1.endpoints.analysis.trigger.is_code_like", return_value=False
    ):
        assert _is_obviously_invalid_analysis_input("贵州茅台") is False


def test_invalid_input_unsupported_chars_is_invalid():
    with patch(
        "api.v1.endpoints.analysis.trigger.is_code_like", return_value=False
    ):
        # emoji / unsupported punctuation fails the regex
        assert _is_obviously_invalid_analysis_input("贵州茅台🎯") is True


# ---------------------------------------------------------------------------
# _resolve_and_normalize_input
# ---------------------------------------------------------------------------

def test_resolve_empty_returns_empty_string():
    assert _resolve_and_normalize_input("") == ""
    assert _resolve_and_normalize_input("   ") == ""


def test_resolve_code_like_passes_through_canonical():
    with patch(
        "api.v1.endpoints.analysis.trigger.is_code_like", return_value=True
    ), patch(
        "api.v1.endpoints.analysis.trigger.canonical_stock_code",
        return_value="sh600519",
    ):
        assert _resolve_and_normalize_input("600519") == "sh600519"


def test_resolve_name_resolves_to_canonical_code():
    with patch(
        "api.v1.endpoints.analysis.trigger.is_code_like", return_value=False
    ), patch(
        "api.v1.endpoints.analysis.trigger.resolve_name_to_code",
        return_value="600519",
    ), patch(
        "api.v1.endpoints.analysis.trigger.canonical_stock_code",
        return_value="sh600519",
    ):
        assert _resolve_and_normalize_input("贵州茅台") == "sh600519"


def test_resolve_unresolvable_name_raises_400():
    from fastapi import HTTPException

    with patch(
        "api.v1.endpoints.analysis.trigger.is_code_like", return_value=False
    ), patch(
        "api.v1.endpoints.analysis.trigger.resolve_name_to_code",
        return_value=None,
    ):
        with pytest.raises(HTTPException) as exc:
            _resolve_and_normalize_input("不存在的名字XYZ")
        assert exc.value.status_code == 400
