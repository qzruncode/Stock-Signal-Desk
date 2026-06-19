"""Evaluator ②: 景气上行周期 — Is the industry in an upward cycle?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import PROSPERITY_CYCLE

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _revenue_trend_summary(items: list[dict[str, Any]]) -> str:
    recent = [
        {
            "date": item.get("report_date"),
            "revenue_yoy": _safe_float(item.get("revenue_yoy")),
        }
        for item in items
    ]
    values = [item["revenue_yoy"] for item in recent if item["revenue_yoy"] is not None]
    if len(values) < 3:
        return "营收同比样本不足，不能单独判断连续趋势。"
    chronological = list(reversed(values[:3]))
    if all(value >= 15 for value in values[:3]):
        return "最近3期营收同比均不低于15%，具备高位增长特征。"
    if chronological[0] < chronological[1] < chronological[2]:
        return "最近3期营收同比逐期改善。"
    if chronological[0] > chronological[1] > chronological[2]:
        return "最近3期营收同比连续走弱。"
    return "最近3期营收同比有波动，需要结合行业与公司订单证据确认。"


def _compact_financial_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_date": item.get("report_date"),
        "revenue": item.get("revenue"),
        "revenue_yoy": item.get("revenue_yoy"),
        "revenue_qoq": item.get("revenue_qoq"),
        "gross_margin": item.get("gross_margin"),
        "net_profit_yoy": item.get("net_profit_yoy") or item.get("parent_net_profit_yoy"),
    }


class ProsperityCycleEvaluator(BaseCriterionEvaluator):
    criterion_id = "prosperity_cycle"
    criterion_name = "景气上行周期"
    index = 1

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        raw["stock_profile"] = {
            "symbol": stock_info.get("symbol") or symbol,
            "name": stock_info.get("name") or stock_info.get("short_name"),
            "industry": stock_info.get("industry"),
            "main_business": stock_info.get("main_business"),
            "product_type": stock_info.get("product_type"),
            "product_name": stock_info.get("product_name"),
        }

        # Sector ranking and fund flow — raw data from endpoints.
        try:
            sectors = ds.get_sector_list("industry")
            industry = stock_info.get("industry", "")
            items_raw = sectors.get("items") or []
            # Sort by change_pct desc (nulls last) and assign rank
            items_sorted = sorted(
                items_raw,
                key=lambda s: (s.get("change_pct") is not None, s.get("change_pct") or 0),
                reverse=True,
            )
            for i, s in enumerate(items_sorted, 1):
                s["rank"] = i
            raw["sector_data"] = {
                "items": [
                    {"name": s["name"], "rank": s.get("rank"), "change_pct": s.get("change_pct")}
                    for s in items_sorted[:20]
                ],
                "target_industry": None,
            }
            for s in items_sorted:
                if s.get("name") == industry:
                    raw["sector_data"]["target_industry"] = {
                        "name": s["name"], "rank": s.get("rank"),
                        "change_pct": s.get("change_pct"), "total_amount": s.get("total_amount"),
                    }
                    break
        except Exception as exc:
            logger.warning("[prosperity] sector_list failed: %s", exc)

        try:
            fund_flow = ds.get_sector_flow_industry()
            raw["fund_flow"] = {
                "inflow": [
                    {"name": f["name"], "net_flow": f.get("main_net_inflow"), "change_pct": f.get("pct_chg")}
                    for f in (fund_flow or [])[:10]
                ],
                "outflow": [
                    {"name": f["name"], "net_flow": f.get("main_net_inflow"), "change_pct": f.get("pct_chg")}
                    for f in (fund_flow or [])[-10:]
                ],
            }
        except Exception as exc:
            logger.warning("[prosperity] sector_flow failed: %s", exc)

        # Company financial growth gives concrete revenue/profit trend evidence.
        try:
            financials = ds.get_financials(symbol, periods=4, force=True)
            items = [_compact_financial_item(item) for item in _list_of_dicts(financials.get("items"))[:4]]
            raw["financials"] = {
                "items": items,
                "trend_summary": _revenue_trend_summary(items),
                "_cached": financials.get("_cached"),
            }
        except Exception as exc:
            logger.warning("[prosperity] financials failed: %s", exc)
            raw["financials_error"] = str(exc)

        # Macro PMI is a broad auxiliary signal, not a substitute for industry PMI.
        try:
            pmi = ds.get_macro_indicator("PMI", months=6)
            raw["macro_pmi"] = {
                "latest": pmi.get("latest"),
                "trend": pmi.get("trend"),
                "history": _list_of_dicts(pmi.get("history"))[-6:],
                "data_time": pmi.get("data_time"),
                "is_stale": pmi.get("is_stale"),
            }
        except Exception as exc:
            logger.warning("[prosperity] PMI failed: %s", exc)
            raw["macro_pmi_error"] = str(exc)

        # Build summary
        profile = raw["stock_profile"]
        industry = profile.get("industry", "")
        target = raw.get("sector_data", {}).get("target_industry")
        financial_items = _list_of_dicts(_as_dict(raw.get("financials")).get("items"))
        pmi = _as_dict(raw.get("macro_pmi"))
        pmi_latest = _as_dict(pmi.get("latest"))
        gaps: list[str] = []
        if not financial_items:
            gaps.append("最近财务增速缺失")
        if not pmi_latest:
            gaps.append("PMI缺失")
        elif pmi.get("is_stale"):
            gaps.append("宏观PMI数据可能过期")
        if not target:
            gaps.append("行业板块排名未匹配")
        gaps.append("细分行业PMI/产能利用率暂无直接数据")

        lines = [
            "## 公司与行业",
            f"- 股票：{profile.get('name') or symbol} ({profile.get('symbol') or symbol})",
            f"- 所属行业：{profile.get('industry') or '缺失'}",
            f"- 主营业务：{profile.get('main_business') or '缺失'}",
            f"- 产品：{profile.get('product_type') or '缺失'} / {profile.get('product_name') or '缺失'}",
            "",
            "## 行业板块与资金流",
        ]
        if target:
            lines.append(f"- 本行业[{target['name']}]：板块排名第{target.get('rank', '?')}名，涨跌幅{target.get('change_pct', '?')}%")
        else:
            lines.append(f"- 本行业[{industry}]：未匹配到板块排名数据")
        top5 = raw.get("sector_data", {}).get("items", [])[:5]
        if top5:
            top5_str = '; '.join(f'{s["name"]} (#{s["rank"]})' for s in top5)
            lines.append(f"- 板块前5名：{top5_str}")
        else:
            lines.append("- 板块前5名：缺失")
        lines.append("")

        ff = raw.get("fund_flow", {})
        if ff.get("inflow"):
            inflow3 = ff["inflow"][:3]
            inflow3_str = '; '.join(f'{f["name"]} (+{f["net_flow"]})' for f in inflow3)
            lines.append(f"- 资金净流入前3：{inflow3_str}")
        else:
            lines.append("- 资金流数据：缺失")
        lines.append("")

        lines.extend([
            "## 公司财务增速",
            f"- 趋势摘要：{_as_dict(raw.get('financials')).get('trend_summary') or '缺失'}",
        ])
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
            "## PMI与产能利用率",
            f"- 宏观PMI最新值：{pmi_latest.get('value') or pmi_latest.get('current') or pmi_latest.get('pmi') or '缺失'}；趋势：{pmi.get('trend') or '缺失'}；日期：{pmi.get('data_time') or '缺失'}；是否过期：{pmi.get('is_stale')}",
            "- 细分行业PMI：缺失",
            "- 产能利用率：缺失",
            "",
            "## 数据缺口",
            *[f"- {gap}" for gap in gaps],
            "",
            "## 判断约束",
            "- 不要因为PMI或产能利用率缺失，就忽略已提供的板块排名、资金流和公司营收增速证据。",
            "- 如果营收增速、板块排名、资金流结论相互矛盾，需要在 verdict 里说明矛盾点。",
            "- 单日板块涨跌和资金流只能辅助判断，不得单独判定景气上行。",
        ])

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return PROSPERITY_CYCLE
