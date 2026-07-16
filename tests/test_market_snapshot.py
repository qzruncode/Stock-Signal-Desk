from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.tools._market_snapshot import (
    _parse_legu_activity,
    expected_trade_day,
    fetch_market_snapshot,
    market_breadth_view,
    market_status_view,
)


TZ = ZoneInfo("Asia/Shanghai")


def test_parse_legu_activity_keeps_stock_count_semantics() -> None:
    html = """
    <html><head><meta name="description" content="2026-07-16，A股赚钱效应"></head>
    <body><div class="market-activity">活跃度 57.45% 2026-07-16 13:15:00</div>
    <table>
      <tr><td>上涨</td><td>2988</td><td>下跌</td><td>2103</td><td>平盘</td><td>106</td></tr>
      <tr><td>涨停</td><td>52</td><td>跌停</td><td>10</td><td>停牌</td><td>4</td></tr>
      <tr><td>真实涨停</td><td>48</td><td>真实跌停</td><td>2</td></tr>
    </table></body></html>
    """

    parsed = _parse_legu_activity(html)

    assert parsed["up_count"] == 2988
    assert parsed["down_count"] == 2103
    assert parsed["flat_count"] == 106
    assert parsed["market_activity_pct"] == 57.45
    assert parsed["data_time"] == "2026-07-16T13:15:00+08:00"
    assert parsed["breadth_scope"] == "沪深A股"


def test_expected_trade_day_uses_today_from_call_auction() -> None:
    calendar = [date(2026, 7, 15), date(2026, 7, 16)]

    assert expected_trade_day(datetime(2026, 7, 16, 9, 14), calendar) == date(2026, 7, 15)
    assert expected_trade_day(datetime(2026, 7, 16, 9, 15), calendar) == date(2026, 7, 16)


def _legu_payload() -> dict:
    return {
        "up_count": 3000,
        "down_count": 2000,
        "flat_count": 100,
        "halt_count": 5,
        "legu_limit_up_count": 52,
        "legu_limit_down_count": 10,
        "real_limit_up_count": 48,
        "real_limit_down_count": 2,
        "market_activity_pct": 58.0,
        "data_time": "2026-07-16T13:30:00+08:00",
        "source": "乐咕乐股市场活跃度",
        "breadth_scope": "沪深A股",
    }


def _index_payload() -> dict:
    sh = {
        "code": "sh000001",
        "name": "上证指数",
        "price": 3900.0,
        "change_pct": -1.0,
        "amount": 700_000_000_000.0,
    }
    sz = {
        "code": "sz399001",
        "name": "深证成指",
        "price": 14000.0,
        "change_pct": -0.5,
        "amount": 800_000_000_000.0,
    }
    return {
        "indices": {"shanghai_composite": sh, "shenzhen_component": sz},
        "total_amount": 15000.0,
        "total_amount_unit": "亿元",
        "turnover_scope": "沪深市场",
        "source": "新浪实时指数行情",
    }


def test_snapshot_uses_correct_broken_board_denominator_and_no_fake_north_flow() -> None:
    pool_counts = {
        "stock_zt_pool_em": 50,
        "stock_zt_pool_dtgc_em": 7,
        "stock_zt_pool_zbgc_em": 10,
    }

    def fake_pool(name: str, trade_day: str) -> dict:
        assert trade_day == "20260716"
        return {"count": pool_counts[name], "source": name}

    with (
        patch("src.tools._market_snapshot._fetch_trade_dates", return_value=[date(2026, 7, 16)]),
        patch("src.tools._market_snapshot._fetch_legu_activity", side_effect=_legu_payload),
        patch("src.tools._market_snapshot._fetch_index_spot", side_effect=_index_payload),
        patch(
            "src.tools._market_snapshot._fetch_index_daily",
            return_value=[
                {"date": "2026-07-14", "close": 3900.0},
                {"date": "2026-07-15", "close": 3950.0},
            ],
        ),
        patch("src.tools._market_snapshot._fetch_pool", side_effect=fake_pool),
    ):
        snapshot = fetch_market_snapshot(datetime(2026, 7, 16, 13, 30, tzinfo=TZ))

    assert snapshot["limit_up_count"] == 50
    assert snapshot["broken_board_count"] == 10
    assert snapshot["broken_board_rate"] == 16.67  # 10 / (50 sealed + 10 failed)
    assert snapshot["total_amount"] == 15000.0
    assert snapshot["total_amount_unit"] == "亿元"
    assert snapshot["north_flow"] is None
    assert snapshot["north_flow_available"] is False
    assert snapshot["consecutive_down_days"] == 1
    assert snapshot["is_stale"] is False


def test_views_remove_unsupported_60_day_high_low_fields() -> None:
    snapshot = {
        **_legu_payload(),
        "total_amount": 15000.0,
        "total_amount_unit": "亿元",
        "turnover_scope": "沪深市场",
        "north_flow": None,
        "north_flow_available": False,
        "north_flow_note": "不可用",
    }

    status = market_status_view(snapshot)
    breadth = market_breadth_view(snapshot)

    assert status["north_flow"] is None
    assert status["north_flow_available"] is False
    assert "new_high_60d" not in breadth
    assert "new_low_60d" not in breadth
    assert "volume" not in breadth
    assert breadth["total_amount_unit"] == "亿元"


def test_legu_failure_uses_full_stock_breadth_not_sector_counts() -> None:
    with (
        patch("src.tools._market_snapshot._fetch_trade_dates", return_value=[date(2026, 7, 16)]),
        patch("src.tools._market_snapshot._fetch_legu_activity", side_effect=RuntimeError("down")),
        patch(
            "src.tools._market_snapshot._fetch_sina_a_breadth",
            return_value={
                "up_count": 3100,
                "down_count": 2200,
                "flat_count": 120,
                "halt_count": None,
                "data_time": "2026-07-16T13:30:00+08:00",
                "source": "新浪全A实时行情",
                "breadth_scope": "A股（含北交所）",
            },
        ),
        patch("src.tools._market_snapshot._fetch_index_spot", side_effect=_index_payload),
        patch("src.tools._market_snapshot._fetch_index_daily", return_value=[]),
        patch("src.tools._market_snapshot._fetch_pool", return_value={"count": 0}),
    ):
        snapshot = fetch_market_snapshot(datetime(2026, 7, 16, 13, 30, tzinfo=TZ))

    assert snapshot["up_count"] == 3100
    assert snapshot["down_count"] == 2200
    assert snapshot["breadth_scope"] == "A股（含北交所）"
    assert snapshot["fallback_used"] is True
    assert any("较慢的新浪全A" in item for item in snapshot["warnings"])
