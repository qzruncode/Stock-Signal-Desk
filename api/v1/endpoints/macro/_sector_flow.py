# -*- coding: utf-8 -*-
"""Sector-flow (/sector-flow) endpoint — sector capital flow data.

Data sources:
  Industry sectors:
    1. 东方财富 stock_sector_fund_flow_rank(indicator='今日', sector_type='行业资金流')
    2. 新浪 stock_sector_spot(indicator='行业')
  Concept sectors:
    1. 东方财富 stock_sector_fund_flow_rank(indicator='今日', sector_type='概念资金流')
    2. 新浪 stock_sector_spot(indicator='概念')

按天缓存。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from ._helpers import safe_float
from ._cache import _macro_cache_get, _macro_cache_put

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def _fetch_sector_flow_industry() -> list[dict]:
    import akshare as ak

    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        if df is not None and not df.empty:
            sort_col = next(
                (c for c in df.columns if "主力净流入" in c and "净额" in c),
                next((c for c in df.columns if "主力净流入" in c), None),
            )
            if sort_col:
                df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
            main_col = next((c for c in df.columns if "主力净流入" in c and "净额" in c), None)
            super_col = next((c for c in df.columns if "超大单净流入" in c and "净额" in c), None)
            large_col = next((c for c in df.columns if "大单净流入" in c and "净额" in c and "超大" not in c), None)
            pct_col = next((c for c in df.columns if "涨跌幅" in c), "涨跌幅")
            leader_col = next((c for c in df.columns if "最大股" in c), None)
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("名称", "")).strip(),
                    "pct_chg": safe_float(row.get(pct_col)),
                    "main_net_inflow": safe_float(row.get(main_col) if main_col else None),
                    "super_large_net_inflow": safe_float(row.get(super_col) if super_col else None),
                    "large_net_inflow": safe_float(row.get(large_col) if large_col else None),
                    "total_amount": None,
                    "up_count": None,
                    "down_count": None,
                    "leading_stock": str(row.get(leader_col, "")).strip() if leader_col else None,
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-行业EM] 降级: {e}")

    try:
        df = ak.stock_sector_spot(indicator="行业")
        if df is not None and not df.empty:
            df = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("板块", "")).strip(),
                    "pct_chg": safe_float(row.get("涨跌幅")),
                    "main_net_inflow": None,
                    "super_large_net_inflow": None,
                    "large_net_inflow": None,
                    "total_amount": safe_float(row.get("总成交额")),
                    "up_count": None,
                    "down_count": None,
                    "leading_stock": str(row.get("股票名称", "")).strip(),
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-行业新浪] 失败: {e}")

    return []


def _fetch_sector_flow_concept() -> list[dict]:
    import akshare as ak

    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="概念资金流")
        if df is not None and not df.empty:
            sort_col = next(
                (c for c in df.columns if "主力净流入" in c and "净额" in c),
                next((c for c in df.columns if "主力净流入" in c), None),
            )
            if sort_col:
                df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
            main_col = next((c for c in df.columns if "主力净流入" in c and "净额" in c), None)
            super_col = next((c for c in df.columns if "超大单净流入" in c and "净额" in c), None)
            large_col = next((c for c in df.columns if "大单净流入" in c and "净额" in c and "超大" not in c), None)
            pct_col = next((c for c in df.columns if "涨跌幅" in c), "涨跌幅")
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("名称", "")).strip(),
                    "pct_chg": safe_float(row.get(pct_col)),
                    "main_net_inflow": safe_float(row.get(main_col) if main_col else None),
                    "super_large_net_inflow": safe_float(row.get(super_col) if super_col else None),
                    "large_net_inflow": safe_float(row.get(large_col) if large_col else None),
                    "total_amount": None,
                    "up_count": None,
                    "down_count": None,
                    "leading_stock": None,
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-概念EM] 降级: {e}")

    try:
        df = ak.stock_sector_spot(indicator="概念")
        if df is not None and not df.empty:
            df = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
            records = []
            for _, row in df.iterrows():
                rec = {
                    "name": str(row.get("板块", "")).strip(),
                    "pct_chg": safe_float(row.get("涨跌幅")),
                    "main_net_inflow": None,
                    "super_large_net_inflow": None,
                    "large_net_inflow": None,
                    "total_amount": safe_float(row.get("总成交额")),
                    "up_count": None,
                    "down_count": None,
                    "leading_stock": str(row.get("股票名称", "")).strip(),
                }
                records.append(rec)
            return records
    except Exception as e:
        logger.warning(f"[Macro-板块资金-概念新浪] 失败: {e}")

    return []


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/sector-flow", summary="获取板块资金流向")
def get_sector_flow(
    type: str = Query("industry", description="板块类型: industry(行业) | concept(概念)"),
    top_n: int = Query(10, ge=1, le=50, description="返回前N个板块"),
):
    """获取行业/概念板块的主力资金净流入/流出情况。

    行业板块数据源: 东方财富 (stock_sector_fund_flow_rank)
    概念板块数据源: 东方财富 (stock_sector_fund_flow_rank)，降级到新浪
    按天缓存。
    """
    type = type.strip().lower()
    if type not in ("industry", "concept"):
        raise HTTPException(status_code=400, detail={
            "error": "invalid_type",
            "message": f"不支持的板块类型: {type}，支持: industry, concept",
        })

    cache_prefix = f"sector-flow-{type}"
    cached = _macro_cache_get(cache_prefix)
    if cached:
        inflow = (cached.get("inflow_top") or [])[:top_n]
        outflow = (cached.get("outflow_top") or [])[:top_n]
        records = (cached.get("records") or [])[:top_n]
        return {
            **cached,
            "inflow_top": inflow,
            "outflow_top": outflow,
            "records": records,
            "top_n": top_n,
            "_cached": True,
            "fallback_used": True,
        }

    errors: list[str] = []

    try:
        if type == "industry":
            all_records = _fetch_sector_flow_industry()
            if all_records and all_records[0].get("main_net_inflow") is not None:
                source = "东方财富"
            else:
                source = "新浪"
        else:
            all_records = _fetch_sector_flow_concept()
            if all_records and all_records[0].get("main_net_inflow") is not None:
                source = "东方财富"
            else:
                source = "新浪"
    except Exception as e:
        logger.warning(f"[Macro-板块资金] 失败: {e}")
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": f"无法获取板块资金流向数据: {e}",
        })

    if not all_records:
        raise HTTPException(status_code=502, detail={
            "error": "no_data",
            "message": "无法获取板块资金流向数据",
        })

    inflow_records = sorted(
        [r for r in all_records if r.get("main_net_inflow") is not None and r["main_net_inflow"] > 0],
        key=lambda r: r["main_net_inflow"], reverse=True,
    )
    outflow_records = sorted(
        [r for r in all_records if r.get("main_net_inflow") is not None and r["main_net_inflow"] < 0],
        key=lambda r: r["main_net_inflow"],
    )

    if not inflow_records and not outflow_records:
        inflow_records = [r for r in all_records if r.get("pct_chg") is not None and r["pct_chg"] > 0]
        outflow_records = [r for r in all_records if r.get("pct_chg") is not None and r["pct_chg"] < 0]

    result = {
        "type": type,
        "top_n": top_n,
        "inflow_top": inflow_records,
        "outflow_top": outflow_records,
        "records": all_records[:top_n],
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        "source": source,
        "errors": errors,
        "data_time": datetime.now().date().isoformat(),
        "is_stale": False,
        "fallback_used": source == "新浪",
    }

    _macro_cache_put(cache_prefix, result)

    result["inflow_top"] = inflow_records[:top_n]
    result["outflow_top"] = outflow_records[:top_n]
    return result