# -*- coding: utf-8 -*-
"""Stock basic info endpoint — fundamental data complementing realtime quotes.

Data sources:
  - Primary: ak.stock_profile_cninfo() — cninfo, 公司概况 (name, industry, listing date, business, profile)
  - Supplementary: ak.stock_individual_info_em() — East Money push API (total shares, circ shares, PE/PB)
    May fail when East Money blocks the connection; endpoint returns partial data gracefully.

The data returned here is largely static (changes at most daily), so a per-day cache is used.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import threading
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_KEY = "stock_info:v2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ('nan', 'None', '--', '-'):
        return None
    # Strip unit suffixes like 亿, 万
    for suffix, multiplier in [('亿', 1e8), ('万', 1e4)]:
        if s.endswith(suffix):
            try:
                return float(s[:-len(suffix)]) * multiplier
            except (ValueError, TypeError):
                return None
    try:
        f = float(s)
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _cache_key(symbol: str) -> str:
    return f"{CACHE_KEY}:{_normalize_symbol(symbol)}:{datetime.now().strftime('%Y%m%d')}"


def _normalize_symbol(symbol: str) -> str:
    code = symbol.strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            return code[2:]
    return code


def _cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_cache_key(symbol))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and 'symbol' in data:
                logger.info(f"[StockInfo] cache HIT {_cache_key(symbol)}")
                return data
    except Exception as e:
        logger.warning(f"[StockInfo] cache read error: {e}")
    return None


def _cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _cache_key(symbol), json.dumps(data, ensure_ascii=False))
        logger.info(f"[StockInfo] cache SAVED {_cache_key(symbol)}")
    except Exception as e:
        logger.warning(f"[StockInfo] cache write error: {e}")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_from_cninfo(symbol: str) -> dict:
    """Fetch stock basic info from cninfo (巨潮资讯).

    Returns fields: name, short_name, industry, market, listing_date,
    register_capital, main_business, business_scope, profile.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {}

    try:
        df = ak.stock_profile_cninfo(symbol=symbol)
        if df is not None and not df.empty:
            row = df.iloc[0]
            result = {
                'name': str(row.get('公司名称', '') or ''),
                'short_name': str(row.get('A股简称', '') or ''),
                'industry': str(row.get('所属行业', '') or ''),
                'market': str(row.get('所属市场', '') or ''),
                'listing_date': str(row.get('上市日期', '') or ''),
                'establish_date': str(row.get('成立日期', '') or ''),
                'register_capital': _safe_float(row.get('注册资金')),
                'main_business': str(row.get('主营业务', '') or ''),
                'business_scope': str(row.get('经营范围', '') or ''),
                'profile': str(row.get('机构简介', '') or ''),
                'website': str(row.get('官方网站', '') or ''),
                '_cninfo_ok': True,
            }
            logger.info(f"[StockInfo] cninfo OK for {symbol}: {_time.time() - t0:.1f}s")
        else:
            logger.warning(f"[StockInfo] cninfo returned empty for {symbol}")
    except Exception as e:
        logger.warning(f"[StockInfo] cninfo failed for {symbol}: {e}")

    return result


def _fetch_from_em(symbol: str) -> dict:
    """Fetch supplementary data from East Money push API.

    Returns:
      - Company info (fallback for cninfo): short_name, industry, listing_date
      - Shares: total_shares, circ_shares
      - Valuation: pe_dynamic, pe_static, pb_ratio, total_mv, circ_mv

    May fail; returns empty dict on failure.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {}

    try:
        df = ak.stock_individual_info_em(symbol=symbol, timeout=10)
        if df is not None and not df.empty:
            item_col = 'item' if 'item' in df.columns else (df.columns[0] if len(df.columns) >= 1 else None)
            value_col = 'value' if 'value' in df.columns else (df.columns[1] if len(df.columns) >= 2 else None)
            if item_col is None or value_col is None:
                logger.warning(f"[StockInfo] EM unexpected columns for {symbol}: {list(df.columns)}")
                return result
            # Map item→value pairs to dict
            info_map: dict[str, str] = {}
            for _, row in df.iterrows():
                item = str(row.get(item_col, ''))
                value = str(row.get(value_col, ''))
                info_map[item] = value

            # Company info fields — serve as fallback when cninfo is unavailable
            em_short_name = str(info_map.get('股票简称', '') or '')
            em_industry = str(info_map.get('行业', '') or '')
            em_listing_date = str(info_map.get('上市时间', '') or '')

            result = {
                # Company info fallback (cninfo takes priority)
                'short_name': em_short_name if em_short_name else None,
                'industry': em_industry if em_industry else None,
                'listing_date': em_listing_date if em_listing_date else None,
                # Shares & valuation (EM is primary source)
                'total_shares': _safe_float(info_map.get('总股本')),
                'circ_shares': _safe_float(info_map.get('流通股')),
                'pe_dynamic': _safe_float(info_map.get('市盈率-动态')),
                'pe_static': _safe_float(info_map.get('市盈率-静态')),
                'pb_ratio': _safe_float(info_map.get('市净率')),
                'total_mv': _safe_float(info_map.get('总市值')),
                'circ_mv': _safe_float(info_map.get('流通市值')),
                '_em_ok': True,
            }
            logger.info(f"[StockInfo] EM OK for {symbol}: {_time.time() - t0:.1f}s")
        else:
            logger.warning(f"[StockInfo] EM returned empty for {symbol}")
    except Exception as e:
        logger.warning(f"[StockInfo] EM failed for {symbol}: {e}")

    return result


def _fetch_from_ths_business(symbol: str) -> dict:
    """Fetch business intro from THS.

    This is a strong fallback for main business / products / scope when cninfo
    is unavailable or incomplete.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {}

    try:
        df = ak.stock_zyjs_ths(symbol=_normalize_symbol(symbol))
        if df is not None and not df.empty:
            row = df.iloc[0]
            result = {
                'main_business': str(row.get('主营业务', '') or '') or None,
                'business_scope': str(row.get('经营范围', '') or '') or None,
                'product_type': str(row.get('产品类型', '') or '') or None,
                'product_name': str(row.get('产品名称', '') or '') or None,
                '_ths_business_ok': True,
            }
            logger.info(f"[StockInfo] THS business OK for {symbol}: {_time.time() - t0:.1f}s")
        else:
            logger.warning(f"[StockInfo] THS business returned empty for {symbol}")
    except Exception as e:
        logger.warning(f"[StockInfo] THS business failed for {symbol}: {e}")

    return result


def _fetch_all(symbol: str) -> dict:
    """Fetch stock info from all sources and merge with fallback chain.

    Fallback strategy:
      Company info:  CNINFO ──fail──▶ EM (行业/简称/上市时间)
      Main business: CNINFO ──fail/weak──▶ THS 主营介绍
      Shares/PE/PB:  EM     ──fail──▶ (none; realtime quote has pe/pb already)
    """
    import time as _time

    t0 = _time.time()

    # cninfo is primary for company info (more reliable, richer data)
    cninfo_data = _fetch_from_cninfo(symbol)

    # THS is a good backup for 主营业务 / 产品结构
    ths_business_data = _fetch_from_ths_business(symbol)

    # EM is primary for shares/valuation, secondary for company info
    em_data = _fetch_from_em(symbol)

    # Merge: EM as base (shares + valuation + company fallback),
    #        THS supplements business fields,
    #        cninfo overrides company fields when available
    result: dict = {'symbol': symbol}
    result.update(em_data)       # EM: shares, valuation, company fallback
    result.update(ths_business_data)  # THS: business fallback
    result.update(cninfo_data)   # cninfo: overrides company fields, adds profile/business

    result['_fetched_at'] = datetime.now().isoformat()
    result['_cached'] = False

    logger.info(f"[StockInfo] total {_time.time() - t0:.1f}s for {symbol} "
                f"(cninfo={cninfo_data.get('_cninfo_ok', False)}, "
                f"ths_business={ths_business_data.get('_ths_business_ok', False)}, "
                f"em={em_data.get('_em_ok', False)})")
    return result


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_lock = None


@router.get("/info", summary="获取个股基本资料")
def get_stock_info(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的基本资料。

    数据源:
    - Primary: stock_profile_cninfo (巨潮资讯) — 公司名称、行业、上市日期、主营业务、公司简介
    - Supplementary: stock_individual_info_em (东方财富) — 总股本、流通股本、市盈率、市净率

    按天缓存。当 East Money 被屏蔽时，返回 cninfo 数据（部分字段可能为空）。
    """
    symbol = _normalize_symbol(symbol)

    if not force:
        cached = _cache_get(symbol)
        if cached:
            cached['_cached'] = True

            # Background refresh
            global _lock
            if _lock is None:
                _lock = threading.Lock()
            if _lock.acquire(blocking=False):
                def _bg_refresh():
                    try:
                        _cache_put(symbol, _fetch_all(symbol))
                    finally:
                        _lock.release()
                threading.Thread(target=_bg_refresh, daemon=True).start()

            return cached

    data = _fetch_all(symbol)
    _cache_put(symbol, data)
    return data


# ---------------------------------------------------------------------------
# Business Analysis Endpoint
# ---------------------------------------------------------------------------

BUSINESS_CACHE_KEY = "stock_business:v2"


def _business_cache_key(symbol: str) -> str:
    return f"{BUSINESS_CACHE_KEY}:{_normalize_symbol(symbol)}:{datetime.now().strftime('%Y%m%d')}"


def _business_cache_get(symbol: str) -> dict | None:
    try:
        from src.storage import DatabaseManager
        raw = DatabaseManager.get_instance().get_kline_snapshot(_business_cache_key(symbol))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and 'symbol' in data:
                logger.info(f"[Business] cache HIT {_business_cache_key(symbol)}")
                return data
    except Exception as e:
        logger.warning(f"[Business] cache read error: {e}")
    return None


def _business_cache_put(symbol: str, data: dict) -> None:
    try:
        from src.storage import DatabaseManager
        DatabaseManager.get_instance().save_kline_snapshot(
            _business_cache_key(symbol), json.dumps(data, ensure_ascii=False))
        logger.info(f"[Business] cache SAVED {_business_cache_key(symbol)}")
    except Exception as e:
        logger.warning(f"[Business] cache write error: {e}")


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _fetch_business_intro(symbol: str) -> dict:
    """Fetch business intro from THS (同花顺主营介绍)."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result = {}

    try:
        df = ak.stock_zyjs_ths(symbol=_normalize_symbol(symbol))
        if df is not None and not df.empty:
            row = df.iloc[0]
            result = {
                'main_business': str(row.get('主营业务', '') or '') or None,
                'business_scope': str(row.get('经营范围', '') or '') or None,
                'product_type': str(row.get('产品类型', '') or '') or None,
                'product_name': str(row.get('产品名称', '') or '') or None,
            }
            logger.info(f"[Business] THS intro OK for {symbol}: {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.warning(f"[Business] THS intro failed for {symbol}: {e}")

    return result


def _fetch_business_composition(symbol: str) -> list[dict]:
    """Fetch business composition from EM (东方财富主营构成)."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result = []

    try:
        df = ak.stock_zygc_em(symbol=f"SH{symbol}" if symbol.startswith(('6', '9')) else f"SZ{symbol}")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                item = {
                    'report_date': str(row.get('报告日期', '') or ''),
                    'category_type': str(row.get('分类类型', '') or ''),
                    'business_name': str(row.get('主营构成', '') or ''),
                    'revenue': _safe_float(row.get('主营收入')),
                    'revenue_pct': _safe_float(row.get('收入比例')),
                    'cost': _safe_float(row.get('主营成本')),
                    'cost_pct': _safe_float(row.get('成本比例')),
                    'profit': _safe_float(row.get('主营利润')),
                    'profit_pct': _safe_float(row.get('利润比例')),
                    'gross_margin': _safe_float(row.get('毛利率')),
                }
                result.append(item)
            logger.info(f"[Business] EM composition OK for {symbol}: {len(result)} items, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.warning(f"[Business] EM composition failed for {symbol}: {e}")

    return result


def _fetch_profit_forecast(symbol: str) -> list[dict]:
    """Fetch analyst profit forecasts from THS.

    Returns list of institutional forecasts with EPS/net profit estimates
    for upcoming fiscal years — shows market's forward expectations.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result = []

    try:
        df = ak.stock_profit_forecast_ths(
            symbol=_normalize_symbol(symbol),
            indicator='业绩预测详表-机构',
        )
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                item = {
                    'analyst': str(row.get('机构名称', '') or ''),
                    'researcher': str(row.get('研究员', '') or ''),
                    'eps_2026': _safe_float(row.get('预测年报每股收益2026预测')),
                    'eps_2027': _safe_float(row.get('预测年报每股收益2027预测')),
                    'eps_2028': _safe_float(row.get('预测年报每股收益2028预测')),
                    'net_profit_2026': _safe_float(row.get('预测年报净利润2026预测')),
                    'net_profit_2027': _safe_float(row.get('预测年报净利润2027预测')),
                    'net_profit_2028': _safe_float(row.get('预测年报净利润2028预测')),
                    'report_date': str(row.get('报告日期', '') or ''),
                }
                result.append(item)
            logger.info(f"[Business] profit forecast OK for {symbol}: {len(result)} entries, {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.warning(f"[Business] profit forecast failed for {symbol}: {e}")

    return result


def _fetch_financial_summary(symbol: str) -> dict:
    """Fetch key financial summary from Sina for trend analysis.

    Extracts recent periods of:
    - Revenue, net profit, operating cost
    - ROE, gross margin, net margin
    Shows whether the company is accelerating or decelerating.
    """
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {'key_metrics': [], 'growth_rates': []}

    try:
        df = ak.stock_financial_abstract(symbol=_normalize_symbol(symbol))
        if df is not None and not df.empty:
            period_cols = [c for c in df.columns if c not in ('选项', '指标')]
            period_cols.sort(reverse=True)  # newest first

            key_metrics = []
            for _, row in df.iterrows():
                category = str(row.get('选项', ''))
                metric_name = str(row.get('指标', ''))
                if category not in ('常用指标',):
                    continue
                if metric_name not in ('营业总收入', '归母净利润', '营业成本', '基本每股收益', '净资产收益率', '毛利率', '净利率'):
                    continue

                values = []
                for col in period_cols[:8]:  # last 8 periods
                    val = row.get(col)
                    values.append({
                        'period': col,
                        'value': _safe_float(val),
                    })
                key_metrics.append({
                    'name': metric_name,
                    'values': values,
                })

            result['key_metrics'] = key_metrics

            # Compute YoY growth rates for revenue and profit
            seen_metrics = set()
            for row_idx in range(len(df)):
                category = str(df.iloc[row_idx].get('选项', ''))
                metric_name = str(df.iloc[row_idx].get('指标', ''))
                if category != '常用指标':
                    continue
                if metric_name not in ('营业总收入', '归母净利润'):
                    continue
                if metric_name in seen_metrics:
                    continue
                seen_metrics.add(metric_name)
                row_data = df.iloc[row_idx]
                # Compare annual periods (YYYY1231)
                annual_periods = sorted([c for c in period_cols if c.endswith('1231')], reverse=True)
                growth_rates = []
                for i in range(len(annual_periods) - 1):
                    curr = _safe_float(row_data.get(annual_periods[i]))
                    prev = _safe_float(row_data.get(annual_periods[i + 1]))
                    if curr is not None and prev is not None and prev != 0:
                        growth_rates.append({
                            'period': annual_periods[i][:4],
                            'growth_rate': round((curr - prev) / abs(prev) * 100, 2),
                        })
                result['growth_rates'].append({
                    'name': metric_name,
                    'rates': growth_rates[:5],  # last 5 years
                })

            logger.info(f"[Business] financial summary OK for {symbol}: {_time.time() - t0:.1f}s")
    except Exception as e:
        logger.warning(f"[Business] financial summary failed for {symbol}: {e}")

    return result


def _fetch_recent_events(symbol: str) -> dict:
    """Fetch recent announcements and news for forward-looking analysis."""
    import time as _time
    import akshare as ak

    t0 = _time.time()
    result: dict = {'announcements': [], 'news': []}

    try:
        df = ak.stock_individual_notice_report(
            security=_normalize_symbol(symbol),
            symbol='全部',
            begin_date=datetime.now().strftime('%Y0101'),
            end_date=datetime.now().strftime('%Y%m%d'),
        )
        if df is not None and not df.empty:
            for _, row in df.head(20).iterrows():
                result['announcements'].append({
                    'title': str(row.get('公告标题', '') or ''),
                    'type': str(row.get('公告类型', '') or ''),
                    'date': str(row.get('公告日期', '') or ''),
                })
            logger.info(f"[Business] fetched {len(result['announcements'])} announcements for {symbol}")
    except Exception as e:
        logger.warning(f"[Business] announcements failed for {symbol}: {e}")

    try:
        df = ak.stock_news_em(symbol=_normalize_symbol(symbol))
        if df is not None and not df.empty:
            for _, row in df.head(15).iterrows():
                result['news'].append({
                    'title': str(row.get('新闻标题', '') or ''),
                    'content': str(row.get('新闻内容', '') or '')[:200],
                    'source': str(row.get('文章来源', '') or ''),
                    'time': str(row.get('发布时间', '') or ''),
                })
            logger.info(f"[Business] fetched {len(result['news'])} news for {symbol}")
    except Exception as e:
        logger.warning(f"[Business] news failed for {symbol}: {e}")

    logger.info(f"[Business] events fetch total {_time.time() - t0:.1f}s for {symbol}")
    return result


def _build_growth_text(financial_summary: dict) -> str:
    """Build sequential quarterly growth text (环比) from cumulative YTD data.

    Only applies to flow metrics (revenue, profit, cost, EPS) — rate/percentage
    metrics (gross margin, ROE, net margin) are skipped since sequential
    differencing doesn't make sense for percentages.
    """
    FLOW_METRICS = {'营业总收入', '归母净利润', '营业成本', '基本每股收益'}
    parts = []
    for km in financial_summary.get('key_metrics', []):
        if km['name'] not in FLOW_METRICS:
            continue

        # Sort periods chronologically (oldest first)
        cum_values = [(v['period'], v['value']) for v in km.get('values', []) if v.get('value') is not None]
        cum_values.sort(key=lambda x: x[0])

        if len(cum_values) < 2:
            continue

        # Extract single-quarter values from cumulative YTD figures
        # Q1 (0331) stands alone; Q2/Q3/Q4 are diffs from prior cumulative period
        single_quarters = []
        for i, (period, cum_val) in enumerate(cum_values):
            suffix = period[4:]
            if suffix == '0331':
                single_quarters.append((period, cum_val))
            elif i > 0:
                single_quarters.append((period, cum_val - cum_values[i - 1][1]))

        # Show last 6 quarters
        recent = single_quarters[-6:]
        items = []
        for i, (period, val) in enumerate(recent):
            year = period[:4]
            q_num = {'0331': 'Q1', '0630': 'Q2', '0930': 'Q3', '1231': 'Q4'}.get(period[4:], period[4:])
            label = f"{year}{q_num}"
            abs_str = f"{val / 1e8:.2f}亿" if abs(val) >= 1e6 else f"{val:.2f}"
            if i > 0:
                prev_val = recent[i - 1][1]
                if prev_val and prev_val != 0:
                    qoq = (val - prev_val) / abs(prev_val) * 100
                    items.append(f"{label} {abs_str}({qoq:+.1f}%)")
                else:
                    items.append(f"{label} {abs_str}")
            else:
                items.append(f"{label} {abs_str}")
        parts.append(f"  - {km['name']}(单季环比): {', '.join(items)}")
    return '\n'.join(parts)


def format_llm_input(
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Format complete prompt sent to LLM for display/traceability."""
    return f"[系统提示词]\n{system_prompt}\n\n[用户输入]\n{user_prompt}"


# ---------------------------------------------------------------------------
# Environment analysis helpers
# ---------------------------------------------------------------------------

def _fetch_macro_data() -> dict:
    """Fetch recent macro indicators (PMI, CPI, PPI) for environment analysis.

    Calls akshare directly via the project's existing fetcher functions (macro.py),
    takes the last 3 records (newest). Non-fatal on failure.
    """
    result = {}
    try:
        from api.v1.endpoints.macro import INDICATOR_FETCHERS

        for key, indicator_name in [('pmi', 'PMI'), ('cpi', 'CPI'), ('ppi', 'PPI')]:
            try:
                fetcher = INDICATOR_FETCHERS.get(indicator_name)
                if not fetcher:
                    continue
                records = fetcher()
                if records:
                    # fetcher returns oldest-first; take last 3 (newest)
                    result[key] = list(reversed(records[-3:]))
            except Exception as e:
                logger.warning(f"[Env] macro {indicator_name} fetch failed: {e}")
    except Exception as e:
        logger.warning(f"[Env] macro fetchers init failed: {e}")
    return result


def _build_environment_prompt(
    symbol: str,
    intro: dict,
    events: dict,
    macro_data: dict,
) -> tuple[str, str, str]:
    """Build system + user prompt for LLM environment analysis."""
    announcements_text = '\n'.join(
        f"  - [{a['date']}] [{a['type']}] {a['title']}"
        for a in events.get('announcements', [])[:15]
    )
    news_text = '\n'.join(
        f"  - [{n['time']}] [{n['source']}] {n['title']}" + (f"\n    {n['content'][:100]}" if n.get('content') else "")
        for n in events.get('news', [])[:10]
    )

    def _fmt_macro(records: list[dict]) -> str:
        if not records:
            return '暂无'
        return ', '.join(f"{r['period']}: {r['value']}" for r in records if r.get('value') is not None) or '暂无'

    system_prompt = """你是一个资深A股行业分析师，擅长从宏观环境和外部因素中判断对公司业务的影响。
请用中文回答，输出严格的 JSON 格式（不要用 markdown 代码块包裹）。
分析要简洁有力，每个维度不超过3句话。"""

    user_prompt = f"""请分析 {symbol}（{intro.get('main_business', '未知')}）所处的外部环境。

【公司所处行业】
- 主营业务：{intro.get('main_business', '未知')}
- 产品类型：{intro.get('product_type', '未知')}

【近期行业相关新闻/公告】
{news_text or '暂无'}

【近期公司公告】
{announcements_text or '暂无'}

【宏观经济数据】
- PMI（近3月）：{_fmt_macro(macro_data.get('pmi', []))}
- CPI（近3月）：{_fmt_macro(macro_data.get('cpi', []))}
- PPI（近3月）：{_fmt_macro(macro_data.get('ppi', []))}

请严格按以下 JSON 格式输出（不要输出其他内容）：
{{
  "policy": {{ "signal": "利好或利空或中性", "summary": "政策环境分析", "factors": ["关键信号1", "关键信号2"] }},
  "technology": {{ "signal": "...", "summary": "...", "factors": [...] }},
  "demand": {{ "signal": "...", "summary": "...", "factors": [...] }},
  "supply_competition": {{ "signal": "...", "summary": "...", "factors": [...] }},
  "macro_context": "宏观背景总结（1-2句话）"
}}"""

    return system_prompt, user_prompt, format_llm_input(system_prompt, user_prompt)


def _parse_environment_analysis(response_text: str, model_used: str, llm_input: str) -> dict:
    """Parse LLM JSON response for environment analysis.

    Falls back to raw_text if JSON parsing fails.
    """
    base = {'llm_used': True, 'model': model_used, 'llm_input': llm_input}

    # Strip markdown code fences if present
    cleaned = response_text.strip()
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        # Remove first and last lines if they are fences
        if lines[0].startswith('```') and lines[-1].strip() == '```':
            cleaned = '\n'.join(lines[1:-1])

    try:
        parsed = json.loads(cleaned)
        return {**base, **parsed}
    except json.JSONDecodeError:
        logger.warning(f"[Env] JSON parse failed, falling back to raw text")
        return {**base, 'raw_text': response_text}


# ---------------------------------------------------------------------------
# Track quality analysis helpers
# ---------------------------------------------------------------------------

def _fetch_peer_data(industry: str, target_symbol: str, max_peers: int = 3) -> list[dict]:
    """Fetch key financial metrics for industry peer companies.

    Gets peer list from stock_board_industry_cons_em, then fetches
    stock_financial_abstract for up to max_peers companies (excluding target).
    Returns compact list of peer snapshots.
    """
    if not industry:
        return []
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_board_industry_cons_em(symbol=industry)
        if df is None or df.empty:
            return []

        code_col = None
        name_col = None
        for c in df.columns:
            cl = c.lower()
            if '代码' in c or 'code' in cl:
                code_col = c
            if '名称' in c or 'name' in cl:
                name_col = c
        if code_col is None:
            return []

        peers = []
        for _, row in df.iterrows():
            code = str(row.get(code_col, '')).strip()
            if not code or code == target_symbol:
                continue
            name = str(row.get(name_col, '')).strip() if name_col else code
            if 'ST' in name.upper():
                continue
            peers.append((code, name))
            if len(peers) >= max_peers:
                break

        results = []
        for code, name in peers:
            try:
                fin_df = ak.stock_financial_abstract(symbol=code)
                if fin_df is None or fin_df.empty:
                    results.append({'name': name, 'symbol': code})
                    continue

                period_cols = [c for c in fin_df.columns if c not in ('选项', '指标')]
                period_cols.sort(reverse=True)

                snapshot: dict = {'name': name, 'symbol': code}

                for _, frow in fin_df.iterrows():
                    if str(frow.get('选项', '')) != '常用指标':
                        continue
                    metric = str(frow.get('指标', ''))
                    if metric == '营业总收入' and period_cols:
                        vals = [_safe_float(frow.get(p)) for p in period_cols[:3] if _safe_float(frow.get(p)) is not None]
                        if len(vals) >= 2 and vals[1] and vals[1] != 0:
                            snapshot['revenue_growth'] = round((vals[0] - vals[1]) / abs(vals[1]) * 100, 1)
                    elif metric == '毛利率' and period_cols:
                        recent = [_safe_float(frow.get(p)) for p in period_cols[:3]]
                        valid = [v for v in recent if v is not None]
                        if len(valid) >= 2:
                            if valid[0] > valid[-1] + 1:
                                snapshot['gross_margin_trend'] = '上升'
                            elif valid[0] < valid[-1] - 1:
                                snapshot['gross_margin_trend'] = '下降'
                            else:
                                snapshot['gross_margin_trend'] = '稳定'
                    elif metric == '归母净利润' and period_cols:
                        vals = [_safe_float(frow.get(p)) for p in period_cols[:3] if _safe_float(frow.get(p)) is not None]
                        if len(vals) >= 2 and vals[1] and vals[1] != 0:
                            snapshot['net_profit_growth'] = round((vals[0] - vals[1]) / abs(vals[1]) * 100, 1)

                results.append(snapshot)
            except Exception as e:
                logger.warning(f"[Track] peer {code} financial fetch failed: {e}")
                results.append({'name': name, 'symbol': code})

        return results

    except Exception as e:
        logger.warning(f"[Track] peer data fetch failed for industry={industry}: {e}")
        return []


def _get_stock_industry(symbol: str) -> str:
    """Get stock's industry name from individual info."""
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=_normalize_symbol(symbol), timeout=10)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                item = str(row.iloc[0]) if len(row) > 0 else ''
                value = str(row.iloc[1]) if len(row) > 1 else ''
                if '行业' in item:
                    return value
    except Exception as e:
        logger.warning(f"[Track] industry lookup failed for {symbol}: {e}")
    return ''


def _build_track_quality_prompt(
    symbol: str,
    intro: dict,
    industry: str,
    peer_data: list[dict],
    financial_summary: dict,
    profit_forecast: list[dict],
    events: dict,
) -> tuple[str, str, str]:
    """Build system + user prompt for LLM track quality analysis."""
    peer_text = '\n'.join(
        f"  - {p['name']}({p['symbol']}): "
        f"营收增速={p.get('revenue_growth', 'N/A')}%, "
        f"毛利率趋势={p.get('gross_margin_trend', 'N/A')}, "
        f"净利润增速={p.get('net_profit_growth', 'N/A')}%"
        for p in peer_data
    ) if peer_data else '暂无同行数据'

    announcements_text = '\n'.join(
        f"  - [{a['date']}] [{a['type']}] {a['title']}"
        for a in events.get('announcements', [])[:10]
    )
    news_text = '\n'.join(
        f"  - [{n['time']}] [{n['source']}] {n['title']}"
        for n in events.get('news', [])[:8]
    )

    growth_text = _build_growth_text(financial_summary)

    forecast_text = '\n'.join(
        f"  - {f['analyst']}: 2026E EPS={f['eps_2026']}, 2027E={f['eps_2027']}, 2028E={f['eps_2028']}"
        for f in profit_forecast[:5]
    ) if profit_forecast else '暂无预测数据'

    system_prompt = """你是一个资深A股行业研究员，擅长判断行业赛道的质量。
请用中文回答，输出严格的 JSON 格式（不要用 markdown 代码块包裹）。
分析要简洁有力，每个维度不超过3句话，evidence 中要有数据支撑。"""

    user_prompt = f"""请评估 {symbol}（{industry or '未知行业'}）所处行业赛道的质量。

【公司主营业务】
{intro.get('main_business', '未知')}

【近期增长趋势】
{growth_text or '暂无'}

【机构盈利预测】
{forecast_text}

【同行业可比公司数据】
{peer_text}

【近期新闻/公告信号】
{news_text or '暂无'}
{announcements_text or '暂无'}

请从以下 3 个维度评估赛道质量：

1. **行业周期位置**（cycle_position）：行业处于上升期/平稳期/下行期？依据是什么？
2. **未来空间**（growth_potential）：未来3年增长空间是否明确？驱动力是什么？
3. **竞争格局**（competition_intensity）：是格局良好/竞争一般/严重内卷？毛利率和同行数据是否支撑？

请严格按以下 JSON 格式输出（不要输出其他内容）：
{{
  "cycle_position": {{ "verdict": "上升期或平稳期或下行期", "evidence": "判断依据..." }},
  "growth_potential": {{ "verdict": "空间明确或增长一般或空间有限", "evidence": "..." }},
  "competition_intensity": {{ "verdict": "格局良好或竞争一般或严重内卷", "evidence": "..." }},
  "overall_verdict": "一句话总结赛道质量"
}}"""

    return system_prompt, user_prompt, format_llm_input(system_prompt, user_prompt)


def _parse_track_quality_analysis(response_text: str, model_used: str, llm_input: str, peer_data: list[dict]) -> dict:
    """Parse LLM JSON response for track quality analysis."""
    base = {'llm_used': True, 'model': model_used, 'llm_input': llm_input, 'peer_snapshot': peer_data}

    cleaned = response_text.strip()
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        if lines[0].startswith('```') and lines[-1].strip() == '```':
            cleaned = '\n'.join(lines[1:-1])

    try:
        parsed = json.loads(cleaned)
        return {**base, **parsed}
    except json.JSONDecodeError:
        logger.warning("[Track] JSON parse failed, falling back to raw text")
        return {**base, 'raw_text': response_text}


def _build_catalyst_prompt(
    symbol: str,
    intro: dict,
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
    environment_analysis: dict | None,
    track_quality: dict | None,
) -> tuple[str, str, str]:
    """Build system + user prompt for LLM catalyst analysis.

    Returns (system_prompt, user_prompt, llm_input_text).
    """
    announcements_text = '\n'.join(
        f"  - [{a['date']}] [{a['type']}] {a['title']}"
        for a in events.get('announcements', [])[:15]
    ) or '暂无公告'
    news_text = '\n'.join(
        f"  - [{n['time']}] [{n['source']}] {n['title']}"
        for n in events.get('news', [])[:10]
    ) or '暂无新闻'

    forecast_text = '\n'.join(
        f"  - {f['analyst']}({f['researcher']}): "
        f"2026E EPS={f['eps_2026']}, 2027E={f['eps_2027']}, 2028E={f['eps_2028']}"
        for f in profit_forecast[:8]
    ) if profit_forecast else '暂无机构预测数据'

    growth_text = _build_growth_text(financial_summary)

    # Extract environment summary
    env_summary = ''
    if environment_analysis and environment_analysis.get('llm_used'):
        dims = []
        for key, label in [('policy', '政策'), ('technology', '技术'), ('demand', '需求'), ('supply_competition', '供给')]:
            dim = environment_analysis.get(key, {})
            if dim.get('signal'):
                dims.append(f"{label}: {dim['signal']}")
        if dims:
            env_summary = '、'.join(dims)
        if environment_analysis.get('overall_verdict'):
            env_summary += f"；综合: {environment_analysis['overall_verdict']}"

    # Extract track quality summary
    track_summary = ''
    if track_quality and track_quality.get('llm_used'):
        parts = []
        for key, label in [('cycle_position', '周期'), ('growth_potential', '空间'), ('competition_intensity', '竞争')]:
            dim = track_quality.get(key, {})
            if dim.get('verdict'):
                parts.append(f"{label}={dim['verdict']}")
        if parts:
            track_summary = '、'.join(parts)
        if track_quality.get('overall_verdict'):
            track_summary += f"；综合: {track_quality['overall_verdict']}"

    system_prompt = """你是一个资深A股策略分析师，擅长判断个股未来 6-12 个月的催化剂。

催化剂是指能够驱动股价出现趋势性行情的具体事件或条件变化，包括但不限于：
- 业绩催化：财报超预期、业绩预告、盈利拐点
- 政策催化：产业政策落地、补贴发放、监管放松
- 事件催化：重大合同、产品发布、并购重组、股权激励
- 行业催化：行业景气度上行、供需拐点、技术突破
- 资金催化：纳入指数、大股东增持、回购计划

请严格基于提供的信息分析，不要编造不存在的事件。对于推断性催化，需标注置信度。

输出格式为 JSON（不要包含 markdown 代码块标记）：
{
  "overall_assessment": "催化充分/催化一般/催化不足",
  "summary": "一句话概括未来6-12个月催化情况（不超过40字）",
  "catalysts": [
    {
      "type": "业绩催化/政策催化/事件催化/行业催化/资金催化",
      "description": "具体描述催化事件",
      "timeframe": "预计触发时间范围，如 2026Q3、2026年下半年",
      "confidence": "高/中/低",
      "impact": "重大/中等/有限"
    }
  ],
  "key_dates": ["需要关注的关键日期或时间窗口"],
  "risks": ["催化可能落空的风险点"]
}"""

    user_prompt = f"""请分析 {symbol} 未来 6-12 个月的催化剂情况。

【公司基本面】
- 主营业务：{intro.get('main_business', '未知')}
- 产品类型：{intro.get('product_type', '未知')}

【机构盈利预测】
{forecast_text}

【财务增长趋势】
{growth_text}

【近期公告】
{announcements_text}

【近期新闻】
{news_text}

【外部环境评估】
{env_summary or '暂无外部环境分析'}

【赛道质量评估】
{track_summary or '暂无赛道质量分析'}

请基于以上信息，判断未来 6-12 个月该公司是否有足够的催化剂驱动股价表现。
重点回答：
1. 有哪些具体的催化事件可以期待？
2. 这些催化的时间窗口和确定性如何？
3. 催化落空的主要风险是什么？"""

    return system_prompt, user_prompt, format_llm_input(system_prompt, user_prompt)


def _parse_catalyst_analysis(response_text: str, model_used: str, llm_input: str) -> dict:
    """Parse LLM JSON response for catalyst analysis."""
    base = {'llm_used': True, 'model': model_used, 'llm_input': llm_input}

    cleaned = response_text.strip()
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        if lines[0].startswith('```') and lines[-1].strip() == '```':
            cleaned = '\n'.join(lines[1:-1])

    try:
        parsed = json.loads(cleaned)
        # Ensure catalysts is a list
        if 'catalysts' not in parsed or not isinstance(parsed['catalysts'], list):
            parsed['catalysts'] = []
        if 'key_dates' not in parsed or not isinstance(parsed['key_dates'], list):
            parsed['key_dates'] = []
        if 'risks' not in parsed or not isinstance(parsed['risks'], list):
            parsed['risks'] = []
        return {**base, **parsed}
    except json.JSONDecodeError:
        logger.warning("[Catalyst] JSON parse failed, falling back to raw text")
        return {**base, 'raw_text': response_text}


def _build_business_prompt(
    symbol: str,
    intro: dict,
    composition: list[dict],
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
) -> tuple[str, str, str]:
    """Build system + user prompt for LLM business analysis."""
    announcements_text = '\n'.join(
        f"  - [{a['date']}] [{a['type']}] {a['title']}"
        for a in events.get('announcements', [])[:15]
    )
    news_text = '\n'.join(
        f"  - [{n['time']}] [{n['source']}] {n['title']}" + (f"\n    {n['content'][:120]}" if n.get('content') else "")
        for n in events.get('news', [])[:10]
    )

    composition_text = '\n'.join(
        f"  - {c['business_name']}: 收入占比{(c['revenue_pct']*100):.1f}%" + (f", 毛利率{(c['gross_margin']*100):.1f}%" if c.get('gross_margin') is not None else "")
        for c in composition
        if c.get('category_type') == '按产品分类' and c.get('revenue_pct') is not None
    )[:500]

    forecast_text = '\n'.join(
        f"  - {f['analyst']}({f['researcher']}): 2026E EPS={f['eps_2026']}, 2027E={f['eps_2027']}, 2028E={f['eps_2028']}"
        for f in profit_forecast[:8]
    ) if profit_forecast else '暂无预测数据'

    growth_text = _build_growth_text(financial_summary)

    system_prompt = """你是一个资深A股分析师，擅长挖掘公司业务动向和投资逻辑。
请用中文回答，格式使用 Markdown。回答要简洁有力，每段不超过3句话。
重点挖掘：公司最近在做什么、战略方向是什么、未来会怎样。"""

    user_prompt = f"""请分析 {symbol} 的业务动向和投资前景。

【公司基本情况】
- 主营业务：{intro.get('main_business', '未知')}
- 产品类型：{intro.get('product_type', '未知')}
- 主营构成（产品）：{composition_text or '暂无'}

【近期增长趋势】
{growth_text or '暂无'}

【机构盈利预测】
{forecast_text}

【近期公告（{len(events.get("announcements", []))}条）】
{announcements_text or '暂无'}

【近期新闻（{len(events.get("news", []))}条）】
{news_text or '暂无'}

请按以下结构分析：
1. **公司动态**：从公告和新闻中提炼公司最近在做什么（高管变动、分红、回购、新项目等）
2. **业务趋势**：结合增长数据和主营构成，分析公司核心业务的健康度和变化方向
3. **机构观点**：从盈利预测中总结市场对公司的预期（增长/放缓/转型）
4. **关键判断**：用1-2句话总结这家公司的核心投资逻辑和主要风险"""

    return system_prompt, user_prompt, format_llm_input(system_prompt, user_prompt)


def _generate_llm_business_analysis(
    symbol: str,
    intro: dict,
    composition: list[dict],
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
) -> dict:
    """Call LLM to generate forward-looking business analysis."""
    t0 = time.time()

    from src.analyzer import get_analyzer
    from src.ai_caller import call_ai_structured

    analyzer = get_analyzer()
    if not getattr(analyzer, "is_available", lambda: False)():
        logger.warning("[Business] LLM not available, skipping analysis")
        return {'llm_used': False, 'error': '模型服务不可用'}

    system_prompt, user_prompt, llm_input = _build_business_prompt(
        symbol, intro, composition, profit_forecast, financial_summary, events,
    )

    try:
        response_text, model_used, _usage = call_ai_structured(
            analyzer,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            call_type="business_analysis",
            temperature=0.3,
            max_tokens=2048,
            response_validator=lambda _text: None,
            stream=False,
        )
        logger.info(f"[Business] LLM analysis OK for {symbol}: {time.time() - t0:.1f}s, model={model_used}")
        return {
            'llm_used': True,
            'model': model_used,
            'analysis': response_text,
            'llm_input': llm_input,
        }
    except Exception as e:
        logger.error(f"[Business] LLM analysis failed for {symbol}: {e}")
        return {'llm_used': False, 'error': str(e)}


def _format_business_sse_event(event_type: str, data) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/business", summary="获取个股业务分析数据")
def get_stock_business(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    """获取单只股票的业务分析数据。

    数据源:
    - 同花顺主营介绍: 主营业务、经营范围、产品类型、产品名称
    - 东方财富主营构成: 按行业/产品/地区的收入、成本、利润、毛利率

    按天缓存。
    """
    symbol = _normalize_symbol(symbol)

    if not force:
        cached = _business_cache_get(symbol)
        if cached:
            cached['_cached'] = True
            analysis = cached.get('llm_analysis', {})
            if not analysis.get('llm_input'):
                _sys_p, _usr_p, _inp = _build_business_prompt(
                    symbol,
                    cached.get('intro', {}),
                    cached.get('composition', []),
                    cached.get('profit_forecast', []),
                    cached.get('financial_summary', {}),
                    cached.get('events', {}),
                )
                analysis['llm_input'] = _inp
            return cached

    # Parallel data fetching
    _results: dict = {}

    def _fetch_into(key: str, fn) -> None:
        _results[key] = fn()

    threads = [
        threading.Thread(target=_fetch_into, args=('intro', lambda: _fetch_business_intro(symbol))),
        threading.Thread(target=_fetch_into, args=('composition', lambda: _fetch_business_composition(symbol))),
        threading.Thread(target=_fetch_into, args=('profit_forecast', lambda: _fetch_profit_forecast(symbol))),
        threading.Thread(target=_fetch_into, args=('financial_summary', lambda: _fetch_financial_summary(symbol))),
        threading.Thread(target=_fetch_into, args=('events', lambda: _fetch_recent_events(symbol))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    intro = _results.get('intro', {})
    composition = _results.get('composition', [])
    profit_forecast = _results.get('profit_forecast', [])
    financial_summary = _results.get('financial_summary', {})
    events = _results.get('events', {})

    llm_analysis = _generate_llm_business_analysis(
        symbol, intro, composition, profit_forecast, financial_summary, events,
    )

    # Environment analysis
    environment_analysis = {'llm_used': False}
    try:
        from src.analyzer import get_analyzer
        from src.ai_caller import call_ai_structured

        analyzer = get_analyzer()
        if getattr(analyzer, "is_available", lambda: False)():
            macro_data = _fetch_macro_data()
            env_sys, env_usr, env_input = _build_environment_prompt(
                symbol, intro, events, macro_data,
            )
            env_response, env_model, _usage = call_ai_structured(
                analyzer,
                system_prompt=env_sys,
                user_prompt=env_usr,
                call_type="environment_analysis",
                temperature=0.3,
                max_tokens=2048,
                response_validator=lambda _text: None,
                stream=False,
            )
            environment_analysis = _parse_environment_analysis(env_response, env_model, env_input)
            logger.info(f"[Business] Env analysis OK for {symbol}: model={env_model}")
    except Exception as e:
        logger.warning(f"[Business] Env analysis failed for {symbol}: {e}")

    # Track quality analysis
    track_quality = {'llm_used': False}
    try:
        from src.analyzer import get_analyzer
        from src.ai_caller import call_ai_structured

        analyzer = get_analyzer()
        if getattr(analyzer, "is_available", lambda: False)():
            industry = _get_stock_industry(symbol)
            peer_data = _fetch_peer_data(industry, symbol, max_peers=3)
            track_sys, track_usr, track_input = _build_track_quality_prompt(
                symbol, intro, industry, peer_data, financial_summary, profit_forecast, events,
            )
            track_response, track_model, _usage = call_ai_structured(
                analyzer,
                system_prompt=track_sys,
                user_prompt=track_usr,
                call_type="track_quality",
                temperature=0.3,
                max_tokens=2048,
                response_validator=lambda _text: None,
                stream=False,
            )
            track_quality = _parse_track_quality_analysis(track_response, track_model, track_input, peer_data)
            logger.info(f"[Business] Track analysis OK for {symbol}: model={track_model}")
    except Exception as e:
        logger.warning(f"[Business] Track analysis failed for {symbol}: {e}")

    data = {
        'symbol': symbol,
        'intro': intro,
        'composition': composition,
        'profit_forecast': profit_forecast,
        'financial_summary': financial_summary,
        'events': events,
        'llm_analysis': llm_analysis,
        'environment_analysis': environment_analysis,
        'track_quality': track_quality,
        '_fetched_at': datetime.now().isoformat(),
        '_cached': False,
    }

    data = _sanitize(data)
    _business_cache_put(symbol, data)
    return data


@router.get("/business/stream", summary="获取个股业务分析数据 (SSE 流)")
async def get_stock_business_stream(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    symbol = _normalize_symbol(symbol)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    SENTINEL_DONE = object()

    def _enqueue(event_type: str, data):
        loop.call_soon_threadsafe(queue.put_nowait, (event_type, data))

    def _worker():
        try:
            if not force:
                cached = _business_cache_get(symbol)
                if cached:
                    cached['_cached'] = True
                    analysis = cached.get('llm_analysis', {})
                    if not analysis.get('llm_input'):
                        _sys_p, _usr_p, _inp = _build_business_prompt(
                            symbol,
                            cached.get('intro', {}),
                            cached.get('composition', []),
                            cached.get('profit_forecast', []),
                            cached.get('financial_summary', {}),
                            cached.get('events', {}),
                        )
                        analysis['llm_input'] = _inp
                    _enqueue("connected", {"message": "Connected", "cached": True})
                    _enqueue("analysis_start", {"cached": True})
                    _enqueue("analysis_chunk", {"text": analysis.get('analysis', '')})
                    _enqueue("analysis_done", cached)
                    # Environment analysis from cache
                    env_analysis = cached.get('environment_analysis')
                    if env_analysis and env_analysis.get('llm_used'):
                        _enqueue("env_analysis_start", {})
                        env_text = env_analysis.get('macro_context', '')
                        if env_text:
                            _enqueue("env_analysis_chunk", {"text": env_text})
                        _enqueue("env_analysis_done", cached)
                    else:
                        _enqueue("env_analysis_done", cached)
                    # Track quality from cache
                    track_analysis = cached.get('track_quality')
                    if track_analysis and track_analysis.get('llm_used'):
                        _enqueue("track_analysis_start", {})
                        track_text = track_analysis.get('overall_verdict', '')
                        if track_text:
                            _enqueue("track_analysis_chunk", {"text": track_text})
                        _enqueue("track_analysis_done", cached)
                    else:
                        _enqueue("track_analysis_done", cached)
                    return

            _enqueue("connected", {"message": "Connected", "cached": False})

            _results: dict = {}
            fetchers = [
                ('intro', '主营介绍', lambda: _fetch_business_intro(symbol)),
                ('composition', '主营构成', lambda: _fetch_business_composition(symbol)),
                ('profit_forecast', '盈利预测', lambda: _fetch_profit_forecast(symbol)),
                ('financial_summary', '财务摘要', lambda: _fetch_financial_summary(symbol)),
                ('events', '公告新闻', lambda: _fetch_recent_events(symbol)),
            ]

            completed = [0]
            lock = threading.Lock()

            def _fetch_with_progress(key: str, label: str, fn):
                try:
                    result = fn()
                except Exception as e:
                    logger.warning(f"[Business SSE] {label} failed for {symbol}: {e}")
                    result = {} if key == 'intro' else ([] if key != 'events' else {'announcements': [], 'news': []})
                _results[key] = result
                with lock:
                    completed[0] += 1
                    _enqueue("progress", {
                        "step": completed[0],
                        "total": len(fetchers),
                        "label": label,
                        "done": completed[0] == len(fetchers),
                    })

            threads = [
                threading.Thread(target=_fetch_with_progress, args=(key, label, fn))
                for key, label, fn in fetchers
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            intro = _results.get('intro', {})
            composition = _results.get('composition', [])
            profit_forecast = _results.get('profit_forecast', [])
            financial_summary = _results.get('financial_summary', {})
            events = _results.get('events', {})

            from src.analyzer import get_analyzer
            from src.ai_caller import call_ai_structured

            analyzer = get_analyzer()
            if not getattr(analyzer, "is_available", lambda: False)():
                _enqueue("error", {"message": "模型服务不可用"})
                return

            _enqueue("analysis_start", {"cached": False})

            system_prompt, user_prompt, formatted_input = _build_business_prompt(
                symbol, intro, composition, profit_forecast, financial_summary, events,
            )

            response_text = ""

            def _on_text(delta: str, full_text: str):
                _enqueue("analysis_chunk", {"text": delta})

            response_text, model_used, _usage = call_ai_structured(
                analyzer,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                call_type="business_analysis",
                temperature=0.3,
                max_tokens=2048,
                response_validator=lambda _text: None,
                stream=True,
                stream_text_callback=_on_text,
            )

            logger.info(f"[Business SSE] LLM analysis OK for {symbol}: model={model_used}")

            data = {
                'symbol': symbol,
                'intro': intro,
                'composition': composition,
                'profit_forecast': profit_forecast,
                'financial_summary': financial_summary,
                'events': events,
                'llm_analysis': {
                    'llm_used': True,
                    'model': model_used,
                    'analysis': response_text,
                    'llm_input': formatted_input,
                },
                '_fetched_at': datetime.now().isoformat(),
                '_cached': False,
            }
            _enqueue("analysis_done", data)

            # --- Environment analysis ---
            try:
                _enqueue("env_analysis_start", {})

                macro_data = _fetch_macro_data()
                env_sys, env_usr, env_input = _build_environment_prompt(
                    symbol, intro, events, macro_data,
                )

                env_text = ""

                def _on_env_text(delta: str, full_text: str):
                    _enqueue("env_analysis_chunk", {"text": delta})

                env_response, env_model, _env_usage = call_ai_structured(
                    analyzer,
                    system_prompt=env_sys,
                    user_prompt=env_usr,
                    call_type="environment_analysis",
                    temperature=0.3,
                    max_tokens=2048,
                    response_validator=lambda _text: None,
                    stream=True,
                    stream_text_callback=_on_env_text,
                )

                env_analysis = _parse_environment_analysis(env_response, env_model, env_input)
                data['environment_analysis'] = env_analysis
                logger.info(f"[Business SSE] Env analysis OK for {symbol}: model={env_model}")

            except Exception as e:
                logger.warning(f"[Business SSE] Env analysis failed for {symbol}: {e}")
                data['environment_analysis'] = {'llm_used': False, 'error': str(e)}

            data = _sanitize(data)
            _business_cache_put(symbol, data)
            _enqueue("env_analysis_done", data)

            # --- Track quality analysis ---
            try:
                _enqueue("track_analysis_start", {})

                industry = _get_stock_industry(symbol)
                peer_data = _fetch_peer_data(industry, symbol, max_peers=3)

                track_sys, track_usr, track_input = _build_track_quality_prompt(
                    symbol, intro, industry, peer_data, financial_summary, profit_forecast, events,
                )

                def _on_track_text(delta: str, full_text: str):
                    _enqueue("track_analysis_chunk", {"text": delta})

                track_response, track_model, _track_usage = call_ai_structured(
                    analyzer,
                    system_prompt=track_sys,
                    user_prompt=track_usr,
                    call_type="track_quality",
                    temperature=0.3,
                    max_tokens=2048,
                    response_validator=lambda _text: None,
                    stream=True,
                    stream_text_callback=_on_track_text,
                )

                track_analysis = _parse_track_quality_analysis(track_response, track_model, track_input, peer_data)
                data['track_quality'] = track_analysis
                logger.info(f"[Business SSE] Track analysis OK for {symbol}: model={track_model}")

            except Exception as e:
                logger.warning(f"[Business SSE] Track analysis failed for {symbol}: {e}")
                data['track_quality'] = {'llm_used': False, 'error': str(e)}

            data = _sanitize(data)
            _business_cache_put(symbol, data)
            _enqueue("track_analysis_done", data)

        except Exception as e:
            logger.exception(f"[Business SSE] failed for {symbol}: {e}")
            _enqueue("error", {"message": str(e)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, (SENTINEL_DONE, None))

    threading.Thread(target=_worker, daemon=True).start()

    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            item = await queue.get()
            event_type, data = item
            if event_type is SENTINEL_DONE:
                break
            yield _format_business_sse_event(event_type, data)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
