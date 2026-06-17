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

    data = {
        'symbol': symbol,
        'intro': intro,
        'composition': composition,
        'profit_forecast': profit_forecast,
        'financial_summary': financial_summary,
        'events': events,
        'llm_analysis': llm_analysis,
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
            data = _sanitize(data)
            _business_cache_put(symbol, data)
            _enqueue("analysis_done", data)

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
