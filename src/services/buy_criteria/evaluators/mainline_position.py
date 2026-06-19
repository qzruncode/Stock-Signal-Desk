"""Evaluator ①: 市场主线属性 — Is the stock's industry a market mainline?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import MAINLINE_POSITION

logger = logging.getLogger(__name__)


def _text(value: Any, default: str = "缺失") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compact_mainline(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "rank": item.get("rank"),
        "stage": item.get("stage") or item.get("stage_hint"),
        "branches": item.get("branches") or [],
        "reason": item.get("reason"),
        "focus": item.get("focus"),
        "evidence": item.get("evidence") or [],
        "triggers": item.get("triggers") or [],
    }


def _format_mainline_rows(title: str, rows: list[dict[str, Any]], *, candidate: bool = False) -> list[str]:
    lines = [f"### {title}"]
    if not rows:
        lines.append("- 缺失")
        return lines
    for index, item in enumerate(rows, start=1):
        prefix = "候选" if candidate else "当前"
        lines.append(
            f"{index}. {prefix}主线：{_text(item.get('name'))}"
            f"；排名：{_text(item.get('rank'), '-')}"
            f"；阶段：{_text(item.get('stage'), '-')}"
        )
        branches = item.get("branches") or []
        if branches:
            lines.append(f"   - 分支：{'、'.join(str(branch) for branch in branches[:6])}")
        reason = _text(item.get("reason"), "")
        if reason:
            lines.append(f"   - 主线理由：{reason[:260]}")
        focus = _text(item.get("focus"), "")
        if focus:
            lines.append(f"   - 观察重点：{focus[:180]}")
        evidence = item.get("evidence") or []
        if evidence:
            lines.append(f"   - 主线证据：{'；'.join(str(piece) for piece in evidence[:4])[:360]}")
        triggers = item.get("triggers") or []
        if triggers:
            lines.append(f"   - 升级触发：{'；'.join(str(trigger) for trigger in triggers[:4])[:240]}")
    return lines


class MainlinePositionEvaluator(BaseCriterionEvaluator):
    criterion_id = "mainline_position"
    criterion_name = "市场主线属性"
    index = 0

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        raw["stock_profile"] = {
            "symbol": stock_info.get("symbol") or symbol,
            "name": stock_info.get("name") or stock_info.get("short_name"),
            "short_name": stock_info.get("short_name"),
            "industry": stock_info.get("industry"),
            "main_business": stock_info.get("main_business"),
            "product_type": stock_info.get("product_type"),
            "product_name": stock_info.get("product_name"),
            "profile": stock_info.get("profile"),
        }

        try:
            market_report = ds.get_market_mainline_report()
            raw["market_mainline_report"] = {
                "report_pending": bool(market_report.get("report_pending")),
                "as_of_date": market_report.get("as_of_date"),
                "overview": market_report.get("overview"),
                "market_stage": market_report.get("market_stage"),
                "current_mainlines": [
                    _compact_mainline(item)
                    for item in _list_of_dicts(market_report.get("current_mainlines"))[:5]
                ],
                "future_mainlines": [
                    _compact_mainline(item)
                    for item in _list_of_dicts(market_report.get("future_mainlines"))[:5]
                ],
            }
        except Exception as exc:
            logger.warning("[mainline] market_mainline_report failed: %s", exc)
            raw["market_mainline_report_error"] = str(exc)

        # Auxiliary market heat. It is included for context only, not as a pass basis.
        try:
            sectors = ds.get_sector_list("industry")
            raw["sector_list"] = {
                "top_10": [
                    {"name": s["name"], "change_pct": s.get("change_pct"), "rank": s.get("rank"), "total_amount": s.get("total_amount")}
                    for s in (sectors.get("items") or [])[:10]
                ],
                "data_time": sectors.get("data_time"),
            }
            # Find this stock's industry in the ranking
            industry = stock_info.get("industry", "")
            for s in (sectors.get("items") or []):
                if industry and s.get("name") == industry:
                    raw["target_industry_rank"] = {"name": s["name"], "rank": s.get("rank"), "change_pct": s.get("change_pct"), "total_amount": s.get("total_amount")}
                    break
        except Exception as exc:
            logger.warning("[mainline] sector_list failed: %s", exc)
            raw["sector_list_error"] = str(exc)

        # Sentiment
        try:
            sentiment = ds.get_sentiment(symbol)
            raw["sentiment"] = {
                "score": sentiment.get("sentiment_score"),
                "total_discussion": sentiment.get("total_discussion"),
            }
        except Exception as exc:
            logger.warning("[mainline] sentiment failed: %s", exc)
            raw["sentiment_error"] = str(exc)

        # Social sentiment
        try:
            social = ds.get_social_sentiment(symbol)
            raw["social_sentiment"] = {
                "score": social.get("score") or social.get("social_score"),
                "trend": social.get("trend"),
            }
        except Exception as exc:
            logger.warning("[mainline] social_sentiment failed: %s", exc)

        # Build summary
        profile = raw["stock_profile"]
        report = raw.get("market_mainline_report") or {}
        market_stage = report.get("market_stage") or {}
        current_mainlines = _list_of_dicts(report.get("current_mainlines"))
        future_mainlines = _list_of_dicts(report.get("future_mainlines"))
        lines = [
            "## 公司主营资料",
            f"- 股票：{_text(profile.get('name'))} ({_text(profile.get('symbol') or symbol)})",
            f"- 所属行业：{_text(profile.get('industry'))}",
            f"- 主营业务：{_text(profile.get('main_business'))}",
            f"- 产品类型：{_text(profile.get('product_type'))}",
            f"- 产品名称：{_text(profile.get('product_name'))}",
            f"- 公司简介：{_text(profile.get('profile'))[:260]}",
            "",
            "## 最新市场主线报告",
            f"- 报告日期：{_text(report.get('as_of_date'))}",
            f"- 报告状态：{'生成中/不可用' if report.get('report_pending') else '可用'}",
            f"- 市场阶段：{_text(market_stage.get('label'))}",
            f"- 总览：{_text(report.get('overview'))}",
            "",
            *_format_mainline_rows("当前主线/分支主线", current_mainlines),
            "",
            *_format_mainline_rows("候选主线（仅作观察，不等同于当前主线）", future_mainlines, candidate=True),
            "",
            "## 辅助市场热度数据（不能单独作为通过依据）",
        ]
        auxiliary_added = False
        if "target_industry_rank" in raw:
            r = raw["target_industry_rank"]
            lines.append(f"- 行业[{r['name']}]板块排名第{r.get('rank', '?')}名，涨跌幅{r.get('change_pct', '?')}%")
            auxiliary_added = True
        if raw.get("sentiment", {}).get("score") is not None:
            lines.append(f"- 舆情情绪评分{raw['sentiment']['score']}，总讨论量{raw.get('sentiment', {}).get('total_discussion', '?')}")
            auxiliary_added = True
        if raw.get("social_sentiment", {}).get("score") is not None:
            lines.append(f"- 社交情绪评分{raw['social_sentiment']['score']}")
            auxiliary_added = True
        if not auxiliary_added:
            lines.append("- 缺失")
        lines.extend([
            "",
            "## 判断约束",
            "- 不要使用本地关键词命中、单日板块涨跌、资金流排名或情绪分作为通过依据。",
            "- 只有当公司主营业务/产品与【当前主线/分支主线】在业务逻辑、分支或证据上存在明确对应关系，才可判为通过。",
            "- 候选主线只能作为观察方向，不能判为通过。",
        ])

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return MAINLINE_POSITION
