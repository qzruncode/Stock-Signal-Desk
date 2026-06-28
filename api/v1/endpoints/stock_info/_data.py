# -*- coding: utf-8 -*-
"""Business analysis data fetchers — akshare wrappers and peer/macro helpers."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    elif isinstance(obj, float) and (obj != obj or obj == float('inf') or obj == float('-inf')):
        return None
    return obj


def _fetch_business_intro(symbol: str) -> dict:
    import akshare as ak
    try:
        profile = ak.stock_profile_cninfo(symbol=symbol)
        if profile is not None and not profile.empty:
            row = profile.iloc[0].to_dict() if len(profile) == 1 else profile.iloc[-1].to_dict()
            return _sanitize(row)
    except Exception as exc:
        logger.warning("[StockBusiness] intro fetch failed for %s: %s", symbol, exc)
    return {}


def _fetch_business_composition(symbol: str) -> list[dict]:
    import akshare as ak
    try:
        df = ak.stock_business_analysis(symbol=symbol)
        if df is not None and not df.empty:
            return _sanitize(df.to_dict('records'))
    except Exception as exc:
        logger.warning("[StockBusiness] composition fetch failed for %s: %s", symbol, exc)
    return []


def _fetch_profit_forecast(symbol: str) -> list[dict]:
    import akshare as ak
    try:
        df = ak.stock_profit_forecast(symbol=symbol)
        if df is not None and not df.empty:
            return _sanitize(df.to_dict('records'))
    except Exception as exc:
        logger.warning("[StockBusiness] profit forecast failed for %s: %s", symbol, exc)
    return []


def _fetch_financial_summary(symbol: str) -> dict:
    import akshare as ak
    try:
        df = ak.stock_financial_abstract(symbol=symbol)
        if df is not None and not df.empty:
            return _sanitize(df.to_dict('records'))
    except Exception as exc:
        logger.warning("[StockBusiness] financial summary failed for %s: %s", symbol, exc)
    return {}


def _fetch_recent_events(symbol: str) -> dict:
    news = []
    announcements = []
    try:
        import akshare as ak
        try:
            df_news = ak.stock_news(symbol=symbol)
            if df_news is not None and not df_news.empty:
                news = _sanitize(df_news.to_dict('records'))
        except Exception:
            logger.warning("[StockBusiness] stock_news failed for symbol=%s", symbol, exc_info=True)
        try:
            df_ann = ak.stock_notice_report(symbol=symbol)
            if df_ann is not None and not df_ann.empty:
                announcements = _sanitize(df_ann.to_dict('records'))
        except Exception:
            logger.warning("[StockBusiness] stock_notice_report failed for symbol=%s", symbol, exc_info=True)
    except Exception as exc:
        logger.warning("[StockBusiness] news/announcements failed for %s: %s", symbol, exc)
    return {"news": news, "announcements": announcements}


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
        for key, indicator_name in [('pmi', 'PMI'), ('cpi', 'CPI'), ('ppi', 'PPI')]:
            try:
                fetcher = INDICATOR_FETCHERS.get(indicator_name)
                if not fetcher:
                    continue
                records = fetcher()
                if records:
                    result[key] = records[-3:]
            except Exception:
                logger.warning("[StockBusiness] _fetch_macro_data fetcher failed for indicator=%s", indicator_name, exc_info=True)
    except ImportError:
        pass
    return result


def _fetch_peer_data(industry: str, target_symbol: str, max_peers: int = 3) -> list[dict]:
    peers = []
    try:
        from src.repositories.stock_repository import get_stocks_by_industry
        all_stocks = get_stocks_by_industry(industry) or []
        for s in all_stocks:
            if str(s.get('code', '')) != target_symbol and s.get('status') == 'active':
                peers.append({
                    'code': s.get('code'),
                    'name': s.get('name'),
                    'market': s.get('market'),
                })
                if len(peers) >= max_peers:
                    break
    except Exception as exc:
        logger.warning("[StockBusiness] peer data fetch failed: %s", exc)
    return peers


def _get_stock_industry(symbol: str) -> str:
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            from src.storage import StockMeta
            from sqlalchemy import select
            meta = session.execute(
                select(StockMeta).where(StockMeta.code == symbol)
            ).scalars().first()
            if meta and meta.industry:
                return meta.industry
    except Exception:
        logger.warning("[StockBusiness] DB industry lookup failed for symbol=%s", symbol, exc_info=True)
    from src.data.stock_mapping import STOCK_SECTOR_MAP, STOCK_NAME_MAP
    name = STOCK_NAME_MAP.get(symbol, "")
    sector = STOCK_SECTOR_MAP.get(symbol, "")
    default_industry = sector or ""
    if not default_industry and name:
        if any(word in name for word in ['银行', '证券', '保险', '信托']):
            return "金融"
        if any(word in name for word in ['医药', '生物', '医疗']):
            return "医药生物"
        if any(word in name for word in ['科技', '信息', '软件', '电子']):
            return "信息技术"
        if any(word in name for word in ['地产', '房产']):
            return "房地产"
    return default_industry or "综合"