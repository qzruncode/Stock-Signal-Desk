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
        # akshare 常返回 datetime.date（如报告日期），JSON 无法直接序列化。
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


def _fetch_business_intro(symbol: str) -> dict:
    import akshare as ak

    try:
        profile = ak.stock_profile_cninfo(symbol=symbol)
        if profile is not None and not profile.empty:
            row = (
                profile.iloc[0].to_dict()
                if len(profile) == 1
                else profile.iloc[-1].to_dict()
            )
            return _sanitize(row)
    except Exception as exc:
        logger.warning("[StockBusiness] intro fetch failed for %s: %s", symbol, exc)
    return {}


def _fetch_business_composition(symbol: str) -> list[dict]:
    """主营业务构成（东方财富 stock_zygc_em，需带市场前缀 symbol）。"""
    import akshare as ak

    try:
        df = ak.stock_zygc_em(symbol=_to_em_prefixed(symbol))
        if df is not None and not df.empty:
            return _sanitize(df.to_dict("records"))
    except Exception as exc:
        logger.warning(
            "[StockBusiness] composition fetch failed for %s: %s", symbol, exc
        )
    return []


def _fetch_profit_forecast(symbol: str) -> list[dict]:
    """盈利预测（东方财富 stock_research_report_em，单股研报含盈利预测）。

    早期实现误用 stock_profit_forecast_em(symbol="")：该接口 symbol 是行业板块名而非
    股票代码，传 "" 会拉取全市场 3000+ 行再本地过滤，既慢又浪费。这里改用单股研报接口，
    其返回已含 2026/2027/2028 盈利预测每股收益。

    输出字段对齐 _build_business_prompt 的读取键（analyst/researcher/eps_2026 等）。
    """
    import akshare as ak

    try:
        code = str(symbol or "").strip().zfill(6)
        df = ak.stock_research_report_em(symbol=code)
        if df is None or df.empty:
            return []
        items: list[dict] = []
        for _, row in df.iterrows():
            items.append(
                {
                    "analyst": str(row.get("机构", "") or ""),
                    "researcher": str(row.get("东财评级", "") or ""),
                    "rating": str(row.get("东财评级", "") or ""),
                    "eps_2026": _sanitize(row.get("2026-盈利预测-收益")),
                    "eps_2027": _sanitize(row.get("2027-盈利预测-收益")),
                    "eps_2028": _sanitize(row.get("2028-盈利预测-收益")),
                    "title": str(row.get("报告名称", "") or ""),
                    "date": str(row.get("日期", "") or ""),
                }
            )
        return items
    except Exception as exc:
        logger.warning("[StockBusiness] profit forecast failed for %s: %s", symbol, exc)
    return []


def _fetch_financial_summary(symbol: str) -> dict:
    import akshare as ak

    try:
        df = ak.stock_financial_abstract(symbol=symbol)
        if df is not None and not df.empty:
            return _sanitize(df.to_dict("records"))
    except Exception as exc:
        logger.warning(
            "[StockBusiness] financial summary failed for %s: %s", symbol, exc
        )
    return {}


def _fetch_recent_events(symbol: str) -> dict:
    """近期新闻 + 公司公告。

    输出字段对齐 _build_business_prompt / _build_catalyst_prompt 的读取键：
    - news:     {time, source, title, content, url}
    - announcements: {date, type, title, url}

    早期实现误用 stock_notice_report(symbol=股票代码)：该接口 symbol 是公告类型枚举
    （"全部"/"财务报告"/...），传股票代码会得到空数据。改用单股公告接口
    stock_individual_notice_report(security=代码, ...)。
    """
    news: list[dict] = []
    announcements: list[dict] = []
    code = str(symbol or "").strip().zfill(6)
    try:
        import akshare as ak

        try:
            df_news = ak.stock_news_em(symbol=code)
            if df_news is not None and not df_news.empty:
                for _, row in df_news.iterrows():
                    news.append(
                        {
                            "time": str(row.get("发布时间", "") or ""),
                            "source": str(row.get("文章来源", "") or ""),
                            "title": str(row.get("新闻标题", "") or ""),
                            "content": str(row.get("新闻内容", "") or ""),
                            "url": str(row.get("新闻链接", "") or ""),
                        }
                    )
        except Exception:
            logger.warning(
                "[StockBusiness] stock_news failed for symbol=%s", symbol, exc_info=True
            )
        try:
            from datetime import timedelta

            end_date = datetime.now().strftime("%Y-%m-%d")
            begin_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            df_ann = ak.stock_individual_notice_report(
                security=code,
                symbol="全部",
                begin_date=begin_date,
                end_date=end_date,
            )
            if df_ann is not None and not df_ann.empty:
                for _, row in df_ann.iterrows():
                    announcements.append(
                        {
                            "date": str(row.get("公告日期", "") or ""),
                            "type": str(row.get("公告类型", "") or ""),
                            "title": str(row.get("公告标题", "") or ""),
                            "url": str(row.get("网址", "") or ""),
                        }
                    )
        except Exception:
            logger.warning(
                "[StockBusiness] stock_individual_notice_report failed for symbol=%s",
                symbol,
                exc_info=True,
            )
    except Exception as exc:
        logger.warning(
            "[StockBusiness] news/announcements failed for %s: %s", symbol, exc
        )
    return {"news": news, "announcements": announcements}
