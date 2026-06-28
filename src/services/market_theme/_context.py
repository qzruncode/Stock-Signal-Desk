# -*- coding: utf-8 -*-
"""Context collection and evidence-pack building from external market/feed sources."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from src.ai_caller import current_shanghai_timestamp
from src.storage import DatabaseManager

from ._utils import shorten, strip_html

logger = logging.getLogger(__name__)


def collect_context(*, force: bool, include_rss: bool = True) -> dict[str, Any]:
    from api.v1.endpoints.macro import get_market_breadth, get_sector_flow
    from api.v1.endpoints.market_status import get_market_status
    from api.v1.endpoints.sectors import get_sector_list

    market_status = get_market_status(force=force)
    breadth = get_market_breadth()
    industry_sectors = get_sector_list(type="industry", force=force)
    concept_sectors = get_sector_list(type="concept", force=force)
    industry_flow = get_sector_flow(type="industry", top_n=8)
    concept_flow = get_sector_flow(type="concept", top_n=8)

    rss_context: dict[str, Any] = {}
    if include_rss:
        from api.v1.endpoints.rss import get_rss_feeds

        rss_sources = [
            ("policy_calendar", {"source": "wallstreetcn_calendar", "limit": 8, "force": force}),
            ("market_news", {"source": "cls", "category": "telegraph", "limit": 8, "force": force}),
            ("strategy_reports", {"source": "eastmoney_report", "category": "strategyreport", "limit": 6, "force": force}),
            ("macro_reports", {"source": "eastmoney_report", "category": "macresearch", "limit": 6, "force": force}),
            ("industry_reports", {"source": "eastmoney_report", "category": "industry", "limit": 6, "force": force}),
            ("exchange_inquire", {"source": "sse_inquire", "limit": 6, "force": force}),
            ("exchange_disclosure", {"source": "sse_disclosure", "limit": 6, "force": force}),
            ("money_center", {"source": "chinamoney", "limit": 6, "force": force}),
        ]
        for key, params in rss_sources:
            try:
                rss_context[key] = get_rss_feeds(**params)
            except Exception as exc:
                logger.warning("market theme rss source failed: %s", key, exc_info=True)
                rss_context[key] = {
                    "source": params["source"],
                    "items": [],
                    "errors": [str(exc)],
                    "_fetched_at": datetime.now().isoformat(),
                    "_cached": False,
                }

    source_catalog = _build_source_catalog(rss_context)
    return {
        "generated_at": current_shanghai_timestamp(),
        "source_snapshot": {
            "market_status": market_status,
            "market_breadth": breadth,
            "industry_sectors": (industry_sectors.get("items") or [])[:12],
            "concept_sectors": (concept_sectors.get("items") or [])[:12],
            "industry_flow": {
                "inflow_top": (industry_flow.get("inflow_top") or [])[:8],
                "outflow_top": (industry_flow.get("outflow_top") or [])[:8],
            },
            "concept_flow": {
                "inflow_top": (concept_flow.get("inflow_top") or [])[:8],
                "outflow_top": (concept_flow.get("outflow_top") or [])[:8],
            },
            "rss": rss_context,
            "source_catalog": source_catalog,
        },
    }


def get_latest_report(report_key: str) -> Optional[dict[str, Any]]:
    try:
        current_as_of_date = _current_report_as_of_date()
        return DatabaseManager.get_instance().get_latest_market_mainline_report(
            report_key=report_key,
            mode="llm",
            as_of_date=current_as_of_date,
        )
    except Exception:
        logger.exception("读取最新市场主线模型报告失败")
        return None


def build_report_evidence_pack(context: dict[str, Any]) -> dict[str, Any]:
    snapshot = context["source_snapshot"]
    rss = snapshot.get("rss") or {}
    return {
        "generated_at": context["generated_at"],
        "as_of_date": snapshot.get("market_status", {}).get("data_time") or datetime.now().date().isoformat(),
        "market_status": snapshot.get("market_status") or {},
        "market_breadth": snapshot.get("market_breadth") or {},
        "industry_inflow_top": (snapshot.get("industry_flow") or {}).get("inflow_top") or [],
        "industry_outflow_top": (snapshot.get("industry_flow") or {}).get("outflow_top") or [],
        "concept_inflow_top": (snapshot.get("concept_flow") or {}).get("inflow_top") or [],
        "concept_outflow_top": (snapshot.get("concept_flow") or {}).get("outflow_top") or [],
        "industry_sectors": snapshot.get("industry_sectors") or [],
        "concept_sectors": snapshot.get("concept_sectors") or [],
        "policy_headlines": _summarize_feed_items(rss.get("policy_calendar")),
        "market_news": _summarize_feed_items(rss.get("market_news")),
        "strategy_reports": _summarize_feed_items(rss.get("strategy_reports")),
        "macro_reports": _summarize_feed_items(rss.get("macro_reports")),
        "industry_reports": _summarize_feed_items(rss.get("industry_reports")),
        "exchange_disclosure": _summarize_feed_items(rss.get("exchange_disclosure")),
        "exchange_inquire": _summarize_feed_items(rss.get("exchange_inquire")),
        "money_center": _summarize_feed_items(rss.get("money_center")),
        "source_summary": _summarize_sources(snapshot),
    }


def summarize_feed_items(feed: Optional[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
    items = (feed or {}).get("items") or []
    results: list[dict[str, str]] = []
    for item in items[:limit]:
        results.append({
            "title": str(item.get("title") or "").strip(),
            "summary": shorten(strip_html(str(item.get("summary") or "")), 180),
            "published": str(item.get("published") or ""),
        })
    return results


def build_minimal_fallback() -> dict[str, Any]:
    return {
        "generated_at": current_shanghai_timestamp(),
        "headline": "市场主线研判暂时降级为基础模式，请稍后重试。",
        "market_regime": "服务降级",
        "primary_judgement": "本次请求未能完成完整的多源公开数据聚合，已返回最小可用结果以避免页面整体不可用。",
        "investment_takeaway": "建议稍后刷新，或先关注已验证的主流板块和官方/交易所新增信息。",
        "policy_watchlist": [
            "检查服务运行状态是否正常",
            "稍后重试主线研判接口",
            "优先参考官方/交易所最新披露信息",
        ],
        "current_themes": [],
        "next_themes": [],
        "source_notes": ["当前为降级结果，未完成本轮完整聚合"],
        "source_summary": {"official_count": 0, "news_count": 0, "report_count": 0, "source_catalog": []},
        "source_snapshot": {},
        "_cached": False,
        "degraded_reason": "isolated_runner_failed",
    }


def build_minimal_model_report() -> dict[str, Any]:
    return {
        "generated_at": current_shanghai_timestamp(),
        "as_of_date": datetime.now().date().isoformat(),
        "overview": "市场主线模型研判暂不可用，请稍后重试。",
        "full_report": "本次未能完成模型直出研判，当前仅保留最小可用结果。",
        "market_stage": {
            "label": "服务降级",
            "description": "模型报告暂时不可用。",
        },
        "current_mainlines": [],
        "future_mainlines": [],
        "action_summary": ["稍后重试模型研判接口。"],
        "evidence_digest": {"policy": [], "industry": [], "market": []},
        "source_summary": {"official_count": 0, "news_count": 0, "report_count": 0, "source_catalog": []},
        "llm_used": False,
        "model_used": None,
    }


def build_minimal_evidence_fallback() -> dict[str, Any]:
    return {
        "generated_at": current_shanghai_timestamp(),
        "market_stage": {"label": "服务降级", "description": "证据层暂时不可用，请稍后刷新。"},
        "current_themes": [],
        "next_themes": [],
        "policy_watchlist": [],
        "source_notes": ["当前为降级结果，未完成证据聚合"],
        "source_summary": {"official_count": 0, "news_count": 0, "report_count": 0, "source_catalog": []},
        "source_snapshot": {},
        "_cached": False,
        "degraded_reason": "isolated_runner_failed",
    }


def _current_report_as_of_date() -> str:
    try:
        from api.v1.endpoints.market_status import get_market_status

        market_status = get_market_status(force=False)
        data_time = (market_status or {}).get("data_time")
        if data_time:
            return str(data_time)[:10]
    except Exception:
        logger.exception("读取市场主线当前分析日期失败")
    return datetime.now().date().isoformat()


def _build_source_catalog(rss_context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": "同花顺行业板块汇总", "category": "公开市场数据", "credibility": "高", "used": True},
        {"name": "东方财富板块异动/资金流", "category": "公开市场数据", "credibility": "中高", "used": True},
        {"name": "上交所问询与披露", "category": "交易所", "credibility": "高", "used": bool((rss_context.get("exchange_inquire") or {}).get("items") or (rss_context.get("exchange_disclosure") or {}).get("items"))},
        {"name": "中国外汇交易中心公开信息", "category": "官方公开信息", "credibility": "高", "used": bool((rss_context.get("money_center") or {}).get("items"))},
        {"name": "财联社电报 / 华尔街见闻日历", "category": "公共资讯", "credibility": "中高", "used": bool((rss_context.get("market_news") or {}).get("items") or (rss_context.get("policy_calendar") or {}).get("items"))},
        {"name": "东方财富策略/宏观/行业研报", "category": "卖方公开研报", "credibility": "中", "used": bool((rss_context.get("strategy_reports") or {}).get("items") or (rss_context.get("macro_reports") or {}).get("items") or (rss_context.get("industry_reports") or {}).get("items"))},
    ]


def _summarize_sources(snapshot: dict[str, Any]) -> dict[str, Any]:
    rss = snapshot.get("rss") or {}
    return {
        "official_count": sum(1 for key in ("exchange_inquire", "exchange_disclosure", "money_center") if (rss.get(key) or {}).get("items")),
        "news_count": sum(len((rss.get(key) or {}).get("items") or []) for key in ("market_news", "policy_calendar")),
        "report_count": sum(len((rss.get(key) or {}).get("items") or []) for key in ("strategy_reports", "macro_reports", "industry_reports")),
        "source_catalog": snapshot.get("source_catalog") or [],
    }