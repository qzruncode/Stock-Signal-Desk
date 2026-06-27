# -*- coding: utf-8 -*-
"""Market-breadth (/market-breadth) endpoint — market participation data.

Aggregates up/down counts, limit-up/down counts, 60-day highs, consecutive
up/down days for the Shanghai index, and derived advance-decline ratio.

按天缓存。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Query

from ._helpers import safe_float, latest_series_date
from ._cache import _macro_cache_get, _macro_cache_put

logger = logging.getLogger(__name__)

router = APIRouter()


def _fetch_market_breadth_data() -> dict:
    import akshare as ak

    errors: list[str] = []
    today_str = datetime.now().strftime("%Y%m%d")
    source_parts: list[str] = []

    up_count = None
    down_count = None
    flat_count = None
    volume = None

    try:
        industry_df = ak.stock_board_industry_name_em()
        if industry_df is not None and not industry_df.empty:
            if "上涨家数" in industry_df.columns:
                up_count = int(industry_df["上涨家数"].sum())
            if "下跌家数" in industry_df.columns:
                down_count = int(industry_df["下跌家数"].sum())
            source_parts.append("东方财富")
    except Exception as e:
        errors.append(f"东方财富行业汇总: {e}")
        logger.warning(f"[Macro-市场宽度-EM] 降级: {e}")

    if up_count is None and down_count is None:
        try:
            sina_df = ak.stock_sector_spot(indicator="行业")
            if sina_df is not None and not sina_df.empty:
                if "总成交额" in sina_df.columns:
                    volume = float(sina_df["总成交额"].sum())
                if "涨跌幅" in sina_df.columns:
                    up_count = int((sina_df["涨跌幅"] > 0).sum())
                    down_count = int((sina_df["涨跌幅"] < 0).sum())
                source_parts.append("新浪")
        except Exception as e:
            errors.append(f"新浪行业板块: {e}")
            logger.warning(f"[Macro-市场宽度-新浪] 失败: {e}")
    elif volume is None:
        try:
            sina_df = ak.stock_sector_spot(indicator="行业")
            if sina_df is not None and not sina_df.empty and "总成交额" in sina_df.columns:
                volume = float(sina_df["总成交额"].sum())
                source_parts.append("新浪成交额")
        except Exception as e:
            errors.append(f"新浪行业成交额: {e}")
            logger.warning(f"[Macro-市场宽度-新浪成交额] 失败: {e}")

    if up_count is not None and down_count is not None:
        try:
            sse = ak.stock_sse_summary()
            szse = ak.stock_szse_summary()
            sse_stocks = int(float(sse[sse['项目'] == '上市股票']['股票'].iloc[0]))
            szse_stocks = int(szse[szse['证券类别'] == '股票']['数量'].iloc[0])
            total = sse_stocks + szse_stocks
            flat = total - up_count - down_count
            if flat < 0:
                try:
                    hsgt_df = ak.stock_hsgt_fund_flow_summary_em()
                    if hsgt_df is not None and not hsgt_df.empty:
                        north = hsgt_df[(hsgt_df['板块'].isin(['沪股通', '深股通'])) & (hsgt_df['资金方向'] == '北向')]
                        hsgt_up = hsgt_down = hsgt_flat = 0
                        for _, row in north.iterrows():
                            hsgt_up += int(row.get('上涨数', 0) or 0)
                            hsgt_down += int(row.get('下跌数', 0) or 0)
                            hsgt_flat += int(row.get('持平数', 0) or 0)
                        hsgt_total = hsgt_up + hsgt_down + hsgt_flat
                        if hsgt_total > 0:
                            flat = round(total * hsgt_flat / hsgt_total)
                except Exception:
                    logger.warning("[Macro-市场宽度-北向资金平盘] 计算异常", exc_info=True)
                    pass
            flat_count = max(0, flat)
        except Exception as e:
            errors.append(f"平盘计算: {e}")
            logger.warning(f"[Macro-市场宽度-平盘] 失败: {e}")

    limit_up_count = None
    broken_board_rate = None

    try:
        zt_df = ak.stock_zt_pool_em(date=today_str)
        if zt_df is not None and not zt_df.empty:
            limit_up_count = len(zt_df)
            if "炸板次数" in zt_df.columns:
                broken_count = int((zt_df["炸板次数"] > 0).sum())
            else:
                broken_count = 0
            broken_board_rate = round(broken_count / limit_up_count * 100, 2) if limit_up_count > 0 else 0.0
            source_parts.append("东方财富")
    except Exception as e:
        errors.append(f"涨停池: {e}")
        logger.warning(f"[Macro-市场宽度-涨停池] 失败: {e}")

    limit_down_count = None

    try:
        dt_df = ak.stock_zt_pool_dtgc_em(date=today_str)
        if dt_df is not None and not dt_df.empty:
            limit_down_count = len(dt_df)
    except Exception as e:
        errors.append(f"跌停池: {e}")
        logger.warning(f"[Macro-市场宽度-跌停池] 失败: {e}")

    new_high_60d = None
    try:
        strong_df = ak.stock_zt_pool_strong_em(date=today_str)
        if strong_df is not None and not strong_df.empty:
            if "是否新高" in strong_df.columns:
                new_high_60d = int((strong_df["是否新高"] == "是").sum())
            else:
                new_high_60d = len(strong_df)
    except Exception as e:
        errors.append(f"强势涨停池: {e}")
        logger.warning(f"[Macro-市场宽度-强势池] 失败: {e}")

    consecutive_up_days = None
    consecutive_down_days = None

    try:
        idx_df = ak.stock_zh_index_daily(symbol="sh000001")
        if idx_df is not None and not idx_df.empty:
            recent = idx_df.tail(20).reset_index(drop=True)
            if "close" in recent.columns and len(recent) >= 2:
                last_close = safe_float(recent.iloc[-1]["close"])
                prev_close = safe_float(recent.iloc[-2]["close"])
                if last_close is not None and prev_close is not None:
                    is_last_up = last_close > prev_close
                    count = 1
                    for i in range(len(recent) - 2, 0, -1):
                        c = safe_float(recent.iloc[i]["close"])
                        p = safe_float(recent.iloc[i - 1]["close"])
                        if c is None or p is None:
                            break
                        if is_last_up and c > p:
                            count += 1
                        elif not is_last_up and c < p:
                            count += 1
                        else:
                            break
                    consecutive_up_days = count if is_last_up else 0
                    consecutive_down_days = count if not is_last_up else 0
    except Exception as e:
        errors.append(f"连涨连跌: {e}")
        logger.warning(f"[Macro-市场宽度-连涨连跌] 失败: {e}")

    advance_decline_ratio = None
    if up_count is not None and down_count is not None and down_count > 0:
        advance_decline_ratio = round(up_count / down_count, 2)

    source_label = " + ".join(source_parts) if source_parts else "未知"

    return {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "advance_decline_ratio": advance_decline_ratio,
        "new_high_60d": new_high_60d,
        "new_low_60d": limit_down_count,
        "consecutive_up_days": consecutive_up_days,
        "consecutive_down_days": consecutive_down_days,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "broken_board_rate": broken_board_rate,
        "volume": volume,
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "source": source_label,
        "errors": errors,
        "data_time": datetime.now().date().isoformat(),
        "is_stale": False,
        "fallback_used": "新浪" in source_label,
    }


@router.get("/market-breadth", summary="获取市场宽度")
def get_market_breadth():
    """获取市场整体参与度（赚钱效应），辅助判断市场情绪。

    按天缓存。数据源同 _fetch_market_breadth_data。
    """
    cached = _macro_cache_get("market-breadth")
    if cached:
        cached["_cached"] = True
        cached.setdefault("data_time", datetime.now().date().isoformat())
        cached.setdefault("is_stale", False)
        cached.setdefault("fallback_used", True)
        return cached

    data = _fetch_market_breadth_data()
    _macro_cache_put("market-breadth", data)
    return data