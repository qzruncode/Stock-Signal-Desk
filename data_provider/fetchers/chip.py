# -*- coding: utf-8 -*-
"""Chip distribution (筹码分布) fetcher."""

from __future__ import annotations

import logging
from typing import Optional

from ..realtime_types import ChipDistribution, safe_float

logger = logging.getLogger(__name__)


def is_us_code(stock_code: str) -> bool:
    from ..us_index_mapping import is_us_stock_code

    return is_us_stock_code(stock_code)


def is_hk_code(stock_code: str) -> bool:
    code = stock_code.strip().lower()
    if code.endswith(".hk"):
        return code[:-3].isdigit() and 1 <= len(code[:-3]) <= 5
    if code.startswith("hk"):
        return code[2:].isdigit() and 1 <= len(code[2:]) <= 5
    return code.isdigit() and len(code) == 5


def is_etf_code(stock_code: str) -> bool:
    etf_prefixes = ("51", "52", "56", "58", "15", "16", "18")
    code = stock_code.strip().split(".")[0]
    return code.startswith(etf_prefixes) and len(code) == 6


def get_chip_distribution(
    stock_code: str,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> Optional[ChipDistribution]:
    """获取筹码分布数据（A 股专属）。"""
    import akshare as ak

    if is_us_code(stock_code):
        logger.debug("[API跳过] %s 是美股，无筹码分布数据", stock_code)
        return None
    if is_hk_code(stock_code):
        logger.debug("[API跳过] %s 是港股，无筹码分布数据", stock_code)
        return None
    if is_etf_code(stock_code):
        logger.debug("[API跳过] %s 是 ETF/指数，无筹码分布数据", stock_code)
        return None

    try:
        if set_user_agent:
            set_user_agent()
        if enforce_rate_limit:
            enforce_rate_limit()

        logger.info("[API调用] ak.stock_cyq_em(symbol=%s) 获取筹码分布...", stock_code)
        import time as _time

        api_start = _time.time()
        df = ak.stock_cyq_em(symbol=stock_code)
        api_elapsed = _time.time() - api_start

        if df.empty:
            logger.warning("[API返回] ak.stock_cyq_em 返回空数据, 耗时 %.2fs", api_elapsed)
            return None

        logger.info("[API返回] ak.stock_cyq_em 成功: %d 天数据, 耗时 %.2fs", len(df), api_elapsed)
        latest = df.iloc[-1]
        chip = ChipDistribution(
            code=stock_code,
            date=str(latest.get("日期", "")),
            profit_ratio=safe_float(latest.get("获利比例")),
            avg_cost=safe_float(latest.get("平均成本")),
            cost_90_low=safe_float(latest.get("90成本-低")),
            cost_90_high=safe_float(latest.get("90成本-高")),
            concentration_90=safe_float(latest.get("90集中度")),
            cost_70_low=safe_float(latest.get("70成本-低")),
            cost_70_high=safe_float(latest.get("70成本-高")),
            concentration_70=safe_float(latest.get("70集中度")),
        )
        logger.info(
            "[筹码分布] %s 日期=%s: 获利比例=%.1f%%",
            stock_code,
            chip.date,
            chip.profit_ratio * 100 if chip.profit_ratio else 0,
        )
        return chip
    except Exception as e:
        logger.error("[API错误] 获取 %s 筹码分布失败: %s", stock_code, e)
        return None
