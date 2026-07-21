# src/services/buy_criteria/evaluators/growth_space.py
"""Evaluator ③: 未来3年空间 — Is there clear 3-year growth space (not zero-sum)?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import GROWTH_SPACE

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("%", "")
        if not value or value.lower() in {"none", "nan", "-", "false"}:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: Any) -> str:
    num = _safe_float(value)
    return "缺失" if num is None else f"{num:.2f}%"


def _compact_financial_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_date": item.get("report_date"),
        "revenue_yoy": item.get("revenue_yoy"),
        "revenue_qoq": item.get("revenue_qoq"),
        "net_profit_yoy": item.get("net_profit_yoy") or item.get("parent_net_profit_yoy"),
        "gross_margin": item.get("gross_margin"),
    }


def _compact_research_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "publish_date": item.get("publish_date") or item.get("date"),
        "org": item.get("org") or item.get("source"),
        "rating": item.get("rating"),
        "industry": item.get("industry"),
        "title": item.get("title"),
        "summary": item.get("summary"),
        "profit_forecasts": _list_of_dicts(item.get("profit_forecasts"))[:4],
    }


def _compact_news_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "publish_time": item.get("publish_time") or item.get("date"),
        "source": item.get("source"),
        "event_label": item.get("event_label"),
        "title": item.get("title"),
        "summary": item.get("summary"),
    }


def _format_forecasts(forecasts: list[dict[str, Any]]) -> str:
    chunks = []
    for forecast in forecasts[:4]:
        parts = [str(forecast.get("year") or "未知年份")]
        if forecast.get("eps") is not None:
            parts.append(f"EPS {forecast.get('eps')}")
        if forecast.get("pe") is not None:
            parts.append(f"PE {forecast.get('pe')}")
        if len(parts) > 1:
            chunks.append(" ".join(parts))
    return "；".join(chunks) if chunks else "缺失"


def _extract_forecast_signals(research_items: list[dict]) -> str:
    """从研报中提取分析师对行业增速/空间的看法"""
    signals = []
    for item in research_items[:6]:
        forecasts = item.get("profit_forecasts") or []
        if forecasts:
            for f in forecasts[:2]:
                if f.get("revenue_growth") or f.get("industry_growth"):
                    signals.append(
                        f"{item.get('org')} ({item.get('publish_date')}): "
                        f"{f.get('revenue_growth') or f.get('industry_growth')}"
                    )
    return "；".join(signals) if signals else "研报中无明确增速预测"


def _format_forecasts_summary(research_items: list[dict]) -> str:
    """汇总研报中的盈利预测信息"""
    parts = []
    for item in research_items[:6]:
        forecasts = item.get("profit_forecasts") or []
        if forecasts:
            org = item.get("org") or "未知机构"
            dates = _format_forecasts(forecasts)
            parts.append(f"{org}：{dates}")
    return "；".join(parts) if parts else "缺失"


class GrowthSpaceEvaluator(BaseCriterionEvaluator):
    criterion_id = "growth_space"
    criterion_name = "未来3年空间"
    index = 2

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        raw["stock_profile"] = {
            "symbol": stock_info.get("symbol") or symbol,
            "name": stock_info.get("name") or stock_info.get("short_name"),
            "industry": stock_info.get("industry", ""),
            "main_business": stock_info.get("main_business", ""),
            "product_type": stock_info.get("product_type"),
            "product_name": stock_info.get("product_name"),
        }

        try:
            financials = ds.get_financials(symbol, periods=4, force=True)
            raw["financials"] = {
                "items": [_compact_financial_item(item) for item in _list_of_dicts(financials.get("items"))[:4]],
                "_cached": financials.get("_cached"),
            }
        except Exception as exc:
            logger.warning("[growth_space] financials failed: %s", exc)
            raw["financials_error"] = str(exc)

        try:
            research = ds.get_research_report(symbol, days=1095)
            items = [_compact_research_item(item) for item in _list_of_dicts(research.get("items"))[:10]]
            raw["research"] = {
                "items": items,
                "count": len(research.get("items") or []),
                "positive_count": sum(1 for item in items if str(item.get("rating") or "").strip() in {"买入", "增持", "推荐", "优于大市", "强烈推荐"}),
                "data_time": research.get("data_time"),
                "is_stale": research.get("is_stale"),
            }
        except Exception as exc:
            logger.warning("[growth_space] research failed: %s", exc)
            raw["research_error"] = str(exc)

        try:
            news = ds.search_news(symbol, days=180)
            raw["news"] = {
                "items": [_compact_news_item(item) for item in _list_of_dicts(news.get("items"))[:8]],
                "count": len(news.get("items") or []),
                "data_time": news.get("data_time"),
                "is_stale": news.get("is_stale"),
            }
        except Exception as exc:
            logger.warning("[growth_space] news failed: %s", exc)
            raw["news_error"] = str(exc)

        # Build summary
        profile = raw["stock_profile"]
        financial_items = _list_of_dicts(_as_dict(raw.get("financials")).get("items"))
        research = _as_dict(raw.get("research"))
        research_items = _list_of_dicts(research.get("items"))
        news = _as_dict(raw.get("news"))
        news_items = _list_of_dicts(news.get("items"))
        # Build dynamic missing list: only include genuinely unavailable fields.
        # These three are industry-level metrics not available through free data sources.
        # The rubric already provides alternative evaluation paths for when they're absent.
        missing: list[str] = []

        lines = [
            "## 公司与行业",
            f"- 股票：{profile.get('name') or symbol} ({profile.get('symbol') or symbol})",
            f"- 所属行业：{profile.get('industry') or '缺失'}",
            f"- 主营业务：{profile.get('main_business') or '缺失'}",
            f"- 产品：{profile.get('product_type') or '缺失'} / {profile.get('product_name') or '缺失'}",
            "",
            "## 公司财务增长",
        ]
        if financial_items:
            for item in financial_items:
                lines.append(
                    "- "
                    f"{item.get('report_date') or '未知报告期'}："
                    f"营收同比 {_fmt_pct(item.get('revenue_yoy'))}，"
                    f"营收环比 {_fmt_pct(item.get('revenue_qoq'))}，"
                    f"净利润同比 {_fmt_pct(item.get('net_profit_yoy'))}，"
                    f"毛利率 {_fmt_pct(item.get('gross_margin'))}"
                )
        else:
            lines.append("- 缺失")
        lines.extend([
            "",
            "## 券商研报与盈利预测",
            f"- 研报数量：{research.get('count', 0)}；正向评级：{research.get('positive_count', 0)}；数据日期：{research.get('data_time') or '缺失'}；是否过期：{research.get('is_stale')}",
            f"- 盈利预测汇总：{_format_forecasts_summary(research_items)}",
            f"- 增速预测线索：{_extract_forecast_signals(research_items)}",
        ])
        if research_items:
            for item in research_items[:6]:
                lines.append(
                    "- "
                    f"{item.get('publish_date') or '未知日期'} {item.get('org') or '未知机构'} "
                    f"{item.get('rating') or '未评级'}：{item.get('title') or '无标题'}；"
                    f"盈利预测：{_format_forecasts(_list_of_dicts(item.get('profit_forecasts')))}"
                )
                summary = str(item.get("summary") or "").strip()
                if summary:
                    lines.append(f"  摘要：{summary[:180]}")
        else:
            lines.append("- 缺失")
        lines.extend([
            "",
            "## 相关新闻新增需求线索",
            f"- 新闻数量：{news.get('count', 0)}；数据日期：{news.get('data_time') or '缺失'}；是否过期：{news.get('is_stale')}",
        ])
        if news_items:
            for item in news_items[:6]:
                lines.append(
                    "- "
                    f"{item.get('publish_time') or '未知时间'} {item.get('source') or '未知来源'} "
                    f"[{item.get('event_label') or '未分类'}] {item.get('title') or '无标题'}："
                    f"{str(item.get('summary') or '')[:180]}"
                )
        else:
            lines.append("- 缺失")
        lines.extend([
            "",
            "## 判断约束",
            "- 只能使用上方证据判断未来3年空间，不允许写'基于行业认知'或自行补充外部行业常识。",
            "- 数据源中无行业CAGR、渗透率、市场规模硬数值时，不得编造；应改用上方研报EPS预测、公司财务增长和新闻线索判断新增需求。",
            "- 不得引用上方证据中没有出现的产品规格、市场规模、渗透率或CAGR。",
            "- 如果通过，应基于'研报EPS预测/公司财务增长/明确需求线索'说明，不能写成无来源的行业CAGR结论。",
        ])

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return GROWTH_SPACE

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        raw = evidence.raw_data
        financial_items = ((raw.get("financials") or {}).get("items") or [])
        research_items = ((raw.get("research") or {}).get("items") or [])
        news_items = ((raw.get("news") or {}).get("items") or [])
        if not financial_items and not research_items and not news_items:
            return "财务、研报和需求新闻均无可用证据，无法验证未来三年增长空间"
        return None
