# -*- coding: utf-8 -*-
"""Market status endpoint. All data from fast sources, < 2s total.

Data sources:
  - 涨跌家数: stock_board_industry_name_em (东方财富行业板块)
  - 总成交额: stock_sector_spot(indicator='行业') (新浪行业板块)
  - 上证指数: stock_zh_index_daily(sh000001) (0.6s)
  - 涨停/跌停: stock_zt_pool_em + stock_zt_pool_dtgc_em (0.1s each)
  - 北向资金: stock_hsgt_fund_flow_summary_em (0.2s)

All sources are independent of East Money bulk API (which is blocked).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, date, time, timedelta

from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY = "market_status:v10"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_trading_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def _latest_trade_day() -> str:
    d = date.today()
    if d.weekday() < 5 and datetime.now().time() >= time(15, 0):
        return d.strftime("%Y%m%d")
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _market_status_data_time(result: dict) -> str | None:
    sh_index = result.get("sh_index") or {}
    return sh_index.get("date") or _latest_trade_day()


def _market_status_is_stale(result: dict) -> bool:
    data_time = _market_status_data_time(result)
    try:
        latest_date = datetime.strptime(str(data_time), "%Y%m%d").date()
    except ValueError:
        try:
            latest_date = datetime.strptime(str(data_time)[:10], "%Y-%m-%d").date()
        except ValueError:
            return False
    return latest_date < (date.today() - timedelta(days=7))


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key() -> str:
    return f"{CACHE_KEY}:{datetime.now().strftime('%Y%m%d')}"


def _cache_get() -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key())
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.warning("读取市场状态缓存失败", exc_info=True)
    return None


def _cache_put(data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(), json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.warning("写入市场状态缓存失败", exc_info=True)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_all() -> dict:
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result = {
        'is_trading_time': _is_trading_hours(),
        'up_count': 0, 'down_count': 0, 'flat_count': 0,
        'limit_up_count': 0, 'limit_down_count': 0,
        'total_amount': 0.0, 'north_flow': 0.0,
        '_fetched_at': datetime.now().isoformat(), '_cached': False,
    }

    # 1. 涨跌家数 (东方财富行业板块)
    try:
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            result['up_count'] = int(df['上涨家数'].sum())
            result['down_count'] = int(df['下跌家数'].sum())
            logger.info(f"[Market] 行业汇总: up={result['up_count']} down={result['down_count']} "
                        "source=Eastmoney")
    except Exception as e:
        logger.warning(f"[Market] 行业汇总失败: {e}")

    # 1b. 总成交额 (新浪行业板块)
    try:
        df = ak.stock_sector_spot(indicator="行业")
        if df is not None and not df.empty and "总成交额" in df.columns:
            result['total_amount'] = round(float(df['总成交额'].sum()), 2)
    except Exception as e:
        logger.warning(f"[Market] 行业成交额失败: {e}")

    # 2. 上证指数 (0.6s)
    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if df is not None and not df.empty:
            r = df.iloc[-1]
            result['sh_index'] = {
                'date': str(r.get('date', '')),
                'close': float(r['close']),
                'open': float(r['open']),
                'high': float(r['high']),
                'low': float(r['low']),
            }
    except Exception as e:
        logger.warning(f"[Market] 上证指数: {e}")

    # 3. 涨停/跌停 (0.2s)
    td = _latest_trade_day()
    try:
        df = ak.stock_zt_pool_em(date=td)
        if df is not None and not df.empty:
            result['limit_up_count'] = len(df)
    except Exception as e:
        logger.warning(f"[Market] 涨停池: {e}")
    try:
        df = ak.stock_zt_pool_dtgc_em(date=td)
        if df is not None and not df.empty:
            result['limit_down_count'] = len(df)
    except Exception as e:
        logger.warning(f"[Market] 跌停池: {e}")

    # 4. 北向资金 + HSGT涨跌(用于平盘估算) (0.2s)
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and not df.empty:
            north = df[(df['板块'].isin(['沪股通', '深股通'])) & (df['资金方向'] == '北向')]
            total_flow = 0.0
            hsgt_up = hsgt_down = hsgt_flat = 0
            for _, row in north.iterrows():
                hsgt_up += int(row.get('上涨数', 0) or 0)
                hsgt_down += int(row.get('下跌数', 0) or 0)
                hsgt_flat += int(row.get('持平数', 0) or 0)
                for col in ('成交净买额', '资金净流入'):
                    v = row.get(col)
                    if v is not None:
                        try:
                            fv = float(v)
                            if fv != 0:
                                total_flow += fv / 1e8 if abs(fv) > 1e10 else fv
                                break
                        except (ValueError, TypeError):
                            pass
            result['north_flow'] = round(total_flow, 2)
            result['_hsgt_up'] = hsgt_up
            result['_hsgt_down'] = hsgt_down
            result['_hsgt_flat'] = hsgt_flat
    except Exception as e:
        logger.warning(f"[Market] 北向资金: {e}")

    # 5. 平盘家数: 放在最后，依赖前面的 HSGT 持平数据
    #    行业汇总 up+down 可能略大于全A总数(B股/ETF差异)
    try:
        sse = ak.stock_sse_summary()
        szse = ak.stock_szse_summary()
        sse_stocks = int(float(sse[sse['项目'] == '上市股票']['股票'].iloc[0]))
        szse_stocks = int(szse[szse['证券类别'] == '股票']['数量'].iloc[0])
        total = sse_stocks + szse_stocks
        up = result['up_count']
        down = result['down_count']
        flat = total - up - down
        if flat < 0:
            # 行业汇总覆盖范围略大于纯A股，用北向数据持平比例估算
            hsgt_total = result.get('_hsgt_up', 0) + result.get('_hsgt_down', 0) + result.get('_hsgt_flat', 0)
            if hsgt_total > 0:
                flat = round(total * result.get('_hsgt_flat', 0) / hsgt_total)
        result['flat_count'] = max(0, flat)
        # 清理内部字段
        result.pop('_hsgt_up', None)
        result.pop('_hsgt_down', None)
        result.pop('_hsgt_flat', None)
    except Exception as e:
        logger.warning(f"[Market] 平盘计算失败: {e}")

    result['_fetched_at'] = datetime.now().isoformat()
    result['data_time'] = _market_status_data_time(result)
    result['is_stale'] = _market_status_is_stale(result)
    result['fallback_used'] = False
    logger.info(f"[Market] 完成 {_time.time()-t0:.1f}s")
    return result


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_lock = threading.Lock()


@router.get("/status", summary="获取市场整体状态")
def get_market_status(
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取市场整体状态（< 2s）。

    数据源:
    - 涨跌家数: stock_board_industry_name_em (东方财富行业板块)
    - 总成交额: stock_sector_spot(indicator='行业') (新浪行业板块)
    - 平盘家数: SSE+SZSE总数 - 上涨 - 下跌
    - 上证指数: stock_zh_index_daily
    - 涨停/跌停: stock_zt_pool_em + stock_zt_pool_dtgc_em
    - 北向资金: stock_hsgt_fund_flow_summary_em

    按天缓存。
    """
    if not force:
        cached = _cache_get()
        if cached:
            for k, v in [('up_count', 0), ('down_count', 0), ('flat_count', 0),
                          ('limit_up_count', 0), ('limit_down_count', 0),
                          ('total_amount', 0.0), ('north_flow', 0.0)]:
                cached.setdefault(k, v)
            cached['_cached'] = True
            cached.setdefault('data_time', _market_status_data_time(cached))
            cached.setdefault('is_stale', _market_status_is_stale(cached))
            cached.setdefault('fallback_used', True)

            if _lock.acquire(blocking=False):
                def _bg():
                    try:
                        _cache_put(_fetch_all())
                    finally:
                        _lock.release()
                threading.Thread(target=_bg, daemon=True).start()
            return cached

    data = _fetch_all()
    _cache_put(data)
    return data
