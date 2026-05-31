# -*- coding: utf-8 -*-
"""Sector/Board list endpoint.

Data sources:
  - 行业板块: ak.stock_board_industry_summary_ths() — 同花顺行业板块, 90 条, 完整数据
  - 概念板块: ak.stock_board_change_em() — 东方财富板块异动 (此接口可用), 过滤后约 200+ 条, 全量含涨跌幅
  - 地区板块: 暂不支持
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY = "sectors:v3"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache_key(sector_type: str) -> str:
    return f"{CACHE_KEY}:{sector_type}:{datetime.now().strftime('%Y%m%d')}"


def _cache_get(sector_type: str) -> tuple[list[dict], str] | tuple[None, None]:
    """Return (items, fetched_at) from cache, or (None, None) on miss."""
    try:
        from src.storage import DatabaseManager
        key = _cache_key(sector_type)
        raw = DatabaseManager.get_instance().get_kline_snapshot(key)
        if raw and isinstance(raw, dict) and 'items' in raw:
            ts = raw.get('ts', '')
            logger.info(f"[Sectors] cache HIT {key}: {len(raw['items'])} items")
            return raw['items'], ts
        else:
            logger.info(f"[Sectors] cache MISS {key}")
    except Exception as e:
        logger.warning(f"[Sectors] cache read error: {e}")
    return None, None


def _cache_put(sector_type: str, data: list[dict], fetched_at: str) -> None:
    try:
        from src.storage import DatabaseManager
        key = _cache_key(sector_type)
        DatabaseManager.get_instance().save_kline_snapshot(
            key, json.dumps({"items": data, "ts": fetched_at}, ensure_ascii=False))
        logger.info(f"[Sectors] cache SAVED {key}: {len(data)} items, ts={fetched_at}")
    except Exception as e:
        logger.warning(f"[Sectors] cache write error: {e}")


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Concept filter: remove non-concept entries from EM board_change
# ---------------------------------------------------------------------------

# Entries to exclude — financial reports, market types, indices, etc.
_CONCEPT_EXCLUDE = {
    # 财报分类
    '2025三季报预增', '2025三季报预减', '2025三季报扭亏',
    '2025年报预增', '2025年报预减', '2025年报扭亏',
    '2026—季报预增', '2026—季报预减', '2026—季报扭亏',
    '预盈预增', '预亏预减',
    # 市场板块类型
    'AB股', 'B股', 'AH股', 'GDR', 'ST股',
    '融资融券', '沪股通', '深股通',
    # 指数
    'HS300_', '上证50_', '上证180_', '上证380_',
    '沪深300', '中证500', '中证100', '中证1000', '中证2000',
    '创业板综', '创业板指', '深证100R', '深证300', '深成500',
    '中小板综', '中小板指', '科创50', '科创100', '科创综指',
    '上证收益', '深证收益', '中证红利', '上证红利',
    # 机构/风格
    '机构重仓', '基金重仓', '社保重仓', '券商重仓',
    '保险重仓', '信托重仓', 'QFII重仓',
    # 昨日系列
    '昨日涨停', '昨日跌停', '昨日连板', '昨日触板',
    '昨日高振幅', '昨日大阴线', '昨日大阳线',
    '昨日涨停股', '昨日跌停股',
    # 其他非概念
    '次新股', '新股', '破净股', '低价股', '百元股',
    '中字头', '茅指数', '宁组合', 'ST概念',
    '微小盘', '小盘股', '中盘股', '大盘股', '微盘股',
    '中盘成长', '中盘价值', '小盘成长', '小盘价值',
    '大盘成长', '大盘价值',
    '历史新高', '历史新低', '连续上涨', '连续下跌',
    '转债标的', '债转股', '转债股',
    '央企改革', '国企改革', '地方国企改革',
}


def _is_likely_concept(name: str) -> bool:
    """Check if a board name from EM is likely a concept/theme board."""
    name = str(name).strip()
    if not name:
        return False
    if name in _CONCEPT_EXCLUDE:
        return False
    # Exclude names starting with year pattern like "2025..."
    if re.match(r'^\d{4}', name):
        return False
    return True


def _normalize_name(name: str) -> str:
    """Normalize board name for matching."""
    return re.sub(r'\s+', '', str(name)).lower()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_industry() -> list[dict]:
    """Fetch industry board list from THS (同花顺行业板块).

    Returns 90 industries with: name, change_pct, lead_stock, up_count, down_count, etc.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []

    try:
        df = ak.stock_board_industry_summary_ths()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                item = {
                    'name': str(row.get('板块', '')),
                    'code': str(row.get('序号', '')),
                    'change_pct': _safe_float(row.get('涨跌幅')),
                    'lead_stock': str(row.get('领涨股', '')),
                    'lead_stock_price': _safe_float(row.get('领涨股-最新价')),
                    'lead_stock_change_pct': _safe_float(row.get('领涨股-涨跌幅')),
                    'up_count': _safe_int(row.get('上涨家数')),
                    'down_count': _safe_int(row.get('下跌家数')),
                    'total_amount': _safe_float(row.get('总成交额')),
                    'net_flow': _safe_float(row.get('净流入')),
                }
                result.append(item)
        logger.info(f"[Sectors] industry: {len(result)} 条, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.error(f"[Sectors] industry 获取失败: {e}")

    return result


def _fetch_concept() -> list[dict]:
    """Fetch concept board list from EM board_change (东方财富板块异动).

    This endpoint works and returns boards with 涨跌幅. We filter out non-concept
    entries (financial reports, market types, indices) and enrich with THS concept
    codes where available.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []

    try:
        # 1. Get concept name→code mapping from THS
        code_map: dict[str, str] = {}
        try:
            names_df = ak.stock_board_concept_name_ths()
            if names_df is not None and not names_df.empty:
                for _, row in names_df.iterrows():
                    code_map[_normalize_name(str(row.get('name', '')))] = str(row.get('code', ''))
        except Exception as e:
            logger.warning(f"[Sectors] concept THS names failed: {e}")

        # 2. Get board change data from EM (this endpoint works)
        change_df = ak.stock_board_change_em()
        if change_df is None or change_df.empty:
            logger.warning("[Sectors] concept: EM board_change returned empty")
            return result

        for _, row in change_df.iterrows():
            name = str(row.get('板块名称', '')).strip()
            if not name or not _is_likely_concept(name):
                continue

            change_pct = row.get('涨跌幅')
            if change_pct == '-' or change_pct is None:
                change_pct = None
            else:
                change_pct = _safe_float(change_pct)

            # Try to find THS code via fuzzy match
            code = ''
            norm = _normalize_name(name)
            # Exact match
            if norm in code_map:
                code = code_map[norm]
            else:
                # Try removing '概念' suffix for matching
                base = norm.replace('概念', '')
                if base in code_map:
                    code = code_map[base]

            item = {
                'name': name,
                'code': code,
                'change_pct': change_pct,
                'net_flow': _safe_float(row.get('主力净流入')),
                # concept boards don't have these, kept for API compatibility
                'lead_stock': '',
                'up_count': None,
                'down_count': None,
            }
            result.append(item)

        # Sort by change_pct desc (nulls last)
        result.sort(key=lambda x: (x['change_pct'] is not None, x['change_pct'] or 0), reverse=True)

        logger.info(f"[Sectors] concept: {len(result)} 条, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.error(f"[Sectors] concept 获取失败: {e}")

    return result


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_lock = None


@router.get("/sectors", summary="获取行业/概念板块列表")
def get_sector_list(
    type: str = Query("industry", description="板块类型: industry(行业) | concept(概念) | region(地区)"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取行业/概念板块列表及各板块涨跌情况。

    数据源:
    - 行业板块: stock_board_industry_summary_ths (同花顺, 90 条, 含涨跌幅/领涨股/上涨下跌家数)
    - 概念板块: stock_board_change_em (东方财富, ~200+ 条, 全量含涨跌幅)
    - 地区板块: 暂不支持

    按天缓存，首次请求拉取后缓存到数据库，后续请求直接返回缓存。
    """
    sector_type = type.strip().lower()
    if sector_type not in ("industry", "concept", "region"):
        sector_type = "industry"

    fetched_at = datetime.now().isoformat()

    if not force:
        cached_items, cached_ts = _cache_get(sector_type)
        if cached_items is not None:
            for item in cached_items:
                item['_cached'] = True

            # Background refresh (same pattern as market_status)
            global _lock
            if _lock is None:
                _lock = threading.Lock()
            if _lock.acquire(blocking=False):
                def _bg_refresh():
                    try:
                        if sector_type == "industry":
                            fresh = _fetch_industry()
                        elif sector_type == "concept":
                            fresh = _fetch_concept()
                        else:
                            fresh = []
                        if fresh:
                            _cache_put(sector_type, fresh, datetime.now().isoformat())
                    finally:
                        _lock.release()
                threading.Thread(target=_bg_refresh, daemon=True).start()

            return {"type": sector_type, "items": cached_items,
                    "_fetched_at": cached_ts or fetched_at, "_cached": True}

    if sector_type == "industry":
        items = _fetch_industry()
    elif sector_type == "concept":
        items = _fetch_concept()
    else:
        items = []

    _cache_put(sector_type, items, fetched_at)
    return {"type": sector_type, "items": items,
            "_fetched_at": fetched_at, "_cached": False}
