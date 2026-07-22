"""Evaluator: verifiable company benefit and industrial competitiveness."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import INDUSTRIAL_COMPETITIVENESS

logger = logging.getLogger(__name__)


def _dict_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][:limit]


def _bare_symbol(value: Any) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    return match.group(1) if match else str(value or "").strip()


class IndustrialCompetitivenessEvaluator(BaseCriterionEvaluator):
    criterion_id = "industrial_competitiveness"
    criterion_name = "产业竞争力与真实受益"
    index = 1

    def collect_data(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionEvidence:
        del pre_fetched_data
        ds = DataService()
        thesis = str(stock_info.get("_investment_thesis") or "").strip()
        thesis_context = stock_info.get("_investment_thesis_context")
        raw: dict[str, Any] = {
            "investment_thesis": thesis or None,
            "profile": {
                key: stock_info.get(key)
                for key in (
                    "symbol", "name", "short_name", "industry", "main_business",
                    "product_type", "product_name", "business_scope", "company_profile", "profile",
                )
                if stock_info.get(key) is not None
            },
        }

        collectors = (
            ("business_segments", lambda: ds.get_business_segments(symbol, periods=2)),
            ("financials", lambda: ds.get_financials(symbol, periods=6, force=True)),
            ("announcements", lambda: ds.get_announcements(symbol, days=730, limit=100)),
            ("news", lambda: ds.search_news(symbol, days=365)),
            ("research", lambda: ds.get_research_report(symbol, days=1095)),
        )
        for key, collect in collectors:
            try:
                raw[key] = collect()
            except Exception as exc:
                logger.warning("[industrial_competitiveness] %s failed: %s", key, exc)
                raw[f"{key}_error"] = str(exc)

        if thesis_context:
            try:
                candidates = ds.get_investment_thesis_candidates(thesis_context)
                target = _bare_symbol(stock_info.get("symbol") or symbol)
                matched = next(
                    (
                        item for item in candidates.get("items") or []
                        if isinstance(item, dict) and _bare_symbol(item.get("symbol")) == target
                    ),
                    None,
                )
                raw["thesis_membership"] = {
                    "requested_domains": candidates.get("requested_domains") or [],
                    "context_themes": candidates.get("inferred_context_themes") or [],
                    "company_matched": bool(matched),
                    "matched_domains": (matched or {}).get("matched_domains") or [],
                    "lookup_themes": (matched or {}).get("lookup_themes") or [],
                    "boards": (matched or {}).get("boards") or [],
                    "sources": (matched or {}).get("sources") or [],
                    "decision_boundary": candidates.get("decision_boundary"),
                }
            except Exception as exc:
                logger.warning("[industrial_competitiveness] thesis membership failed: %s", exc)
                raw["thesis_membership_error"] = str(exc)

        announcement_items = _dict_items(
            (raw.get("announcements") or {}).get("items"),
            limit=100,
        )
        try:
            raw["formal_business_evidence"] = ds.get_formal_business_evidence(
                symbol,
                announcement_items,
                thesis=thesis,
                thesis_context=thesis_context if isinstance(thesis_context, dict) else None,
            )
        except Exception as exc:
            logger.warning(
                "[industrial_competitiveness] formal report bodies failed: %s",
                exc,
            )
            raw["formal_business_evidence_error"] = str(exc)

        lines = [
            "## 本轮投资逻辑",
            f"- {thesis or '未指定主题；按公司主营与最新市场主线的直接关系核验'}",
            "",
            "## 公司资料",
            f"- 公司：{raw['profile'].get('name') or raw['profile'].get('short_name') or symbol} ({symbol})",
            f"- 行业：{raw['profile'].get('industry') or '缺失'}",
            f"- 主营：{raw['profile'].get('main_business') or '缺失'}",
            f"- 产品：{raw['profile'].get('product_type') or '缺失'} / {raw['profile'].get('product_name') or '缺失'}",
            f"- 经营范围：{str(raw['profile'].get('business_scope') or '缺失')[:500]}",
            f"- 公司简介：{str(raw['profile'].get('company_profile') or raw['profile'].get('profile') or '缺失')[:360]}",
            "",
            "## 定期报告正文中的产业与经营证据",
        ]
        formal_business = raw.get("formal_business_evidence") or {}
        formal_passages = _dict_items(formal_business.get("items"), limit=12)
        formal_documents = _dict_items(formal_business.get("documents"), limit=3)
        if formal_documents:
            for document in formal_documents:
                lines.append(
                    f"- 已读取：{document.get('publish_date') or '未知日期'} "
                    f"{document.get('title') or '定期报告'}，共{document.get('page_count') or '?'}页，"
                    f"命中{document.get('passage_count') or 0}段；{document.get('url') or ''}"
                )
        if formal_passages:
            for index, item in enumerate(formal_passages, 1):
                lines.append(
                    f"- F{index} [{item.get('source') or '公司定期报告正文'}] "
                    f"{item.get('date') or '未知日期'}：{str(item.get('excerpt') or '')[:1200]} "
                    f"{item.get('url') or ''}"
                )
        elif raw.get("formal_business_evidence_error"):
            lines.append(
                f"- 正文读取失败：{raw['formal_business_evidence_error']}"
            )
        else:
            lines.append("- 未从已读取定期报告中检索到与本轮结构化产业方向相近的段落")

        lines.extend([
            "",
            "## 正式披露的主营构成",
        ])
        segments = _dict_items((raw.get("business_segments") or {}).get("items"), limit=10)
        if segments:
            for item in segments:
                lines.append(
                    "- "
                    f"{item.get('report_date') or '未知报告期'} {item.get('segment_name') or '未命名业务'}："
                    f"收入{item.get('revenue') if item.get('revenue') is not None else '缺失'}元，"
                    f"收入占比{item.get('revenue_share_pct') if item.get('revenue_share_pct') is not None else '缺失'}%，"
                    f"毛利率{item.get('gross_margin_pct') if item.get('gross_margin_pct') is not None else '缺失'}%"
                )
        else:
            lines.append("- 未取得可用的分业务收入或利润披露")

        lines.extend(["", "## 连续财务兑现"])
        financials = _dict_items((raw.get("financials") or {}).get("items"), limit=6)
        if financials:
            for item in financials:
                lines.append(
                    "- "
                    f"{item.get('report_date') or item.get('report_period') or '未知报告期'}："
                    f"营收同比{item.get('revenue_yoy') if item.get('revenue_yoy') is not None else '缺失'}%，"
                    f"扣非净利同比{item.get('deducted_net_profit_yoy') if item.get('deducted_net_profit_yoy') is not None else '缺失'}%，"
                    f"毛利率{item.get('gross_margin') if item.get('gross_margin') is not None else '缺失'}%"
                )
        else:
            lines.append("- 缺失")

        membership = raw.get("thesis_membership") or {}
        lines.extend(["", "## 结构化产业方向成员关系（L1映射证据）"])
        if membership:
            lines.append(f"- 请求方向：{'、'.join(str(item) for item in membership.get('requested_domains') or []) or '缺失'}")
            lines.append(f"- 公司是否命中：{'是' if membership.get('company_matched') else '否'}")
            lines.append(f"- 候选召回覆盖方向：{'、'.join(str(item) for item in membership.get('matched_domains') or []) or '无'}（不可据此声称公司拥有对应产品）")
            lines.append(f"- 对应板块：{'、'.join(str(item) for item in membership.get('boards') or []) or '无'}")
            lines.append("- 边界：该证据只证明产业映射，不能单独证明订单、收入或竞争优势。")
        elif raw.get("thesis_membership_error"):
            lines.append(f"- 获取失败：{raw['thesis_membership_error']}")
        else:
            lines.append("- 本轮未指定产业方向")

        lines.extend(["", "## 公司正式公告中的兑现证据"])
        announcements = announcement_items[:20]
        if announcements:
            for item in announcements:
                lines.append(
                    f"- {item.get('publish_date') or '未知日期'} "
                    f"[{item.get('notice_type') or '公告'}] {item.get('title') or '无标题'} "
                    f"{item.get('url') or ''}"
                )
        else:
            lines.append("- 缺失")

        lines.extend(["", "## 经营兑现与产业竞争地位线索"])
        for source_key, date_key in (("news", "publish_time"), ("research", "publish_date")):
            items = _dict_items((raw.get(source_key) or {}).get("items"), limit=10)
            if not items:
                lines.append(f"- {source_key}：缺失")
                continue
            for item in items:
                summary = str(item.get("summary") or "")[:260]
                lines.append(
                    f"- {item.get(date_key) or '未知日期'} "
                    f"{item.get('source') or item.get('org') or source_key}："
                    f"{item.get('title') or '无标题'}；{summary}"
                )

        lines.extend([
            "",
            "## 判断约束",
            "- 分业务收入/利润、正式资料证明的成熟主营产品、订单/销量/客户应用/产能/量产/出货/连续增速，是并列替代证据；不得把订单或量产公告设成额外必选项。",
            "- 对成熟的通用核心零部件，不要求公司必须披露带有特定下游主题名称的专项订单；应核验产品是否真实商业化、是否与本轮产业位置相符，以及竞争优势是否可回查。",
            "- 请求方向、候选召回覆盖方向和板块别名只能说明候选来源，不能证明公司具体产品。公司产品和真实受益必须以正式主营、经营范围、分业务披露或公告为准。",
            "- 至少一项业务真实性证据必须来自主营构成、公司正式资料、正式公告或其他公司正式披露；新闻和研报只能补充，不能单独证明真实受益。",
            "- 定期报告正文段落由结构化投资逻辑做通用相关性检索，只是候选证据；模型必须阅读原文语义，不能把检索命中本身当成通过。",
            "- 未披露客户名称或未单独公告订单，不等于没有客户或订单；只能标注证据边界，不能把未披露反推成负面事实。",
            "- 必须同时证明真实受益和可验证竞争优势；任何一项不足都判为不通过。",
            "- 证据来源异常与真实零披露必须区分；无法核验时不允许乐观推断。",
        ])
        return CriterionEvidence(raw_data=raw, data_summary="\n".join(lines))

    def get_rubric(self) -> str:
        return INDUSTRIAL_COMPETITIVENESS

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        raw = evidence.raw_data
        formal_sources = (
            "business_segments", "financials", "announcements",
            "formal_business_evidence",
        )
        profile = raw.get("profile") or {}
        has_formal_profile = bool(
            profile.get("main_business")
            or profile.get("business_scope")
            or profile.get("company_profile")
            or profile.get("product_name")
        )
        if not has_formal_profile and not any(isinstance(raw.get(key), dict) for key in formal_sources):
            return "公司正式资料、主营构成、财务和公告均获取失败，无法验证真实受益和产业竞争力"
        return None
