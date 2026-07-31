# src/services/buy_criteria/evaluators/competition_landscape.py
"""Evaluator ④: 竞争格局 — Is the industry free from price wars/involution?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.evidence_queries import structured_thesis_queries
from src.services.buy_criteria.prompts.rubrics import COMPETITION_LANDSCAPE

logger = logging.getLogger(__name__)


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """Ensure value is a list of dicts."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


class CompetitionLandscapeEvaluator(BaseCriterionEvaluator):
    criterion_id = "competition_landscape"
    criterion_name = "竞争格局"
    index = 4

    def collect_data(
        self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None
    ) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {
            "investment_thesis": str(stock_info.get("_investment_thesis") or "").strip() or None,
            "company_profile": {
                "name": stock_info.get("name") or stock_info.get("short_name"),
                "industry": stock_info.get("industry"),
                "main_business": stock_info.get("main_business"),
                "business_scope": stock_info.get("business_scope"),
            },
        }

        # --- Margin data — use pre-fetched valuation when available (from page) ---
        valuation = None
        if pre_fetched_data and "valuation" in pre_fetched_data:
            valuation = pre_fetched_data["valuation"]
            logger.info("[competition] using pre-fetched valuation for %s", symbol)
        else:
            try:
                valuation = ds.get_valuation_ratios(symbol)
            except Exception as exc:
                logger.warning("[competition] valuation failed: %s", exc)

        if valuation:
            industry_avg = valuation.get("industry_average") or {}
            raw["margin_data"] = {
                "gross_margin": valuation.get("gross_margin"),
                "net_margin": valuation.get("net_margin"),
                "industry_avg_gross_margin": industry_avg.get("gross_margin"),
                "industry_avg_net_margin": industry_avg.get("net_margin"),
            }

        # --- Margin trend — last 4 quarters ---
        try:
            financials = ds.get_financials(symbol, periods=4, force=True)
            items = _list_of_dicts(financials.get("items"))[:4]
            raw["margin_trend"] = {
                "items": [
                    {"date": item.get("report_date"), "gross_margin": item.get("gross_margin")} for item in items
                ],
            }
        except Exception as exc:
            logger.warning("[competition] financials failed: %s", exc)

        # Only company/segment-targeted research is admissible here.  The old
        # generic "latest industry reports" feed mixed solar, chemicals and
        # other unrelated price wars into precision-transmission judgments.
        raw["industry_research"] = _fetch_targeted_research(ds, symbol)
        competition_items: list[dict[str, Any]] = []
        competition_errors: list[str] = []
        competition_queries = structured_thesis_queries(stock_info, limit=4)
        for query in competition_queries:
            try:
                result = ds.search_industry_news(query, days=365, limit=8)
                competition_items.extend({**item, "query": query} for item in _list_of_dicts(result.get("items"))[:8])
            except Exception as exc:
                competition_errors.append(f"{query}: {exc}")
        raw["competition_evidence"] = {
            "queries": competition_queries,
            "items": competition_items[:24],
            "errors": competition_errors,
        }

        # Build summary
        summary = _build_summary(raw)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return COMPETITION_LANDSCAPE

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        raw = evidence.raw_data
        margins = (raw.get("margin_trend") or {}).get("items") or []
        reports = (raw.get("industry_research") or {}).get("items") or []
        industry_items = (raw.get("competition_evidence") or {}).get("items") or []
        if not margins and not reports and not industry_items:
            return "毛利率趋势、公司研究和细分产业竞争证据均不可用，无法证明竞争格局健康"
        return None


def _fetch_targeted_research(ds: DataService, symbol: str) -> dict[str, Any]:
    """Return research already scoped by the concrete security."""
    try:
        result = ds.get_research_report(symbol, days=1095)
        items = _list_of_dicts(result.get("items"))
        return {
            "items": [
                {
                    "title": item.get("title"),
                    "summary": (item.get("summary") or "")[:360],
                    "source": item.get("org") or item.get("source") or "个股研报",
                    "time": item.get("publish_date") or item.get("publish_time"),
                }
                for item in items[:12]
            ],
            "data_time": result.get("data_time"),
            "is_stale": result.get("is_stale"),
        }
    except Exception as exc:
        logger.warning("[competition] targeted research failed: %s", exc)
        return {"items": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------


def _build_summary(raw: dict[str, Any]) -> str:
    profile = raw.get("company_profile") or {}
    lines = [
        "## 本轮细分产业范围",
        f"- 公司：{profile.get('name') or '缺失'}",
        f"- 法定行业：{profile.get('industry') or '缺失'}",
        f"- 主营业务：{profile.get('main_business') or '缺失'}",
        f"- 经营范围：{str(profile.get('business_scope') or '缺失')[:420]}",
        f"- 本轮投资逻辑：{raw.get('investment_thesis') or '未指定'}",
        "",
        "## 毛利率数据",
    ]
    md = raw.get("margin_data", {})
    if md.get("gross_margin") is not None:
        lines.append(f"- 当前毛利率：{md['gross_margin']}%")
    if md.get("industry_avg_gross_margin") is not None:
        lines.append(f"- 行业平均毛利率：{md['industry_avg_gross_margin']}%")

    mt = raw.get("margin_trend", {})
    if mt.get("items"):
        trend_parts = []
        for item in mt["items"]:
            gm = item.get("gross_margin")
            val = f"{gm}%" if gm is not None else "?"
            trend_parts.append(f"{item.get('date') or '?'} {val}")
        lines.append(f"- 最近4季度毛利率趋势：{' → '.join(trend_parts)}")

    # Security-scoped industry research
    lines.extend(["", "## 公司及真实细分产业研究（已按证券过滤）"])
    reports = raw.get("industry_research", {})
    rpt_items = _list_of_dicts(reports.get("items"))
    if rpt_items:
        for item in rpt_items:
            lines.append(
                f"- [{item.get('time', '?')}] {item.get('source', '')}："
                f"{item.get('title', '')}；{item.get('summary', '')}"
            )
    else:
        lines.append("- 无已按证券过滤的研报数据")

    competition = raw.get("competition_evidence") or {}
    lines.extend(
        [
            "",
            "## 细分产业竞争与供给证据",
            f"- 结构化查询方向：{'、'.join(competition.get('queries') or []) or '缺失'}",
        ]
    )
    for item in _list_of_dicts(competition.get("items")):
        lines.append(
            f"- [{item.get('query') or '未标注方向'}] "
            f"{item.get('publish_time') or item.get('date') or '未知日期'} "
            f"{item.get('source') or '公开资料'}：{item.get('title') or '无标题'}；"
            f"{str(item.get('summary') or item.get('content') or '')[:260]}"
        )
    if not _list_of_dicts(competition.get("items")):
        lines.append("- 未取得可用的细分产业竞争材料")

    lines.extend(
        [
            "",
            "## 判断约束",
            "- 只判断公司真实受益的细分产品行业。其他行业的降价、产能或内卷材料均为无效证据。",
            "- 股价、板块涨跌、资金流、换手率和新闻中的个股下跌，全部不能证明产品价格战或产业内卷。",
            "- 单个季度毛利率回落不等于连续下滑；至少需要两个连续季度下降，或同时有同一细分行业的降价、扩产过剩、份额恶化等证据，才可据此否决。",
            "- 公司毛利率不等于行业平均毛利率。若行业均值缺失，必须如实标注，不能把公司单季变化改写为行业趋势。",
            "- 通过也需要正面竞争力证据，例如份额、技术/客户壁垒、规模成本、差异化或盈利能力；仅仅没有找到价格战新闻不够。",
        ]
    )

    return "\n".join(lines)
