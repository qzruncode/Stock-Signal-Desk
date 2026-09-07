"""Business analysis data fetchers — akshare wrappers and peer/macro helpers."""

from __future__ import annotations
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, float) and (
        obj != obj or obj == float("inf") or obj == float("-inf")
    ):
        return None
    return obj


def _to_em_prefixed(symbol: str) -> str:
    """6 位代码 → 东财带市场前缀 symbol（SH/SZ/BJ），供 stock_zygc_em 等接口使用。"""
    code = (
        str(symbol or "")
        .strip()
        .upper()
        .lstrip("SH")
        .lstrip("SZ")
        .lstrip("BJ")
        .zfill(6)
    )
    if code.startswith(("6", "5", "9")):
        return f"SH{code}"
    if code.startswith(("8", "4")):
        return f"BJ{code}"
    return f"SZ{code}"


def _fetch_business_intro(symbol):
    from src.services.market_data_client import read_source

    return read_source("company.fetch_business_intro", {"symbol": symbol})["data"]


def _fetch_business_composition(symbol):
    from src.services.market_data_client import read_source

    return read_source("company.fetch_business_composition", {"symbol": symbol})["data"]


def _fetch_profit_forecast(symbol):
    from src.services.market_data_client import read_source

    return read_source("company.fetch_profit_forecast", {"symbol": symbol})["data"]


def _fetch_financial_summary(symbol):
    from src.services.market_data_client import read_source

    return read_source("company.fetch_financial_summary", {"symbol": symbol})["data"]


def _fetch_recent_events(symbol):
    from src.services.market_data_client import read_source

    return read_source("company.fetch_recent_events", {"symbol": symbol})["data"]


def _build_growth_text(financial_summary: dict) -> str:
    if isinstance(financial_summary, list):
        financial_summary = financial_summary[-1] if financial_summary else {}
    if not financial_summary or not isinstance(financial_summary, dict):
        return "暂无财务数据"
    growth = financial_summary.get("growth", "")
    return str(growth)[:200] if growth else "暂无增长数据"


def _fetch_macro_data() -> dict:
    result = {}
    try:
        from api.v1.endpoints.macro import INDICATOR_FETCHERS

        for key, indicator_name in [("pmi", "PMI"), ("cpi", "CPI"), ("ppi", "PPI")]:
            try:
                fetcher = INDICATOR_FETCHERS.get(indicator_name)
                if not fetcher:
                    continue
                records = fetcher()
                if records:
                    result[key] = records[-3:]
            except Exception:
                logger.warning(
                    "[StockBusiness] _fetch_macro_data fetcher failed for indicator=%s",
                    indicator_name,
                    exc_info=True,
                )
    except ImportError:
        pass
    return result


def _fetch_peer_data(
    industry: str, target_symbol: str, max_peers: int = 3
) -> list[dict]:
    peers = []
    try:
        from src.repositories.stock_repository import get_stocks_by_industry

        all_stocks = get_stocks_by_industry(industry) or []
        for s in all_stocks:
            if str(s.get("code", "")) != target_symbol and s.get("status") == "active":
                peers.append(
                    {
                        "code": s.get("code"),
                        "name": s.get("name"),
                        "market": s.get("market"),
                    }
                )
                if len(peers) >= max_peers:
                    break
    except Exception as exc:
        logger.warning("[StockBusiness] peer data fetch failed: %s", exc)
    return peers


def _get_stock_industry(symbol: str) -> str:
    from src.tools.get_stock_info import get_stock_info

    value = get_stock_info(symbol)
    return str(
        value.get("industry") or (value.get("data") or {}).get("industry") or "未知"
    )
