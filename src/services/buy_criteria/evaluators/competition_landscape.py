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
    index = 3

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

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

        # --- Industry board data (THS summary) ---
        raw["industry_board"] = _fetch_industry_board(industry_name)

        # --- News: industry reports + stock news ---
        raw["industry_reports"] = _fetch_industry_reports()
        raw["stock_news"] = _fetch_stock_news(ds, symbol)

        # Build summary
        summary = _build_summary(raw)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return COMPETITION_LANDSCAPE


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
        from api.v1.endpoints.rss import get_rss_feeds
        result = get_rss_feeds(source="eastmoney_report", category="industry", limit=12, force=False)
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


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def _build_summary(raw: dict[str, Any]) -> str:
    lines = ["## 毛利率数据"]
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
            trend_parts.append(val)
        lines.append(f"- 最近4季度毛利率趋势：{' → '.join(trend_parts)}")

    # Industry board (THS)
    lines.extend(["", "## 所属行业板块数据"])
    ib = raw.get("industry_board", {})
    if not ib.get("error"):
        matched = ib.get("matched_name", "")
        rank = ib.get("rank")
        total = ib.get("total_boards")
        if rank is not None and total:
            lines.append(f"- 行业板块：{matched}，排名 {rank}/{total}")
        lines.append(f"- 板块涨跌幅：{ib.get('change_pct')}%")
        net = ib.get("net_flow")
        if net is not None:
            unit = "亿"
            val = abs(net) / 1e8
            lines.append(f"- 板块主力资金净流入：{'+' if net > 0 else '-'}{val:.2f}{unit}")
        up, down = ib.get("up_count"), ib.get("down_count")
        if up is not None and down is not None:
            lines.append(f"- 板块涨跌家数：{int(up)} 涨 / {int(down)} 跌")
        ls = ib.get("lead_stock")
        lsp = ib.get("lead_stock_change_pct")
        if ls:
            pct_str = f"{lsp}%" if lsp is not None else ""
            lines.append(f"- 领涨股：{ls}（{pct_str}）")
    else:
        lines.append(f"- 行业板块数据不可用：{ib.get('error', '未知')}")

    # Industry reports
    lines.extend(["", "## 行业研究报告（请重点参考，判断行业竞争格局/产能/价格趋势）"])
    reports = raw.get("industry_reports", {})
    rpt_items = _list_of_dicts(reports.get("items"))
    if rpt_items:
        for item in rpt_items:
            lines.append(f"- [{item.get('time', '?')}] {item.get('source', '')}：{item.get('title', '')}")
    else:
        lines.append("- 无行业研报数据")

    # Stock news
    lines.extend(["", "## 公司相关新闻（补充参考）"])
    stock_news = raw.get("stock_news", [])
    if stock_news:
        for item in stock_news[:6]:
            lines.append(f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:160]}；{item.get('summary', '')}")
    else:
        lines.append("- 无新闻数据")

    lines.extend([
        "",
        "## 判断约束",
        "- 毛利率连续下滑 + 行业研报/新闻中有价格战/产能过剩/内卷/利润压缩描述 = 内卷风险高。",
        "- 毛利率稳定/提升 + 行业研报显示供需格局良好 = 竞争格局健康。",
        "- 请基于行业研报实际内容判断竞争格局，行业研报比个股新闻更能反映行业竞争状态。",
    ])

    return "\n".join(lines)
