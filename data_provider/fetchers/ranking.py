# -*- coding: utf-8 -*-
"""Sector and concept ranking fetchers."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def get_sector_rankings(
    n: int = 5,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> Optional[Tuple[List[Dict], List[Dict]]]:
    """获取行业板块涨跌榜，优先东财接口，失败后降级新浪。"""
    import akshare as ak

    def _get_rank_top_n(df: pd.DataFrame, change_col: str, industry_name: str, n: int):
        df[change_col] = pd.to_numeric(df[change_col], errors="coerce")
        df = df.dropna(subset=[change_col])
        top = df.nlargest(n, change_col)
        bottom = df.nsmallest(n, change_col)
        return (
            [{"name": row[industry_name], "change_pct": row[change_col]} for _, row in top.iterrows()],
            [{"name": row[industry_name], "change_pct": row[change_col]} for _, row in bottom.iterrows()],
        )

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        logger.info("[API调用] ak.stock_board_industry_name_em() 获取板块排行...")
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            return _get_rank_top_n(df, "涨跌幅", "板块名称", n)
    except Exception as e:
        logger.warning("[Akshare] 东财获取板块排行失败: %s，尝试新浪", e)

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()
    try:
        logger.info("[API调用] ak.stock_sector_spot() 获取板块排行(新浪)...")
        df = ak.stock_sector_spot(indicator="行业")
        if df is None or df.empty:
            return None
        return _get_rank_top_n(df, "涨跌幅", "板块", n)
    except Exception as e:
        logger.error("[Akshare] 新浪获取板块排行失败: %s", e)
        return None


def get_concept_rankings(
    n: int = 5,
    enforce_rate_limit=None,
    set_user_agent=None,
) -> Optional[Tuple[List[Dict], List[Dict]]]:
    """获取概念/题材涨跌榜。"""
    import akshare as ak

    if set_user_agent:
        set_user_agent()
    if enforce_rate_limit:
        enforce_rate_limit()

    try:
        logger.info("[API调用] ak.stock_board_concept_name_em() 获取概念排行...")
        df = ak.stock_board_concept_name_em()
        if df is None or df.empty:
            return None

        change_col = "涨跌幅"
        name_col = "板块名称"
        if change_col not in df.columns or name_col not in df.columns:
            return None

        df = df.copy()
        df[change_col] = pd.to_numeric(df[change_col], errors="coerce")
        df = df.dropna(subset=[change_col])
        top = df.nlargest(n, change_col)
        bottom = df.nsmallest(n, change_col)
        return (
            [{"name": str(row[name_col]), "change_pct": float(row[change_col])} for _, row in top.iterrows()],
            [{"name": str(row[name_col]), "change_pct": float(row[change_col])} for _, row in bottom.iterrows()],
        )
    except Exception as e:
        logger.warning("[Akshare] 获取概念排行失败: %s", e)
        return None
