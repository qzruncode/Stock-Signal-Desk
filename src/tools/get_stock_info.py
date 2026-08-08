# -*- coding: utf-8 -*-
"""``get_stock_info`` — normalized company profile and share-capital snapshot."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

import httpx

from data_provider.utils import is_bse_code
from src.tools._akshare import bare_local_symbol, bare_symbol, cached_call, json_value
from src.tools._trading_calendar import is_trading_time
from src.tools.base import ToolSpec, object_schema

_EM_URL = "https://push2delay.eastmoney.com/api/qt/stock/get"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text in {"", "-", "--", "None", "nan"} else text


def _date_yyyymmdd(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _quote_time(value: Any) -> str | None:
    """Normalize Eastmoney's provider timestamp without using fetch time."""
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp //= 1_000
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _market(code: str) -> tuple[str, int]:
    if is_bse_code(code):
        return "bj", 0
    if code.startswith(("6", "9")):
        return "sh", 1
    return "sz", 0


def _fetch_cninfo(code: str) -> dict[str, Any]:
    import akshare as ak

    frame = ak.stock_profile_cninfo(symbol=code)
    if frame is None or frame.empty:
        raise RuntimeError("巨潮资讯没有返回公司概况")
    row = frame.iloc[0].to_dict()
    return {str(key): json_value(value) for key, value in row.items()}


def _fetch_eastmoney_capital(code: str) -> dict[str, Any]:
    market, market_code = _market(code)
    response = httpx.get(
        _EM_URL,
        params={
            "secid": f"{market_code}.{code}",
            "fields": "f43,f57,f58,f84,f85,f116,f117,f124,f127,f189",
            "fltt": 2,
            "invt": 2,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    if str(data.get("f57") or "").zfill(6) != code:
        raise RuntimeError("东方财富返回的股票代码与请求不一致")
    return {
        "symbol": code,
        "market_code": market,
        "short_name": _text(data.get("f58")),
        "industry_eastmoney": _text(data.get("f127")),
        "listing_date_eastmoney": _date_yyyymmdd(data.get("f189")),
        "latest_price": _number(data.get("f43")),
        "quote_time": _quote_time(data.get("f124")),
        "total_shares": _number(data.get("f84")),
        "circulating_shares": _number(data.get("f85")),
        "total_market_cap": _number(data.get("f116")),
        "circulating_market_cap": _number(data.get("f117")),
    }


def _website(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return text if text.startswith(("http://", "https://")) else f"https://{text}"


def _normalized_profile(code: str, profile: dict[str, Any], capital: dict[str, Any]) -> dict[str, Any]:
    registered_capital = _number(profile.get("注册资金"))
    return {
        "symbol": code,
        "company_name": _text(profile.get("公司名称")),
        "company_name_en": _text(profile.get("英文名称")),
        "short_name": _text(profile.get("A股简称")) or capital.get("short_name"),
        "former_names": _text(profile.get("曾用简称")),
        "a_share_code": _text(profile.get("A股代码")) or code,
        "b_share_code": _text(profile.get("B股代码")),
        "h_share_code": _text(profile.get("H股代码")),
        "included_indices": [item.strip() for item in str(profile.get("入选指数") or "").split(",") if item.strip()],
        "market": _text(profile.get("所属市场")),
        "market_code": capital.get("market_code") or _market(code)[0],
        "industry": _text(profile.get("所属行业")),
        "industry_eastmoney": capital.get("industry_eastmoney"),
        "legal_representative": _text(profile.get("法人代表")),
        "registered_capital": registered_capital,
        "registered_capital_unit": "万元人民币" if registered_capital is not None else None,
        "established_date": _text(profile.get("成立日期")),
        "listing_date": _text(profile.get("上市日期")) or capital.get("listing_date_eastmoney"),
        "official_website": _website(profile.get("官方网站")),
        "email": _text(profile.get("电子邮箱")),
        "phone": _text(profile.get("联系电话")),
        "fax": _text(profile.get("传真")),
        "registered_address": _text(profile.get("注册地址")),
        "office_address": _text(profile.get("办公地址")),
        "postal_code": _text(profile.get("邮政编码")),
        "main_business": _text(profile.get("主营业务")),
        "business_scope": _text(profile.get("经营范围")),
        "company_profile": _text(profile.get("机构简介")),
        "capital_snapshot": (
            {
                "latest_price": capital.get("latest_price"),
                "total_shares": capital.get("total_shares"),
                "circulating_shares": capital.get("circulating_shares"),
                "total_market_cap": capital.get("total_market_cap"),
                "circulating_market_cap": capital.get("circulating_market_cap"),
                "price_unit": "人民币元",
                "share_unit": "股",
                "market_cap_unit": "元",
            }
            if capital
            else None
        ),
    }


def get_stock_info(symbol: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Legacy bundled company view for non-Agent callers.

    The Agent registry exports the two source-specific reads below instead of
    this convenience merger.
    """
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    now = datetime.now().astimezone()
    errors: list[str] = []
    profile: dict[str, Any] = {}
    capital: dict[str, Any] = {}
    profile_cached = capital_cached = False

    try:
        if use_cache:
            profile, profile_cached = cached_call(
                f"stock_info:cninfo:v3:{code}",
                lambda: _fetch_cninfo(code),
                ttl_seconds=24 * 60 * 60,
                attempts=2,
            )
        else:
            profile = _fetch_cninfo(code)
    except Exception as exc:
        errors.append(f"巨潮资讯公司概况失败: {exc}")

    try:
        ttl = 120 if is_trading_time(now) else 30 * 60
        if use_cache:
            capital, capital_cached = cached_call(
                f"stock_info:eastmoney:v3:{code}",
                lambda: _fetch_eastmoney_capital(code),
                ttl_seconds=ttl,
                attempts=2,
            )
        else:
            capital = _fetch_eastmoney_capital(code)
    except Exception as exc:
        errors.append(f"东方财富股本快照失败: {exc}")

    data = _normalized_profile(code, profile, capital)
    profile_available = bool(profile)
    capital_available = bool(capital)
    success = profile_available or capital_available
    data.update(
        {
            "profile_available": profile_available,
            "capital_snapshot_available": capital_available,
            "sources": [
                source
                for source, available in (("巨潮资讯/AKShare", profile_available), ("东方财富", capital_available))
                if available
            ],
            "source": " + ".join(
                [
                    source
                    for source, available in (("巨潮资讯/AKShare", profile_available), ("东方财富", capital_available))
                    if available
                ]
            )
            or "none",
            "success": success,
            "errors": errors,
            "warnings": ["公司概况不完整"] if success and not profile_available else [],
            "data_time": now.isoformat() if success else None,
            "data_time_inferred": success,
            "is_stale": False if success else None,
            "freshness_unknown": not success,
            "fallback_used": not profile_available and capital_available,
            "_cached": profile_cached and (capital_cached if capital_available else True),
            "cache_detail": {"profile": profile_cached, "capital_snapshot": capital_cached},
            "_fetched_at": now.isoformat(),
        }
    )
    return data


def read_company_profile_cninfo(symbol: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Read exactly one company-profile source (CNInfo via AKShare)."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    profile, cached = (
        cached_call(
            f"company-profile:cninfo:v1:{code}",
            lambda: _fetch_cninfo(code),
            ttl_seconds=24 * 60 * 60,
            attempts=2,
        )
        if use_cache
        else (_fetch_cninfo(code), False)
    )
    data = _normalized_profile(code, profile, {})
    data.pop("capital_snapshot", None)
    data.pop("industry_eastmoney", None)
    data.update(
        {
            "source": "巨潮资讯/AKShare公司概况",
            "source_scope": "company_profile",
            "success": True,
            "errors": [],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
            "freshness_unknown": True,
            "_cached": cached,
            "_fetched_at": datetime.now().astimezone().isoformat(),
        }
    )
    return data


def read_stock_capital_snapshot_eastmoney(
    symbol: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Read exactly one Eastmoney quote/capital snapshot."""
    code = bare_local_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("symbol 必须能解析为 6 位股票代码")
    now = datetime.now().astimezone()
    ttl = 120 if is_trading_time(now) else 30 * 60
    snapshot, cached = (
        cached_call(
            f"stock-capital:eastmoney:v1:{code}",
            lambda: _fetch_eastmoney_capital(code),
            ttl_seconds=ttl,
            attempts=2,
        )
        if use_cache
        else (_fetch_eastmoney_capital(code), False)
    )
    # `quote_time` is the only provider-originated timestamp for this
    # snapshot.  `_fetched_at` records when our service made the request and
    # must never be presented as a market-data timestamp.
    data_time = snapshot.get("quote_time")
    return {
        "symbol": code,
        "short_name": snapshot.get("short_name"),
        "market_code": snapshot.get("market_code"),
        "industry_eastmoney": snapshot.get("industry_eastmoney"),
        "listing_date_eastmoney": snapshot.get("listing_date_eastmoney"),
        "latest_price": snapshot.get("latest_price"),
        "total_shares": snapshot.get("total_shares"),
        "circulating_shares": snapshot.get("circulating_shares"),
        "total_market_cap": snapshot.get("total_market_cap"),
        "circulating_market_cap": snapshot.get("circulating_market_cap"),
        "price_unit": "人民币元",
        "share_unit": "股",
        "market_cap_unit": "元",
        "source": "东方财富证券快照",
        "source_scope": "stock_capital_snapshot",
        "success": True,
        "errors": [],
        "warnings": [],
        "data_time": data_time,
        "data_time_provenance": "source" if data_time else "unavailable",
        "data_time_note": (
            None
            if data_time
            else "东方财富快照未返回 quote_time；_fetched_at 仅表示本服务获取时间。"
        ),
        "is_stale": None,
        "freshness_unknown": data_time is None,
        "_cached": cached,
        "_fetched_at": now.isoformat(),
    }


TOOLS = (
    ToolSpec(
        name="read_company_profile_cninfo",
        description=(
            "从巨潮资讯读取一只 A 股的公司基础档案：公司名称、行业、成立和上市日期、主营业务、"
            "联系方式和注册地址；不读取实时价格、市值或股本。"
        ),
        parameters=object_schema(
            {"symbol": {"type": "string", "description": "A股股票代码或可解析的股票名称"}},
            ["symbol"],
        ),
        executor=read_company_profile_cninfo,
        category="data",
    ),
    ToolSpec(
        name="read_stock_capital_snapshot_eastmoney",
        description=(
            "从东方财富读取一只 A 股的当前价格、总/流通股本和总/流通市值快照；"
            "不读取公司档案或估值历史。"
        ),
        parameters=object_schema(
            {"symbol": {"type": "string", "description": "A股股票代码或可解析的股票名称"}},
            ["symbol"],
        ),
        executor=read_stock_capital_snapshot_eastmoney,
        category="data",
    ),
)


__all__ = [
    "TOOLS",
    "get_stock_info",
    "read_company_profile_cninfo",
    "read_stock_capital_snapshot_eastmoney",
]
