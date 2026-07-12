# -*- coding: utf-8 -*-
"""Regression tests for shareholder fetcher hardening (timeout + process cache + concurrency).

Covers api.v1.endpoints.financials._fetch_shareholders:
- _run_with_timeout returns None on timeout (no hang)
- _fetch_actual_controller uses process-level cache (no repeat full-market fetch)
- _fetch_shareholder_structure runs the 4 sub-fetchers concurrently
"""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from api.v1.endpoints.financials import _fetch_shareholders as mod


# ---------------------------------------------------------------------------
# _run_with_timeout
# ---------------------------------------------------------------------------

def test_run_with_timeout_returns_none_on_timeout():
    """A slow callable must be aborted after the timeout, returning None (not raising)."""
    def slow(*a, **kw):
        time.sleep(10)
        return "should-not-reach"

    t0 = time.time()
    out = mod._run_with_timeout(slow, 0.3)
    elapsed = time.time() - t0
    assert out is None
    # Should return well under the 10s sleep — proving it didn't wait.
    assert elapsed < 2.0


def test_run_with_timeout_returns_value_when_fast():
    assert mod._run_with_timeout(lambda: "ok", 5) == "ok"


def test_run_with_timeout_swallows_exception():
    def boom(*a, **kw):
        raise RuntimeError("network down")
    assert mod._run_with_timeout(boom, 5) is None


# ---------------------------------------------------------------------------
# _fetch_actual_controller — process cache
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_controller_cache():
    """Reset the process-level cache between tests."""
    old_data, old_ts = mod._actual_controller_cache._data, mod._actual_controller_cache._ts
    mod._actual_controller_cache._data = None
    mod._actual_controller_cache._ts = 0.0
    yield mod._actual_controller_cache
    mod._actual_controller_cache._data = old_data
    mod._actual_controller_cache._ts = old_ts


def _fake_control_df():
    return pd.DataFrame([
        {"证券代码": "600519", "证券简称": "贵州茅台", "实际控制人": "贵州省国资委"},
        {"证券代码": "000001", "证券简称": "平安银行", "实际控制人": "无"},
    ])


def test_actual_controller_caches_full_market_result(fresh_controller_cache):
    """The full-market cninfo call must happen at most once; subsequent calls hit cache."""
    with patch("akshare.stock_hold_control_cninfo", return_value=_fake_control_df()) as mocked:
        ctrl1, src1, _ = mod._fetch_actual_controller("600519")
        ctrl2, src2, _ = mod._fetch_actual_controller("000001")
        ctrl3, src3, _ = mod._fetch_actual_controller("600519")

    assert mocked.call_count == 1  # full-market fetched once, then cached
    assert ctrl1 == "贵州省国资委"
    assert ctrl2 == "无"
    assert ctrl3 == "贵州省国资委"
    assert src1 == src2 == src3 == "stock_hold_control_cninfo"
    assert fresh_controller_cache.get() is not None


def test_actual_controller_timeout_does_not_hang(fresh_controller_cache):
    """If the cninfo call times out, return gracefully without raising."""
    def slow_cninfo(*a, **kw):
        time.sleep(5)
        return _fake_control_df()

    # Temporarily lower the timeout so the 5s sleep triggers it.
    with patch.object(mod, "_SLOW_MARKET_TIMEOUT", 0.3), \
         patch("akshare.stock_hold_control_cninfo", side_effect=slow_cninfo):
        t0 = time.time()
        ctrl, src, errs = mod._fetch_actual_controller("600519")
        elapsed = time.time() - t0

    assert elapsed < 2.0
    assert ctrl is None
    assert any("timeout" in e or "empty" in e for e in errs)


# ---------------------------------------------------------------------------
# _fetch_holder_changes — degraded source has timeout protection
# ---------------------------------------------------------------------------

def test_holder_changes_primary_source_used_without_full_market_call():
    """The single-stock primary source should satisfy the call; the slow no-arg
    degraded source (stock_hold_management_detail_em) must not be invoked."""
    primary_df = pd.DataFrame([{
        "代码": "600519", "变动股东": "茅台集团", "变动方向": "增持",
        "变动数量": "100万股", "变动比例": "0.1%", "交易均价": "1500",
        "变动日期": "2026-06-01",
    }])

    with patch("akshare.stock_shareholder_change_ths", return_value=primary_df) as primary, \
         patch("akshare.stock_hold_management_detail_em") as degraded:
        records, src, errs = mod._fetch_holder_changes("600519")

    assert primary.call_count == 1
    assert degraded.call_count == 0  # primary succeeded, degraded never touched
    assert src == "stock_shareholder_change_ths"
    assert records and records[0]["holder"] == "茅台集团"


# ---------------------------------------------------------------------------
# _fetch_shareholder_structure — concurrency
# ---------------------------------------------------------------------------

def test_shareholder_structure_runs_four_sub_fetchers_concurrently():
    """All four sub-fetchers must be invoked (concurrently); results merged into one dict."""
    with patch.object(mod, "_fetch_holder_count_from_akshare",
                      return_value=({"holder_count": 100}, "gdhs", [])), \
         patch.object(mod, "_fetch_holder_count_from_tushare",
                      return_value=({}, None, [])), \
         patch.object(mod, "_fetch_top10_holders",
                      return_value=([{"name": "x"}], 5.0, "top", [])), \
         patch.object(mod, "_fetch_holder_changes",
                      return_value=([{"holder": "y"}], "changes", [])), \
         patch.object(mod, "_fetch_actual_controller",
                      return_value=("某国资委", "control", [])):
        data = mod._fetch_shareholder_structure("600519")

    assert data["holder_count"] == 100
    assert data["top10_holders"] == [{"name": "x"}]
    assert data["institution_holding_pct"] == 5.0
    assert data["major_holder_changes"] == [{"holder": "y"}]
    assert data["actual_controller"] == "某国资委"
    assert set(data["source_chain"]) == {"gdhs", "top", "changes", "control"}


def test_shareholder_structure_sub_fetcher_failure_does_not_break_others():
    """A raising sub-fetcher is captured by future.result() and must not abort the rest."""
    with patch.object(mod, "_fetch_holder_count_from_akshare",
                      return_value=({"holder_count": 100}, "gdhs", [])), \
         patch.object(mod, "_fetch_holder_count_from_tushare",
                      return_value=({}, None, [])), \
         patch.object(mod, "_fetch_top10_holders",
                      side_effect=RuntimeError("top10 boom")), \
         patch.object(mod, "_fetch_holder_changes",
                      return_value=([{"holder": "y"}], "changes", [])), \
         patch.object(mod, "_fetch_actual_controller",
                      return_value=("某国资委", "control", [])):
        # top10 raises inside its thread — future.result() re-raises in main thread.
        # The structure function must still return (other fields populated).
        data = mod._fetch_shareholder_structure("600519")

    # holder_count / changes / controller still populated despite top10 failure.
    assert data["holder_count"] == 100
    assert data["major_holder_changes"] == [{"holder": "y"}]
    assert data["actual_controller"] == "某国资委"
    # top10 fell back to its initialized empty default.
    assert data["top10_holders"] == []
