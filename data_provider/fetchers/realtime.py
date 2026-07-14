# -*- coding: utf-8 -*-
"""Realtime quote fetchers — EastMoney push, EastMoney full-scan, Xueqiu, Sina, Tencent."""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

import pandas as pd
import requests

from ..constants import USER_AGENTS, SINA_REALTIME_ENDPOINT, TENCENT_REALTIME_ENDPOINT
from ..circuit_breaker import get_realtime_circuit_breaker
from ..cache import realtime_cache
from ..realtime_types import (
    UnifiedRealtimeQuote, RealtimeSource,
    safe_float, safe_int,
)
from ..utils import is_bse_code, normalize_stock_code

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────


def _to_sina_tx_symbol(stock_code: str) -> str:
    """Convert a stock code to Sina/Tencent API symbol format."""
    code = stock_code.strip()
    if code.startswith(('6', '5', '90')):
        return f"sh{code}"
    elif code.startswith(('8', '4', '9')):
        return f"bj{code}"
    else:
        return f"sz{code}"


# ── EM Push ──────────────────────────────────────────────────────────────


def _get_stock_realtime_quote_em_push(stock_code: str) -> Optional[UnifiedRealtimeQuote]:
    """A 股实时行情（东方财富 push API，单股查询，~0.2s）。"""
    source_key = RealtimeSource.EASTMONEY_PUSH.value
    breaker = get_realtime_circuit_breaker()
    if not breaker.is_available(source_key):
        logger.info("[熔断跳过] 东方财富 push 接口暂不可用")
        return None

    if is_bse_code(stock_code):
        secid = f"0.{stock_code}"
    elif stock_code.startswith(("6", "5", "90")):
        secid = f"1.{stock_code}"
    else:
        secid = f"0.{stock_code}"

    url = "https://push2.eastmoney.com/api/qt/stock/get"
    try:
        resp = requests.get(
            url,
            params={
                "secid": secid,
                "fields": "f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,f162,f167,f168,f169,f170,f171",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            },
            headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": random.choice(USER_AGENTS),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            breaker.record_failure(source_key, f"HTTP {resp.status_code}")
            return None

        data = resp.json()
        d = data.get("data")
        if not d or d.get("f43") is None or d.get("f43") == "-":
            breaker.record_failure(source_key, "empty_payload")
            return None

        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=str(d.get("f58", "")),
            source=RealtimeSource.EASTMONEY_PUSH,
            price=safe_float(d.get("f43")) / 100 if d.get("f43") else None,
            change_pct=safe_float(d.get("f170")) / 100 if d.get("f170") else None,
            change_amount=safe_float(d.get("f169")) / 100 if d.get("f169") else None,
            volume=safe_int(d.get("f47")),
            amount=safe_float(d.get("f48")),
            volume_ratio=safe_float(d.get("f50")) / 100 if d.get("f50") else None,
            turnover_rate=safe_float(d.get("f168")) / 100 if d.get("f168") else None,
            amplitude=safe_float(d.get("f171")) / 100 if d.get("f171") else None,
            open_price=safe_float(d.get("f46")) / 100 if d.get("f46") else None,
            high=safe_float(d.get("f44")) / 100 if d.get("f44") else None,
            low=safe_float(d.get("f45")) / 100 if d.get("f45") else None,
            pre_close=safe_float(d.get("f60")) / 100 if d.get("f60") else None,
            pe_ratio=safe_float(d.get("f162")) / 100 if d.get("f162") else None,
            pb_ratio=safe_float(d.get("f167")) / 100 if d.get("f167") else None,
            total_mv=safe_float(d.get("f116")),
            circ_mv=safe_float(d.get("f117")),
        )
        breaker.record_success(source_key)
        logger.info("[实时行情-东财push] %s %s: 价格=%s, 涨跌=%s%%", stock_code, quote.name, quote.price, quote.change_pct)
        return quote
    except Exception as e:
        detail = str(e)
        category = type(e).__name__
        breaker.record_failure(source_key, f"category={category} detail={detail}")
        logger.info("[API错误] 东方财富 push 实时行情失败: %s", detail)
        return None


# ── EM Full-scan ─────────────────────────────────────────────────────────


def _get_stock_realtime_quote_em(stock_code: str) -> Optional[UnifiedRealtimeQuote]:
    """A 股实时行情（东方财富全量拉取，带缓存）。"""
    import akshare as ak
    import concurrent.futures

    try:
        current_time = time.time()
        df = realtime_cache.get()
        cache_hit = df is not None
        if cache_hit:
            cache_age = realtime_cache.age()
            logger.debug("[缓存命中] A股实时行情(东财) - 缓存年龄 %ds/%ds", cache_age, realtime_cache.ttl)
        else:
            logger.info("[缓存未命中] 触发全量刷新 A股实时行情(东财)")
            last_error: Optional[Exception] = None
            df = None
            _EM_TIMEOUT = 20

            import time as _time
            api_start = _time.time()

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(ak.stock_zh_a_spot_em)
                    df = future.result(timeout=_EM_TIMEOUT)
                api_elapsed = _time.time() - api_start
                logger.info("[API返回] ak.stock_zh_a_spot_em 成功: 返回 %d 只股票, 耗时 %.2fs", len(df), api_elapsed)
            except concurrent.futures.TimeoutError:
                last_error = TimeoutError(f"ak.stock_zh_a_spot_em 超时 ({_EM_TIMEOUT}s)")
                api_elapsed = _time.time() - api_start
                logger.info("[API超时] ak.stock_zh_a_spot_em 超时 (耗时 %.2fs)，走降级源", api_elapsed)
                df = None
            except Exception as e:
                last_error = e
                api_elapsed = _time.time() - api_start
                logger.info("[API错误] ak.stock_zh_a_spot_em 失败: %s (耗时 %.2fs)", e, api_elapsed)

            if df is None:
                df = pd.DataFrame()
            if df is not None and not df.empty:
                realtime_cache.set(df)
                logger.info("[缓存更新] A股实时行情(东财) 缓存已刷新，TTL=%ds", realtime_cache.ttl)
            else:
                logger.info("[缓存跳过] A股实时行情(东财) 获取为空，不覆盖现有缓存")

        if df is None or df.empty:
            logger.info("[实时行情] A股实时行情数据为空，跳过 %s", stock_code)
            return None

        row = df[df['代码'] == stock_code]
        if row.empty:
            logger.info("[API返回] 未找到股票 %s 的实时行情", stock_code)
            return None

        row = row.iloc[0]
        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=str(row.get('名称', '')),
            source=RealtimeSource.AKSHARE_EM,
            price=safe_float(row.get('最新价')),
            change_pct=safe_float(row.get('涨跌幅')),
            change_amount=safe_float(row.get('涨跌额')),
            volume=safe_int(row.get('成交量')),
            amount=safe_float(row.get('成交额')),
            volume_ratio=safe_float(row.get('量比')),
            turnover_rate=safe_float(row.get('换手率')),
            amplitude=safe_float(row.get('振幅')),
            open_price=safe_float(row.get('今开')),
            high=safe_float(row.get('最高')),
            low=safe_float(row.get('最低')),
            pe_ratio=safe_float(row.get('市盈率-动态')),
            pb_ratio=safe_float(row.get('市净率')),
            total_mv=safe_float(row.get('总市值')),
            circ_mv=safe_float(row.get('流通市值')),
            change_60d=safe_float(row.get('60日涨跌幅')),
            # stock_zh_a_spot_em 不含 52 周高低列，high_52w/low_52w 留空
        )
        logger.info("[实时行情-东财] %s %s: 价格=%s, 涨跌=%s%%", stock_code, quote.name, quote.price, quote.change_pct)
        return quote

    except Exception as e:
        logger.info("[API错误] 获取 %s 实时行情(东财)失败: %s", stock_code, e)
        return None


# ── Xueqiu ───────────────────────────────────────────────────────────────


def _get_stock_realtime_quote_xueqiu(stock_code: str) -> Optional[UnifiedRealtimeQuote]:
    """A 股实时行情（雪球）。"""
    source_key = RealtimeSource.XUEQIU.value
    breaker = get_realtime_circuit_breaker()
    if not breaker.is_available(source_key):
        logger.info("[熔断跳过] 雪球实时行情接口暂不可用")
        return None

    if is_bse_code(stock_code):
        symbol = f"BJ{stock_code}"
    elif stock_code.startswith(("6", "5", "90")):
        symbol = f"SH{stock_code}"
    else:
        symbol = f"SZ{stock_code}"

    try:
        session = requests.Session()
        session.get(
            "https://xueqiu.com/",
            headers={"User-Agent": random.choice(USER_AGENTS)},
            timeout=5,
        )
        resp = session.get(
            "https://stock.xueqiu.com/v5/stock/quote.json",
            params={"symbol": symbol, "extend": "detail"},
            headers={
                "Referer": "https://xueqiu.com/",
                "User-Agent": random.choice(USER_AGENTS),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            breaker.record_failure(source_key, f"HTTP {resp.status_code}")
            return None

        result = resp.json()
        if result.get("error_code") != 0 or not result.get("data"):
            breaker.record_failure(source_key, "api_error")
            return None

        quote_data = result["data"].get("quote", {})
        if not quote_data or not quote_data.get("current"):
            breaker.record_failure(source_key, "empty_payload")
            return None

        current = safe_float(quote_data.get("current"))
        last_close = safe_float(quote_data.get("last_close"))
        change_amount = safe_float(quote_data.get("chg"))
        if change_amount is None and current is not None and last_close is not None:
            change_amount = round(current - last_close, 4)

        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=str(quote_data.get("name", "")),
            source=RealtimeSource.XUEQIU,
            price=current,
            change_pct=safe_float(quote_data.get("percent")),
            change_amount=change_amount,
            volume=safe_int(quote_data.get("volume")),
            amount=safe_float(quote_data.get("amount")),
            turnover_rate=safe_float(quote_data.get("turnover_rate")),
            open_price=safe_float(quote_data.get("open")),
            high=safe_float(quote_data.get("high")),
            low=safe_float(quote_data.get("low")),
            pre_close=last_close,
            pe_ratio=safe_float(quote_data.get("pe_ttm")),
            pb_ratio=safe_float(quote_data.get("pb")),
            total_mv=safe_float(quote_data.get("market_capital")),
            circ_mv=safe_float(quote_data.get("float_market_capital")),
            high_52w=safe_float(quote_data.get("high52w")),
            low_52w=safe_float(quote_data.get("low52w")),
        )
        breaker.record_success(source_key)
        logger.info("[实时行情-雪球] %s %s: 价格=%s, 涨跌=%s%%", stock_code, quote.name, quote.price, quote.change_pct)
        return quote
    except Exception as e:
        detail = str(e)
        category = type(e).__name__
        breaker.record_failure(source_key, f"category={category} detail={detail}")
        logger.info("[API错误] 雪球实时行情失败: %s", detail)
        return None


# ── Sina ─────────────────────────────────────────────────────────────────


def _get_stock_realtime_quote_sina(stock_code: str) -> Optional[UnifiedRealtimeQuote]:
    """A 股实时行情（新浪财经接口）。"""
    source_key = RealtimeSource.AKSHARE_SINA.value
    breaker = get_realtime_circuit_breaker()
    if not breaker.is_available(source_key):
        logger.info("[熔断跳过] 新浪实时行情接口暂不可用 endpoint=%s", SINA_REALTIME_ENDPOINT)
        return None

    symbol = _to_sina_tx_symbol(stock_code)
    url = f"https://{SINA_REALTIME_ENDPOINT}"
    try:
        response = requests.get(
            url,
            params={"list": symbol},
            headers={
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": random.choice(USER_AGENTS),
            },
            timeout=10,
        )
        if response.status_code != 200:
            breaker.record_failure(source_key, f"category=http_status endpoint={SINA_REALTIME_ENDPOINT} detail=HTTP {response.status_code}")
            return None

        response.encoding = "gbk"
        text = response.text
        if '=""' in text:
            breaker.record_failure(source_key, f"category=empty_payload endpoint={SINA_REALTIME_ENDPOINT}")
            return None

        payload = text.split('"', 2)[1] if '"' in text else ""
        fields = payload.split(",")
        if len(fields) < 32:
            breaker.record_failure(source_key, f"category=malformed_payload endpoint={SINA_REALTIME_ENDPOINT}")
            return None

        price = safe_float(fields[3])
        pre_close = safe_float(fields[2])
        change_amount = price - pre_close if price is not None and pre_close not in (None, 0) else None
        change_pct = (change_amount / pre_close * 100) if change_amount is not None and pre_close else None

        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=fields[0],
            source=RealtimeSource.AKSHARE_SINA,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=safe_int(fields[8]),
            amount=safe_float(fields[9]),
            open_price=safe_float(fields[1]),
            high=safe_float(fields[4]),
            low=safe_float(fields[5]),
            pre_close=pre_close,
        )
        breaker.record_success(source_key)
        logger.info("[实时行情-新浪] %s %s: 价格=%s, 涨跌=%s%% endpoint=%s", stock_code, quote.name, quote.price, quote.change_pct, SINA_REALTIME_ENDPOINT)
        return quote
    except Exception as e:
        detail = str(e)
        category = "remote_disconnect" if "Remote end closed connection" in detail else type(e).__name__
        breaker.record_failure(source_key, f"category={category} endpoint={SINA_REALTIME_ENDPOINT} detail={detail}")
        logger.info("[API错误] 新浪实时行情失败: category=%s endpoint=%s detail=%s", category, SINA_REALTIME_ENDPOINT, detail)
        return None


# ── Tencent ──────────────────────────────────────────────────────────────


def _get_stock_realtime_quote_tencent(stock_code: str) -> Optional[UnifiedRealtimeQuote]:
    """A 股实时行情（腾讯财经接口）。"""
    source_key = RealtimeSource.AKSHARE_TENCENT.value
    breaker = get_realtime_circuit_breaker()
    if not breaker.is_available(source_key):
        logger.info("[熔断跳过] 腾讯实时行情接口暂不可用 endpoint=%s", TENCENT_REALTIME_ENDPOINT)
        return None

    symbol = _to_sina_tx_symbol(stock_code)
    url = f"https://{TENCENT_REALTIME_ENDPOINT}"
    try:
        response = requests.get(
            url,
            params={"q": symbol},
            headers={"User-Agent": random.choice(USER_AGENTS)},
            timeout=10,
        )
        if response.status_code != 200:
            message = f"category=http_status endpoint={TENCENT_REALTIME_ENDPOINT} detail=HTTP {response.status_code}"
            breaker.record_failure(source_key, message)
            logger.info("[API错误] 腾讯实时行情接口失败: %s", message)
            return None

        response.encoding = "gbk"
        payload = response.text.split('"', 2)[1] if '"' in response.text else ""
        fields = payload.split("~")
        if len(fields) < 40:
            breaker.record_failure(source_key, f"category=malformed_payload endpoint={TENCENT_REALTIME_ENDPOINT}")
            return None

        # 腾讯 qt 接口 ~ 分隔字段顺序：[3]最新价 [4]昨收 [5]今开 [6]成交量
        # [31]涨跌额 [32]涨跌幅 [33]最高 [34]最低；[35] 为"价/量/额"复合串勿用
        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=fields[1],
            source=RealtimeSource.AKSHARE_TENCENT,
            price=safe_float(fields[3]),
            change_pct=safe_float(fields[32]),
            change_amount=safe_float(fields[31]),
            volume=safe_int(fields[6]),
            open_price=safe_float(fields[5]),
            pre_close=safe_float(fields[4]),
            high=safe_float(fields[33]),
            low=safe_float(fields[34]),
            turnover_rate=safe_float(fields[38]),
            pe_ratio=safe_float(fields[39]),
            pb_ratio=safe_float(fields[46] if len(fields) > 46 else None),
            total_mv=safe_float(fields[45] if len(fields) > 45 else None),
            circ_mv=safe_float(fields[44] if len(fields) > 44 else None),
        )
        breaker.record_success(source_key)
        logger.info("[实时行情-腾讯] %s %s: 价格=%s, 涨跌=%s%% endpoint=%s", stock_code, quote.name, quote.price, quote.change_pct, TENCENT_REALTIME_ENDPOINT)
        return quote
    except Exception as e:
        detail = str(e)
        category = "remote_disconnect" if "Remote end closed connection" in detail else type(e).__name__
        breaker.record_failure(source_key, f"category={category} endpoint={TENCENT_REALTIME_ENDPOINT} detail={detail}")
        logger.info("[API错误] 腾讯实时行情失败: category=%s endpoint=%s detail=%s", category, TENCENT_REALTIME_ENDPOINT, detail)
        return None


# ── Public entry point ───────────────────────────────────────────────────


def get_realtime_quote(stock_code: str, source: str = "em") -> Optional[UnifiedRealtimeQuote]:
    """获取 A 股实时行情数据，多源故障切换。

    Args:
        stock_code: 股票代码
        source: 数据源标识（保留参数，内部自行切换）

    Returns:
        UnifiedRealtimeQuote 或 None
    """
    normalized_code = normalize_stock_code(stock_code)

    quote = _get_stock_realtime_quote_em_push(normalized_code)
    if quote and quote.has_basic_data():
        return quote

    quote = _get_stock_realtime_quote_em(normalized_code)
    if quote and quote.has_basic_data():
        return quote

    quote = _get_stock_realtime_quote_xueqiu(normalized_code)
    if quote and quote.has_basic_data():
        return quote

    for fallback in (_get_stock_realtime_quote_sina, _get_stock_realtime_quote_tencent):
        quote = fallback(normalized_code)
        if quote and quote.has_basic_data():
            return quote
    return None