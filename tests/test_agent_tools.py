# -*- coding: utf-8 -*-
"""Agent tool helpers tests — result formatting, compaction, and fallback.

Covers api.v1.endpoints.agent.tools:
- _format_result / _pick_fields / _trim_list
- _compact_tool_result for representative tool families
- _maybe_attach_search_fallback (normal / no-fallback / unresolvable subject)
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from api.v1.endpoints.agent.tools import (
    LLM_ARRAY_LIMIT,
    LLM_SERIES_LIMIT,
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
    _pick_fields,
    _trim_list,
)


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------

def test_format_result_serializes_to_json_str():
    out = _format_result({"a": 1, "b": "中文"})
    assert "\"a\": 1" in out
    assert "中文" in out  # ensure_ascii=False keeps CJK readable


def test_pick_fields_skips_missing_and_none():
    out = _pick_fields({"a": 1, "b": None, "c": 3}, ["a", "b", "c", "d"])
    assert out == {"a": 1, "c": 3}


def test_trim_list_returns_empty_for_non_list():
    assert _trim_list(None, 5) == []
    assert _trim_list("abc", 5) == []


def test_trim_list_applies_limit_and_field_projection():
    items = [{"k": i, "v": i * 10} for i in range(20)]
    out = _trim_list(items, 5, ["k"])
    assert len(out) == 5
    assert out[0] == {"k": 0}


def test_trim_list_keeps_raw_items_when_no_fields():
    items = ["a", "b", "c"]
    assert _trim_list(items, 2) == ["a", "b"]


# ---------------------------------------------------------------------------
# _compact_tool_result
# ---------------------------------------------------------------------------

def test_compact_non_dict_returned_as_is():
    assert _compact_tool_result("get_kline", ["raw", "list"]) == ["raw", "list"]


def test_compact_realtime_quotes_trims_and_annotates():
    items = [{"symbol": str(i), "price": i} for i in range(20)]
    out = _compact_tool_result("get_realtime_quotes", {"items": items, "total": 20})
    assert out["_tool_payload_meta"]["tool_name"] == "get_realtime_quotes"
    assert out["_tool_payload_meta"]["compacted"] is True
    assert out["total"] == 20
    assert len(out["items"]) == 12  # quotes item window


def test_compact_kline_compacts_time_series():
    series = [{"date": f"2026-01-{i:02d}"} for i in range(1, 60)]
    out = _compact_tool_result("get_kline", {"data": series, "symbol": "000001"})
    assert out["count"] == len(series)
    assert len(out["recent"]) == LLM_SERIES_LIMIT
    assert out["range"]["start"] == "2026-01-01"


def test_compact_market_status_picks_key_fields():
    out = _compact_tool_result(
        "get_market_status",
        {"is_trading_time": True, "up_count": 100, "noise": "skip"},
    )
    assert out["is_trading_time"] is True
    assert out["up_count"] == 100
    assert "noise" not in out


def test_compact_sector_list_sorts_and_windows_top_bottom():
    items = [{"change_pct": i, "name": f"s{i}"} for i in range(-5, 5)]
    out = _compact_tool_result("get_sector_list", {"items": items, "type": "industry"})
    assert out["total"] == len(items)
    assert out["top_movers"][0]["change_pct"] == 4
    assert out["bottom_movers"][0]["change_pct"] == -5


def test_compact_news_family_windows_items():
    items = [{"title": f"n{i}"} for i in range(20)]
    out = _compact_tool_result("search_news", {"items": items})
    assert len(out["items"]) == LLM_ARRAY_LIMIT
    assert out["item_count"] == 20


def test_compact_unknown_tool_returns_full_payload():
    out = _compact_tool_result("unknown_tool", {"a": 1})
    assert out["a"] == 1
    assert out["_tool_payload_meta"]["payload_policy"] == "full"
    assert out["_tool_payload_meta"]["compacted"] is False


# ---------------------------------------------------------------------------
# _maybe_attach_search_fallback
# ---------------------------------------------------------------------------

def test_maybe_attach_search_fallback_skips_when_healthy():
    result = {"items": [{"symbol": "000001"}]}
    out = _maybe_attach_search_fallback("get_realtime_quotes", {"symbol": "000001"}, result)
    assert out is result


def test_maybe_attach_search_fallback_unresolvable_subject_no_fallback_payload():
    # tool returns empty quotes -> should_fallback True, but symbol is empty
    result = {"items": []}
    out = _maybe_attach_search_fallback("get_realtime_quotes", {"symbol": ""}, result)
    assert out["fallback_status"]["used"] is False
    assert "search_fallback" not in out


def test_maybe_attach_search_fallback_attaches_fallback_on_empty_quotes():
    result = {"items": []}
    fake_payload = {"type": "price", "success": True, "results": [{"title": "t"}]}

    with patch(
        "api.v1.endpoints.agent.tools._resolve_search_subject",
        return_value=("000001", "平安银行"),
    ), patch(
        "api.v1.endpoints.agent.tools._build_search_fallback_payload",
        return_value=fake_payload,
    ):
        out = _maybe_attach_search_fallback(
            "get_realtime_quotes", {"symbol": "000001"}, result
        )

    assert out["fallback_status"]["used"] is True
    assert out["fallback_status"]["symbol"] == "000001"
    assert out["search_fallback"] == fake_payload


def test_maybe_attach_search_fallback_handles_first_symbol_in_csv():
    result = {"items": []}
    captured = {}

    def _fake_resolve(raw):
        captured["raw"] = raw
        return ("600519", "贵州茅台")

    with patch(
        "api.v1.endpoints.agent.tools._resolve_search_subject", side_effect=_fake_resolve
    ), patch(
        "api.v1.endpoints.agent.tools._build_search_fallback_payload",
        return_value={"type": "price", "success": True, "results": []},
    ):
        out = _maybe_attach_search_fallback(
            "get_realtime_quotes", {"symbol": "600519,000001"}, result
        )

    assert captured["raw"] == "600519"
    assert out["fallback_status"]["name"] == "贵州茅台"
