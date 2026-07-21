# src/services/buy_criteria/evaluators/competition_landscape.py
"""Evaluator ④: 竞争格局 — Is the industry free from price wars/involution?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import COMPETITION_LANDSCAPE

logger = logging.getLogger(__name__)


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """Ensure value is a list of dicts."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _safe_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class CompetitionLandscapeEvaluator(BaseCriterionEvaluator):
    criterion_id = "competition_landscape"
    criterion_name = "竞争格局"
    index = 4

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
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

        # --- Stock industry name ---
        industry_name = _safe_str(stock_info.get("industry", ""))
        raw["industry_name"] = industry_name

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
                    {"date": item.get("report_date"), "gross_margin": item.get("gross_margin")}
                    for item in items
                ],
            }
        except Exception as exc:
            logger.warning("[competition] financials failed: %s", exc)

        # Only company/segment-targeted research is admissible here.  The old
        # generic "latest industry reports" feed mixed solar, chemicals and
        # other unrelated price wars into precision-transmission judgments.
        raw["industry_research"] = _fetch_targeted_research(ds, symbol)

        # Build summary
        summary = _build_summary(raw)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return COMPETITION_LANDSCAPE

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        raw = evidence.raw_data
        margins = ((raw.get("margin_trend") or {}).get("items") or [])
        reports = ((raw.get("industry_research") or {}).get("items") or [])
        if not margins and not reports:
            return "毛利率趋势和行业竞争研究均无可用证据，无法证明行业不过度内卷"
        return None


# ---------------------------------------------------------------------------
# Industry board — THS stock_board_industry_summary_ths
# ---------------------------------------------------------------------------

def _fetch_industry_board(industry_name: str) -> dict[str, Any]:
    """Fetch THS industry board summary for the stock's industry.

    Exact match only. If no match, the model uses industry reports + news
    to judge competition landscape instead.
    """
    import re

    if not industry_name:
        return {"error": "no industry name"}

    try:
        import akshare as ak

        name_df = ak.stock_board_industry_name_ths()
        summary_df = ak.stock_board_industry_summary_ths()
        if name_df is None or name_df.empty or summary_df is None or summary_df.empty:
            return {"error": "ths tables empty"}

        def _norm(s: str) -> str:
            return re.sub(r'\s+', '', str(s).lower())

        aliases = {
            _norm(industry_name),
            _norm(industry_name.replace("Ⅰ", "").replace("Ⅱ", "").replace("Ⅲ", "")),
        }

        ths_names = [str(n) for n in name_df["name"].astype(str)]
        matched = None
        for alias in aliases:
            for tn in ths_names:
                if alias == _norm(tn):
                    matched = tn
                    break
            if matched:
                break

        if not matched:
            return {"error": f"ths board not matched for '{industry_name}'"}

        code_row = name_df[name_df["name"].astype(str) == matched]
        matched_code = str(code_row.iloc[0]["code"]) if not code_row.empty else ""

        s = summary_df.copy()
        s["_name"] = s["板块"].astype(str)
        row = s[s["_name"] == matched]
        if row.empty:
            return {"error": f"summary not found for '{matched}'", "matched_name": matched}

        r = row.iloc[0]
        up_down = _safe_str(r.get("涨跌家数"))
        up_count = down_count = None
        if "/" in up_down:
            parts = up_down.split("/", 1)
            up_count = _safe_float(parts[0])
            down_count = _safe_float(parts[1])

        return {
            "matched_name": matched,
            "matched_code": matched_code,
            "rank": _safe_float(r.get("序号")),
            "total_boards": int(len(summary_df)),
            "change_pct": _safe_float(r.get("涨跌幅")),
            "lead_stock": _safe_str(r.get("领涨股")),
            "lead_stock_change_pct": _safe_float(r.get("领涨股-涨跌幅")),
            "up_count": up_count,
            "down_count": down_count,
            "total_amount": _safe_float(r.get("总成交额")) * 1e8 if _safe_float(r.get("总成交额")) else None,
            "net_flow": _safe_float(r.get("净流入")) * 1e8 if _safe_float(r.get("净流入")) else None,
        }
    except Exception as exc:
        logger.warning("[competition] THS industry board failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Industry reports — RSSHub eastmoney_report/industry
# ---------------------------------------------------------------------------

def _fetch_industry_reports() -> dict[str, Any]:
    """Fetch industry research reports via RSSHub."""
    try:
        from api.v1.endpoints._rss_reader import read_feed

        result = read_feed(
            route_path="/eastmoney/report/:category",
            params={"category": "industry"},
            limit=12,
            force=False,
        )
        items = _list_of_dicts(result.get("items"))
        if items:
            return {
                "items": [
                    {
                        "title": item.get("title"),
                        "source": item.get("author") or item.get("source") or "东方财富行业研报",
                        "time": item.get("published") or item.get("publish_time"),
                        "link": item.get("link"),
                    }
                    for item in items[:12]
                ],
            }
    except Exception as exc:
        logger.warning("[competition] industry reports failed: %s", exc)

    return {"items": []}


# ---------------------------------------------------------------------------
# Stock news
# ---------------------------------------------------------------------------

def _fetch_stock_news(ds: DataService, symbol: str) -> list[dict[str, Any]]:
    """Fetch stock-level news (supplementary context only)."""
    try:
        news = ds.search_news(symbol, days=90)
        items = _list_of_dicts(news.get("items"))
        return [
            {
                "title": n.get("title"),
                "summary": (n.get("summary") or "")[:200],
                "source": n.get("source"),
                "time": n.get("publish_time"),
            }
            for n in items[:8]
        ]
    except Exception as exc:
        logger.warning("[competition] stock news failed: %s", exc)
        return []


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

    lines.extend([
        "",
        "## 判断约束",
        "- 只判断公司真实受益的细分产品行业。任何其他行业（例如光伏、硅料、白酒）的降价、产能或内卷材料均为无效证据。",
        "- 股价、板块涨跌、资金流、换手率和新闻中的个股下跌，全部不能证明产品价格战或产业内卷。",
        "- 单个季度毛利率回落不等于连续下滑；至少需要两个连续季度下降，或同时有同一细分行业的降价、扩产过剩、份额恶化等证据，才可据此否决。",
        "- 公司毛利率不等于行业平均毛利率。若行业均值缺失，必须如实标注，不能把公司单季变化改写为行业趋势。",
        "- 通过也需要正面竞争力证据，例如份额、技术/客户壁垒、规模成本、差异化或盈利能力；仅仅没有找到价格战新闻不够。",
    ])

    return "\n".join(lines)
