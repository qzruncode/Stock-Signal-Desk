# -*- coding: utf-8 -*-
"""Tests for name_to_code_resolver.

Covers:
- Local mapping (STOCK_NAME_MAP reverse)
- Code format boundary (_is_code_like, _normalize_code)
- Maintained data service identities (mocked HTTP boundary)
- No fuzzy guessing or hidden provider requests
- Ambiguous names return None
"""

import pytest
from unittest.mock import patch

from src.services.name_to_code_resolver import (
    resolve_local_name_to_code,
    resolve_name_to_code,
    _is_code_like,
    _normalize_code,
    _build_reverse_map_no_duplicates,
)


@pytest.fixture(autouse=True)
def maintained_master():
    with patch(
        "src.services.name_to_code_resolver.get_database_stock_indexes",
        return_value=({}, {}),
    ) as lookup:
        yield lookup


# ---------------------------------------------------------------------------
# _is_code_like
# ---------------------------------------------------------------------------


class TestIsCodeLike:
    def test_a_share_5_digits(self):
        assert _is_code_like("60051") is True
        assert _is_code_like("600519") is True

    def test_a_share_6_digits(self):
        assert _is_code_like("300750") is True

    def test_bse_with_exchange_hint(self):
        assert _is_code_like("920493.BJ") is True
        assert _is_code_like("BJ920493") is True

    def test_bj_exchange_hint_rejects_non_bse_code(self):
        assert _is_code_like("600519.BJ") is False
        assert _is_code_like("BJ600519") is False

    def test_hk_5_digits(self):
        assert _is_code_like("00700") is True

    def test_us_stock_letters(self):
        assert _is_code_like("AAPL") is True
        assert _is_code_like("TSLA") is True
        assert _is_code_like("BRK.B") is True

    def test_rejects_non_code(self):
        assert _is_code_like("贵州茅台") is False
        assert _is_code_like("1234") is False  # too short
        assert _is_code_like("1234567") is False  # too long
        assert _is_code_like("") is False
        assert _is_code_like("   ") is False


# ---------------------------------------------------------------------------
# _normalize_code
# ---------------------------------------------------------------------------


class TestNormalizeCode:
    def test_preserves_valid_a_share(self):
        assert _normalize_code("600519") == "600519"
        assert _normalize_code("  600519  ") == "600519"

    def test_strips_suffix(self):
        assert _normalize_code("600519.SH") == "600519"
        assert _normalize_code("000001.SZ") == "000001"
        assert _normalize_code("920493.BJ") == "920493"

    def test_strips_bse_prefix(self):
        assert _normalize_code("BJ920493") == "920493"

    def test_bj_exchange_hint_rejects_non_bse_code(self):
        assert _normalize_code("600519.BJ") is None
        assert _normalize_code("BJ600519") is None

    def test_preserves_us_stock(self):
        assert _normalize_code("AAPL") == "AAPL"
        assert _normalize_code("brk.b") == "BRK.B"

    def test_returns_none_for_invalid(self):
        assert _normalize_code("") is None
        assert _normalize_code("1234") is None
        assert _normalize_code("贵州茅台") is None


# ---------------------------------------------------------------------------
# _build_reverse_map_no_duplicates
# ---------------------------------------------------------------------------


class TestBuildReverseMapNoDuplicates:
    def test_excludes_ambiguous_names(self):
        # "阿里巴巴" maps to both BABA and 09988
        code_to_name = {"BABA": "阿里巴巴", "09988": "阿里巴巴", "600519": "贵州茅台"}
        result = _build_reverse_map_no_duplicates(code_to_name)
        assert "阿里巴巴" not in result
        assert result.get("贵州茅台") == "600519"

    def test_includes_unique_names(self):
        code_to_name = {"600519": "贵州茅台", "00700": "腾讯控股"}
        result = _build_reverse_map_no_duplicates(code_to_name)
        assert result["贵州茅台"] == "600519"
        assert result["腾讯控股"] == "00700"


# ---------------------------------------------------------------------------
# resolve_name_to_code
# ---------------------------------------------------------------------------


class TestResolveNameToCode:
    def test_code_like_input_returned_normalized(self):
        assert resolve_name_to_code("600519") == "600519"
        assert resolve_name_to_code("600519.SH") == "600519"
        assert resolve_name_to_code("920493.BJ") == "920493"
        assert resolve_name_to_code("  AAPL  ") == "AAPL"

    def test_local_map_exact_match(self):
        assert resolve_name_to_code("贵州茅台") == "600519"
        assert resolve_name_to_code("腾讯控股") == "00700"

    @patch(
        "src.services.name_to_code_resolver.get_database_stock_indexes",
        return_value=({"维宏股份": "300508"}, {"300508": "维宏股份"}),
    )
    def test_maintained_universe_is_resolved_once(self, _mock_database):
        assert resolve_name_to_code("维宏股份") == "300508"
        _mock_database.assert_called_once_with()

    @patch(
        "src.services.name_to_code_resolver.get_database_stock_indexes",
        return_value=({}, {}),
    )
    def test_unknown_identity_is_not_guessed(self, _mock_database):
        assert resolve_local_name_to_code("仅在线可解析的证券") is None
        _mock_database.assert_called_once_with()

    def test_returns_none_for_empty_or_invalid_input(self):
        assert resolve_name_to_code("") is None
        assert resolve_name_to_code("   ") is None
        assert resolve_name_to_code(None) is None  # type: ignore

    def test_ambiguous_name_returns_none(self):
        # "阿里巴巴" maps to both BABA and 09988 in STOCK_NAME_MAP
        assert resolve_name_to_code("阿里巴巴") is None

    def test_service_exact_match_when_not_in_static_aliases(self, maintained_master):
        maintained_master.return_value = (
            {"新维护证券": "600000"},
            {"600000": "新维护证券"},
        )
        assert resolve_name_to_code("新维护证券") == "600000"

    def test_fuzzy_match_is_not_used(self, maintained_master):
        maintained_master.return_value = (
            {"贵州茅台": "600519"},
            {"600519": "贵州茅台"},
        )
        assert resolve_name_to_code("贵州茅苔") is None

    def test_strict_resolution_does_not_guess_similar_company(self, maintained_master):
        maintained_master.return_value = (
            {"龙星科技": "002442"},
            {"002442": "龙星科技"},
        )
        assert resolve_name_to_code("火星科技") is None

    def test_returns_none_when_no_match(self):
        assert resolve_name_to_code("不存在的股票名称xyz") is None

    def test_skips_service_for_non_cjk_garbage_input(self, maintained_master):
        assert resolve_name_to_code("aaaaaaa") is None
        maintained_master.assert_not_called()
