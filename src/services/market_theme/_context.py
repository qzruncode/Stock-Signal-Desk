# -*- coding: utf-8 -*-
"""Context collection and evidence-pack building from external market/feed sources."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Optional

from src.ai_caller import current_shanghai_timestamp
from src.storage import DatabaseManager

from ._utils import shorten, strip_html

logger = logging.getLogger(__name__)

MARKET_MAINLINE_REPORT_CONTRACT = "market_mainline_lifecycle_strategy_v3"


def collect_context(*, force: bool, include_rss: bool = True) -> dict[str, Any]:
    from api.v1.endpoints.market_status import get_market_status
    from api.v1.endpoints.sectors import get_sector_list

    try:
        market_status = get_market_status(force=force)
    except Exception as exc:
        logger.warning("market theme market status failed", exc_info=True)
        market_status = {
            "data_time": datetime.now().date().isoformat(),
            "success": False,
            "errors": [str(exc)],
        }

    try:
        from src.tools.get_market_regime import get_market_regime

        market_regime = get_market_regime(index="沪深300", history_points=30)
    except Exception as exc:
        logger.warning("market theme market regime failed", exc_info=True)
        market_regime = {
            "success": False,
            "datasets": {},
            "errors": [str(exc)],
            "data_time": None,
        }

    def _board_catalog(sector_type: str) -> dict[str, Any]:
        try:
            payload = get_sector_list(type=sector_type, force=force)
        except Exception as exc:
            logger.warning(
                "market theme %s board catalog failed",
                sector_type,
                exc_info=True,
            )
            return {
                "items": [],
                "data_time": None,
                "errors": [str(exc)],
            }
        return {
            "items": [
                {
                    "name": item.get("name"),
                    "code": item.get("code"),
                    "data_source": item.get("data_source"),
                }
                for item in payload.get("items") or []
                if isinstance(item, dict) and item.get("name")
            ],
            "data_time": payload.get("data_time"),
            "errors": payload.get("errors") or [],
        }

    # Board catalogs are taxonomy only.  Prices, rankings and capital flows
    # must never become evidence that a direction is or is not a market
    # mainline.
    industry_catalog = _board_catalog("industry")
    concept_catalog = _board_catalog("concept")

    rss_context: dict[str, Any] = {}
    if include_rss:
        from api.v1.endpoints._rss_reader import read_feed

        # Use the same audited FeedSpec routes as the Agent's finance reader.
        # The legacy semantic ``get_rss_feeds(source=...)`` adapter no longer
        # exists, so keeping that call here made the whole mainline pipeline
        # fail before any evidence could be returned.
        rss_sources = [
            ("policy_calendar", "/wallstreetcn/live/:category?/:score?", {}, 8),
            ("market_news", "/cls/telegraph/:category?", {}, 8),
            ("strategy_reports", "/eastmoney/report/:category", {"category": "strategyreport"}, 6),
            ("macro_reports", "/eastmoney/report/:category", {"category": "macresearch"}, 6),
            ("industry_reports", "/eastmoney/report/:category", {"category": "industry"}, 6),
            ("exchange_inquire", "/sse/inquire", {}, 6),
            ("exchange_disclosure", "/sse/disclosure/:query?", {}, 6),
            ("money_center", "/gov/pbc/tradeAnnouncement", {}, 6),
        ]

        def _fetch_feed(spec: tuple[str, str, dict[str, str], int]) -> tuple[str, dict[str, Any]]:
            key, route_path, params, limit = spec
            try:
                result = read_feed(
                    route_path=route_path,
                    params=params,
                    limit=limit,
                    force=force,
                )
                result["route_path"] = route_path
                result["params"] = params
                result["_fetched_at"] = datetime.now().isoformat()
                return key, result
            except Exception as exc:
                logger.warning("market theme rss source failed: %s", key, exc_info=True)
                return key, {
                    "route_path": route_path,
                    "params": params,
                    "items": [],
                    "errors": [str(exc)],
                    "_fetched_at": datetime.now().isoformat(),
                    "_cached": False,
                }

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_fetch_feed, spec) for spec in rss_sources]
            for future in as_completed(futures):
                key, result = future.result()
                rss_context[key] = result

    source_catalog = _build_source_catalog(rss_context)
    return {
        "generated_at": current_shanghai_timestamp(),
        "source_snapshot": {
            "market_status": market_status,
            "market_regime": market_regime,
            "industry_sectors": industry_catalog["items"],
            "concept_sectors": concept_catalog["items"],
            "board_catalog_status": {
                "industry": {
                    "data_time": industry_catalog["data_time"],
                    "errors": industry_catalog["errors"],
                },
                "concept": {
                    "data_time": concept_catalog["data_time"],
                    "errors": concept_catalog["errors"],
                },
            },
            "rss": rss_context,
            "source_catalog": source_catalog,
        },
    }


def get_latest_report(report_key: str) -> Optional[dict[str, Any]]:
    try:
        current_as_of_date = _current_report_as_of_date()
        report = DatabaseManager.get_instance().get_latest_market_mainline_report(
            report_key=report_key,
            mode="llm",
            as_of_date=current_as_of_date,
        )
        if report and report.get("contract_version") != MARKET_MAINLINE_REPORT_CONTRACT:
            logger.info(
                "忽略旧版市场主线报告：expected=%s actual=%s",
                MARKET_MAINLINE_REPORT_CONTRACT,
                report.get("contract_version"),
            )
            return None
        return report
    except Exception:
        logger.exception("读取最新市场主线模型报告失败")
        return None


def build_report_evidence_pack(context: dict[str, Any]) -> dict[str, Any]:
    snapshot = context["source_snapshot"]
    rss = snapshot.get("rss") or {}
    sections: dict[str, list[dict[str, Any]]] = {
        "policy_headlines": summarize_feed_items(
            rss.get("policy_calendar"),
            section="policy",
        ),
        "market_news": summarize_feed_items(
            rss.get("market_news"),
            section="market_news",
        ),
        "strategy_reports": summarize_feed_items(
            rss.get("strategy_reports"),
            section="strategy_report",
        ),
        "macro_reports": summarize_feed_items(
            rss.get("macro_reports"),
            section="macro_report",
        ),
        "industry_reports": summarize_feed_items(
            rss.get("industry_reports"),
            section="industry_report",
        ),
        "exchange_disclosure": summarize_feed_items(
            rss.get("exchange_disclosure"),
            section="exchange_disclosure",
        ),
        "exchange_inquire": summarize_feed_items(
            rss.get("exchange_inquire"),
            section="exchange_inquire",
        ),
        "money_center": summarize_feed_items(
            rss.get("money_center"),
            section="money_center",
        ),
    }
    evidence_refs = {
        str(item["evidence_id"]): {
            "section": section,
            "name": item.get("name") or item.get("title") or "",
        }
        for section, items in sections.items()
        for item in items
    }
    return {
        "generated_at": context["generated_at"],
        "as_of_date": snapshot.get("market_status", {}).get("data_time") or datetime.now().date().isoformat(),
        "market_status": snapshot.get("market_status") or {},
        "market_regime": snapshot.get("market_regime") or {},
        "board_catalog": {
            "industry": [item for item in snapshot.get("industry_sectors") or [] if isinstance(item, dict)],
            "concept": [item for item in snapshot.get("concept_sectors") or [] if isinstance(item, dict)],
        },
        **sections,
        "evidence_refs": evidence_refs,
        "source_summary": _summarize_sources(snapshot),
    }


def _identified_items(
    items: list[dict[str, Any]],
    section: str,
) -> list[dict[str, Any]]:
    return [{**item, "evidence_id": f"{section}:{index}"} for index, item in enumerate(items) if isinstance(item, dict)]


def summarize_feed_items(
    feed: Optional[dict[str, Any]],
    limit: int | None = None,
    *,
    section: str = "feed",
) -> list[dict[str, str]]:
    items = (feed or {}).get("items") or []
    results: list[dict[str, str]] = []
    selected = items if limit is None else items[: max(0, int(limit))]
    for index, item in enumerate(selected):
        results.append(
            {
                "evidence_id": f"{section}:{index}",
                "title": str(item.get("title") or "").strip(),
                "summary": shorten(strip_html(str(item.get("summary") or "")), 180),
                "published": str(item.get("published") or ""),
                "link": str(item.get("link") or item.get("url") or ""),
            }
        )
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
        "contract_version": MARKET_MAINLINE_REPORT_CONTRACT,
        "generated_at": current_shanghai_timestamp(),
        "as_of_date": datetime.now().date().isoformat(),
        "overview": "市场主线模型研判暂不可用，请稍后重试。",
        "full_report": "本次未能完成模型直出研判，当前仅保留最小可用结果。",
        "market_stage": {
            "label": "服务降级",
            "description": "模型报告暂时不可用。",
        },
        "current_mainlines": [],
        "candidate_mainlines": [],
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
        {"name": "行业与概念板块目录", "category": "方向映射目录", "credibility": "中高", "used": True},
        {
            "name": "上交所问询与披露",
            "category": "交易所",
            "credibility": "高",
            "used": bool(
                (rss_context.get("exchange_inquire") or {}).get("items")
                or (rss_context.get("exchange_disclosure") or {}).get("items")
            ),
        },
        {
            "name": "中国外汇交易中心公开信息",
            "category": "官方公开信息",
            "credibility": "高",
            "used": bool((rss_context.get("money_center") or {}).get("items")),
        },
        {
            "name": "财联社电报 / 华尔街见闻日历",
            "category": "公共资讯",
            "credibility": "中高",
            "used": bool(
                (rss_context.get("market_news") or {}).get("items")
                or (rss_context.get("policy_calendar") or {}).get("items")
            ),
        },
        {
            "name": "东方财富策略/宏观/行业研报",
            "category": "卖方公开研报",
            "credibility": "中",
            "used": bool(
                (rss_context.get("strategy_reports") or {}).get("items")
                or (rss_context.get("macro_reports") or {}).get("items")
                or (rss_context.get("industry_reports") or {}).get("items")
            ),
        },
    ]


def _summarize_sources(snapshot: dict[str, Any]) -> dict[str, Any]:
    rss = snapshot.get("rss") or {}
    return {
        "official_count": sum(
            1
            for key in ("exchange_inquire", "exchange_disclosure", "money_center")
            if (rss.get(key) or {}).get("items")
        ),
        "news_count": sum(len((rss.get(key) or {}).get("items") or []) for key in ("market_news", "policy_calendar")),
        "report_count": sum(
            len((rss.get(key) or {}).get("items") or [])
            for key in ("strategy_reports", "macro_reports", "industry_reports")
        ),
        "source_catalog": snapshot.get("source_catalog") or [],
    }
