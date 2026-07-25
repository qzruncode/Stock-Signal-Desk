# -*- coding: utf-8 -*-
"""Sector/Board list endpoint.

Data sources:
  - 行业板块: ak.stock_board_industry_name_em() — 东方财富行业板块
  - 概念板块: ak.stock_board_concept_name_em() — 东方财富概念目录，板块异动仅按精确名称补行情
  - 降级源: ak.stock_sector_spot() — 新浪行业/概念板块
  - 地区板块: 暂不支持
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY = "sectors:v4"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache_key(sector_type: str) -> str:
    return f"{CACHE_KEY}:{sector_type}:{datetime.now().strftime('%Y%m%d')}"


def _cache_get(sector_type: str) -> tuple[list[dict], str, bool] | tuple[None, None, bool]:
    """Return (items, fetched_at, is_fallback) from cache, or (None, None, False) on miss.

    Falls back to the most recent non-empty cache entry when today's cache
    is empty or missing. This handles the case where the EM API is temporarily
    down and we've cached empty data.
    """
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        today_key = _cache_key(sector_type)

        # Try today's cache first
        raw = db.get_kline_snapshot(today_key)
        if raw and isinstance(raw, dict) and 'items' in raw and raw.get('items'):
            ts = raw.get('ts', '')
            logger.info(f"[Sectors] cache HIT {today_key}: {len(raw['items'])} items")
            return raw['items'], ts, False

        # Today's cache miss or empty — find most recent non-empty entry
        try:
            from sqlalchemy import select, desc
            from src.storage import KlineSnapshot
            prefix = f"{CACHE_KEY}:{sector_type}:"
            with db.get_session() as session:
                rows = session.execute(
                    select(KlineSnapshot)
                    .where(KlineSnapshot.code.like(prefix + "%"))
                    .order_by(desc(KlineSnapshot.code))
                ).scalars().all()
                for row in rows:
                    try:
                        data = json.loads(row.data or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    items = data.get("items") if isinstance(data, dict) else None
                    if items and isinstance(items, list) and len(items) > 0:
                        ts = data.get("ts", "")
                        logger.info(
                            f"[Sectors] cache FALLBACK {row.code}: {len(items)} items (today key={today_key})")
                        return items, ts, True
        except Exception as e:
            logger.debug(f"[Sectors] fallback query failed: {e}")

        logger.info(f"[Sectors] cache MISS {today_key} (no valid fallback)")
    except Exception as e:
        logger.warning(f"[Sectors] cache read error: {e}")
    return None, None, False


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


def _sector_data_time() -> str:
    return datetime.now().date().isoformat()


def _normalize_name(name: str) -> str:
    """Normalize provider formatting for exact identifier joins."""
    return "".join(str(name).split()).casefold()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_sector_sina(indicator: str) -> list[dict]:
    """Fetch industry/concept boards from Sina as an independent fallback."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []
    try:
        df = ak.stock_sector_spot(indicator=indicator)
        if df is None or df.empty:
            return result
        for _, row in df.iterrows():
            name = str(row.get("板块", "")).strip()
            if not name:
                continue
            result.append({
                "name": name,
                "code": str(row.get("label", "")).strip(),
                "change_pct": _safe_float(row.get("涨跌幅")),
                "lead_stock": str(row.get("股票名称", "")).strip(),
                "lead_stock_price": _safe_float(row.get("个股-当前价")),
                "lead_stock_change_pct": _safe_float(row.get("个股-涨跌幅")),
                "up_count": None,
                "down_count": None,
                "company_count": _safe_int(row.get("公司家数")),
                "total_volume": _safe_float(row.get("总成交量")),
                "total_amount": _safe_float(row.get("总成交额")),
                "net_flow": None,
                "data_source": "新浪",
            })
        result.sort(
            key=lambda item: (
                item.get("change_pct") is not None,
                item.get("change_pct") or 0,
            ),
            reverse=True,
        )
        logger.info("[Sectors] sina %s: %s 条, %.1fs", indicator, len(result), _time.time() - t0)
    except Exception as exc:
        logger.warning("[Sectors] sina %s 获取失败: %s", indicator, exc)
    return result

def _fetch_industry() -> list[dict]:
    """Fetch industry boards from Eastmoney, then Sina when EM is unavailable."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []

    try:
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                item = {
                    'name': str(row.get('板块名称', '')),
                    'code': str(row.get('板块代码', '')),
                    'change_pct': _safe_float(row.get('涨跌幅')),
                    'lead_stock': str(row.get('领涨股票', '')),
                    'lead_stock_price': None,
                    'lead_stock_change_pct': _safe_float(row.get('领涨股票-涨跌幅')),
                    'up_count': _safe_int(row.get('上涨家数')),
                    'down_count': _safe_int(row.get('下跌家数')),
                    'total_amount': None,
                    'net_flow': None,
                    'data_source': '东方财富',
                }
                result.append(item)
        logger.info(f"[Sectors] industry: {len(result)} 条, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.error(f"[Sectors] industry 获取失败: {e}")

    return result or _fetch_sector_sina("行业")


def _fetch_concept() -> list[dict]:
    """Fetch the provider's authoritative concept catalog and exact-join quotes."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: list[dict] = []

    try:
        names_df = ak.stock_board_concept_name_em()
        if names_df is None or names_df.empty:
            logger.warning("[Sectors] concept catalog returned empty")
            return _fetch_sector_sina("概念")

        change_by_name: dict[str, dict] = {}
        try:
            change_df = ak.stock_board_change_em()
            if change_df is not None and not change_df.empty:
                for _, change_row in change_df.iterrows():
                    change_name = str(
                        change_row.get("板块名称") or ""
                    ).strip()
                    if change_name:
                        change_by_name[_normalize_name(change_name)] = (
                            change_row.to_dict()
                        )
        except Exception as exc:
            logger.warning(
                "[Sectors] concept quote enrichment failed: %s",
                exc,
            )

        for _, catalog_row in names_df.iterrows():
            name = str(catalog_row.get("板块名称") or "").strip()
            code = str(catalog_row.get("板块代码") or "").strip()
            if not name:
                continue
            quote_row = change_by_name.get(_normalize_name(name), {})
            change_pct = quote_row.get(
                "涨跌幅",
                catalog_row.get("涨跌幅"),
            )
            if change_pct == '-' or change_pct is None:
                change_pct = None
            else:
                change_pct = _safe_float(change_pct)
            result.append({
                'name': name,
                'code': code,
                'change_pct': change_pct,
                'net_flow': _safe_float(
                    quote_row.get(
                        "主力净流入",
                        catalog_row.get("主力净流入"),
                    )
                ),
                'lead_stock': '',
                'up_count': None,
                'down_count': None,
                'data_source': '东方财富',
            })

        result.sort(key=lambda x: (x['change_pct'] is not None, x['change_pct'] or 0), reverse=True)

        logger.info(f"[Sectors] concept: {len(result)} 条, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.error(f"[Sectors] concept 获取失败: {e}")

    return result or _fetch_sector_sina("概念")


def _sector_source(items: list[dict]) -> str:
    sources = {
        str(item.get("data_source") or "").strip()
        for item in items
        if isinstance(item, dict) and item.get("data_source")
    }
    return "+".join(sorted(sources)) if sources else "none"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_lock = threading.Lock()


@router.get("/sectors", summary="获取行业/概念板块列表")
def get_sector_list(
    type: str = Query("industry", description="板块类型: industry(行业) | concept(概念) | region(地区)"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取行业/概念板块列表及各板块涨跌情况。

    数据源:
    - 行业板块: stock_board_industry_name_em (东方财富, 含涨跌幅/领涨股/上涨下跌家数)
    - 概念板块: stock_board_change_em (东方财富, ~200+ 条, 全量含涨跌幅)
    - 地区板块: 暂不支持

    按天缓存，首次请求拉取后缓存到数据库，后续请求直接返回缓存。
    """
    sector_type = type.strip().lower()
    if sector_type not in ("industry", "concept", "region"):
        sector_type = "industry"

    fetched_at = datetime.now().isoformat()

    if not force:
        cached_items, cached_ts, is_fallback = _cache_get(sector_type)
        if cached_items is not None:
            for item in cached_items:
                item['_cached'] = True

            # Background refresh (same pattern as market_status)
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
                    "_fetched_at": cached_ts or fetched_at, "_cached": True,
                    "data_time": (cached_ts or fetched_at)[:10], "is_stale": is_fallback,
                    "fallback_used": is_fallback, "source": _sector_source(cached_items), "errors": []}

    if sector_type == "industry":
        items = _fetch_industry()
    elif sector_type == "concept":
        items = _fetch_concept()
    else:
        items = []

    # Don't cache empty results — would poison the cache during API outages
    if items:
        _cache_put(sector_type, items, fetched_at)

    return {"type": sector_type, "items": items,
            "_fetched_at": fetched_at, "_cached": False,
            "data_time": _sector_data_time(), "is_stale": False,
            "fallback_used": _sector_source(items) == "新浪",
            "source": _sector_source(items),
            "errors": [] if items else [f"未获取到 {sector_type} 板块数据"]}
