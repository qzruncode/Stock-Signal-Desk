# src/services/buy_criteria/evaluators/catalyst_events.py
"""Evaluator: Catalyst Events — Are there catalysts in the next 6-12 months?"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence, CriterionResult
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import CATALYST_EVENTS

logger = logging.getLogger(__name__)


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def normalize_catalyst_details(
    details: dict[str, Any],
    raw_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Bind model-extracted catalysts back to retrieved, numbered evidence.

    The model may interpret event semantics, but it cannot invent the source,
    date or URL.  Those fields are always copied from the retrieved evidence
    selected by its evidence ids.
    """
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for key in (
        "announcement_events",
        "document_events",
        "schedule_events",
        "news_events",
        "research_events",
        "industry_events",
    ):
        for item in _list_of_dicts(raw_data.get(key)):
            evidence_id = str(item.get("evidence_id") or "").strip().upper()
            if evidence_id:
                evidence_by_id[evidence_id] = item

    normalized: list[dict[str, Any]] = []
    for candidate in _list_of_dicts(details.get("catalysts")):
        event = str(candidate.get("event") or "").strip()
        time_window = str(candidate.get("time_window") or "").strip()
        raw_ids = candidate.get("evidence_ids")
        if isinstance(raw_ids, str):
            raw_ids = re.findall(r"[ADSNRI]\d+", raw_ids.upper())
        if not isinstance(raw_ids, list):
            raw_ids = []
        evidence_ids: list[str] = []
        for raw_id in raw_ids:
            evidence_id = str(raw_id or "").strip().upper()
            if evidence_id in evidence_by_id and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
        # A relative phrase such as “未来半年” is not an auditable event
        # window.  Require an explicit calendar year and a month/quarter/half.
        concrete_window = bool(
            re.search(r"20\d{2}", time_window)
            and re.search(
                r"(?:-\d{1,2}(?:-\d{1,2})?|\d{1,2}\s*月|Q[1-4]|季度|上半年|下半年)",
                time_window,
                re.I,
            )
        )
        if not event or not concrete_window or not evidence_ids:
            continue
        normalized.append({
            "event": event[:200],
            "time_window": time_window[:80],
            "event_type": (
                str(candidate.get("event_type") or "company_milestone").strip()
                if str(candidate.get("event_type") or "company_milestone").strip() in {
                    "company_milestone",
                    "financial_validation",
                    "sector_mapping",
                    "conditional_watch",
                }
                else "conditional_watch"
            ),
            "why_it_matters": str(candidate.get("why_it_matters") or "").strip()[:300],
            "confidence": str(candidate.get("confidence") or "").strip()[:20],
            "verification_status": str(candidate.get("verification_status") or "").strip()[:40],
            "evidence_ids": evidence_ids,
            "sources": [
                {
                    "evidence_id": evidence_id,
                    "title": evidence_by_id[evidence_id].get("title"),
                    "date": (
                        evidence_by_id[evidence_id].get("date")
                        or evidence_by_id[evidence_id].get("time")
                    ),
                    "source": (
                        evidence_by_id[evidence_id].get("source")
                        or evidence_by_id[evidence_id].get("org")
                        or evidence_by_id[evidence_id].get("label")
                        or "公司公告"
                    ),
                    "url": evidence_by_id[evidence_id].get("url"),
                    "excerpt": evidence_by_id[evidence_id].get("excerpt"),
                }
                for evidence_id in evidence_ids
            ],
        })
    return normalized


class CatalystEventsEvaluator(BaseCriterionEvaluator):
    criterion_id = "catalyst_events"
    criterion_name = "催化事件"
    index = 5
    max_output_tokens = 1400
    # Formal forward windows are preserved deterministically when the model
    # gateway is unavailable, so a second long model retry only delays the
    # answer without improving evidence coverage.
    max_llm_attempts = 1

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Formal announcements — use the actual disclosure source, not the
        # risk-event classifier that previously mislabeled this dimension.
        try:
            announcements = ds.get_announcements(symbol, days=730, limit=100)
            ann_items = _list_of_dicts(announcements.get("items"))[:100]
            raw["announcement_events"] = [
                {
                    "evidence_id": f"A{index}",
                    "title": a.get("title", ""),
                    "date": a.get("publish_date") or a.get("date") or a.get("publish_time"),
                    "label": a.get("notice_type") or a.get("event_label"),
                    "importance": a.get("importance"),
                    "url": a.get("url"),
                }
                for index, a in enumerate(ann_items, 1)
            ]
        except Exception as exc:
            logger.warning("[catalyst] announcements failed: %s", exc)
            raw["announcement_events"] = []
            raw["announcement_events_error"] = str(exc)
            ann_items = []

        # Formal filing bodies — title metadata is not enough for a forward
        # catalyst study.  The acquisition layer reads complete periodic
        # reports, then exposes every passage with an explicit future calendar
        # window.  It does not use a catalyst keyword list to decide meaning.
        try:
            document_result = ds.get_catalyst_document_passages(symbol, ann_items)
            document_items = _list_of_dicts(document_result.get("items"))
            raw["document_events"] = [
                {
                    "evidence_id": f"D{index}",
                    "title": item.get("title"),
                    "date": item.get("date"),
                    "source": item.get("source") or "公司定期报告正文",
                    "time_window": item.get("time_window"),
                    "window_start": item.get("window_start"),
                    "window_end": item.get("window_end"),
                    "excerpt": item.get("excerpt"),
                    "url": item.get("url"),
                    "art_code": item.get("art_code"),
                }
                for index, item in enumerate(document_items, 1)
            ]
            raw["catalyst_documents"] = _list_of_dicts(document_result.get("documents"))
            if document_result.get("errors"):
                raw["document_events_error"] = "；".join(
                    str(error) for error in document_result.get("errors") or []
                )[:800]
        except Exception as exc:
            logger.warning("[catalyst] formal document bodies failed: %s", exc)
            raw["document_events"] = []
            raw["catalyst_documents"] = []
            raw["document_events_error"] = str(exc)

        # A scheduled financial report is a verification window.  It is shown
        # to the model but must not be treated as positive by itself.
        try:
            schedule_result = ds.get_report_schedule(symbol)
            schedule_items = _list_of_dicts(schedule_result.get("items"))
            raw["schedule_events"] = [
                {
                    "evidence_id": f"S{index}",
                    "title": item.get("event"),
                    "date": item.get("date"),
                    "source": item.get("source"),
                    "time_window": item.get("time_window"),
                    "report_period": item.get("report_period"),
                    "schedule_status": item.get("schedule_status"),
                    "url": item.get("url"),
                }
                for index, item in enumerate(schedule_items, 1)
            ]
            if schedule_result.get("errors"):
                raw["schedule_events_error"] = "；".join(
                    str(error) for error in schedule_result.get("errors") or []
                )[:800]
        except Exception as exc:
            logger.warning("[catalyst] report schedule failed: %s", exc)
            raw["schedule_events"] = []
            raw["schedule_events_error"] = str(exc)

        # News — raw data for LLM to judge catalyst clues
        try:
            news = ds.search_news(symbol, days=180)
            news_items = _list_of_dicts(news.get("items"))[:15]
            raw["news_events"] = [
                {
                    "evidence_id": f"N{index}",
                    "title": n.get("title", ""),
                    "source": n.get("source"),
                    "time": n.get("publish_time") or n.get("published") or n.get("date"),
                    "summary": (n.get("summary") or "")[:200],
                    "url": n.get("url") or n.get("link"),
                }
                for index, n in enumerate(news_items, 1)
            ]
        except Exception as exc:
            logger.warning("[catalyst] news failed: %s", exc)
            raw["news_events"] = []
            raw["news_events_error"] = str(exc)

        # Research reports — raw data for LLM to judge catalyst clues
        try:
            research = ds.get_research_report(symbol, days=365)
            research_items = _list_of_dicts(research.get("items"))[:10]
            raw["research_events"] = [
                {
                    "evidence_id": f"R{index}",
                    "title": r.get("title", ""),
                    "org": r.get("org"),
                    "date": r.get("publish_date"),
                    "rating": r.get("rating"),
                    "summary": (r.get("summary") or "")[:200],
                    "url": r.get("url") or r.get("link"),
                }
                for index, r in enumerate(research_items, 1)
            ]
        except Exception as exc:
            logger.warning("[catalyst] research failed: %s", exc)
            raw["research_events"] = []
            raw["research_events_error"] = str(exc)

        # Build summary for LLM prompt — show raw data
        lines = [
            "## 正式公告事件目录（元数据，不等于正文）",
        ]
        ae = raw.get("announcement_events", [])
        if ae:
            for item in ae[:16]:
                lines.append(
                    f"- [{item.get('evidence_id')}] [{item.get('date', '?')}] [{item.get('label', '?')}] "
                    f"{item.get('title', '')[:180]} {item.get('url') or ''}"
                )
        elif raw.get("announcement_events_error"):
            lines.append("- 公告数据获取失败，不能解释为没有公告或没有催化")
        else:
            lines.append("- 无公告数据")

        lines.extend(["", "## 正式定期报告正文中的未来时间窗（逐页读取后按时间结构召回，语义由你判断）"])
        de = raw.get("document_events", [])
        if de:
            for item in de[:14]:
                lines.append(
                    f"- [{item.get('evidence_id')}] [{item.get('time_window', '?')}] "
                    f"{item.get('title', '')}：{item.get('excerpt', '')} {item.get('url') or ''}"
                )
        elif raw.get("document_events_error"):
            lines.append("- 定期报告正文读取失败，不能解释为没有公司级催化")
        else:
            lines.append("- 最近两年公告目录中没有可读取的完整定期报告正文")

        lines.extend(["", "## 定期报告预约披露窗口（仅是核验节点，不自动构成利好）"])
        se = raw.get("schedule_events", [])
        if se:
            for item in se[:6]:
                lines.append(
                    f"- [{item.get('evidence_id')}] {item.get('time_window', '?')}："
                    f"{item.get('title', '')}；报告期 {item.get('report_period') or '?'}；{item.get('url') or ''}"
                )
        elif raw.get("schedule_events_error"):
            lines.append("- 预约披露数据获取失败，不能解释为没有财报核验窗口")
        else:
            lines.append("- 未来12个月暂无已预约的财报披露日期")

        lines.extend(["", "## 新闻线索（请自行判断是否涉及展会、签约、战略合作、政策窗口等催化）"])
        ne = raw.get("news_events", [])
        if ne:
            for item in ne[:10]:
                lines.append(
                    f"- [{item.get('evidence_id')}] [{item.get('time', '?')}] {item.get('source', '?')}："
                    f"{item.get('title', '')[:160]}；{item.get('summary', '')}"
                )
        elif raw.get("news_events_error"):
            lines.append("- 新闻数据获取失败，不能解释为没有新闻或没有催化")
        else:
            lines.append("- 无新闻数据")

        lines.extend(["", "## 研报表述（请自行判断是否涉及业绩拐点、技术迭代、产品发布等催化）"])
        re_ = raw.get("research_events", [])
        if re_:
            for item in re_[:8]:
                lines.append(
                    f"- [{item.get('evidence_id')}] [{item.get('date', '?')}] {item.get('org', '?')} [{item.get('rating', '?')}]"
                    f"：{item.get('title', '')[:140]}；{item.get('summary', '')}"
                )
        elif raw.get("research_events_error"):
            lines.append("- 研报数据获取失败，不能解释为零覆盖或没有催化")
        else:
            lines.append("- 无研报数据")

        lines.extend([
            "",
            "## 判断约束",
            "- 关注未来 6-12 个月内可预见的催化事件（如已知展会、政策窗口、业绩拐点、技术迭代、产品发布）。",
            "- 已完全消化的事件（利好出尽）不算有效催化。",
            "- 必须区分四类事件：公司里程碑、财务核验、板块映射、条件观察；板块事件不得冒充公司订单，预约财报本身不得冒充利好。",
            "- 请基于公告/新闻/研报的实际内容判断，不要因为标题不含关键词就忽略催化信号。",
            "- 正式报告正文出现明确投产、交付、量产或项目时间窗时，必须读取并判断，不能因最近公告标题未提及而漏掉。",
            "- 至少一个具体催化才判为通过。",
            "- 公司公告正文是优先证据；只有研报预测而没有可验证时间窗口时不得乐观通过。",
            "- 财务核验事件必须写清楚要验证的经营指标；只有披露日期、没有可验证的经营预期时，不得单独据此通过。",
            "- 数据源失败不能当作零事件；若没有至少一个可回查来源和明确未来时间窗口，必须判为不通过。",
        ])
        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return CATALYST_EVENTS

    def evaluate(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionResult:
        result = super().evaluate(symbol, stock_info, pre_fetched_data)
        normalized = normalize_catalyst_details(result.details, result.evidence.raw_data)
        result.details = {
            **result.details,
            "catalysts": normalized,
        }
        if result.passed and not normalized:
            result.passed = False
            result.verdict = (
                "模型未返回同时具备明确日历时间窗和可回查证据编号的催化事件，"
                "因此不能判定未来6—12个月催化条件通过。"
            )
        elif result.passed and not any(
            item.get("event_type") in {"company_milestone", "financial_validation"}
            for item in normalized
        ):
            result.passed = False
            result.verdict = (
                "本轮只有板块映射或条件观察事件，未核验到公司级里程碑或有经营预期支撑的财务核验窗口，"
                "因此不能判定未来6—12个月公司催化条件通过。"
            )
        return result

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        raw = evidence.raw_data
        if all(raw.get(key) for key in (
            "announcement_events_error",
            "document_events_error",
            "schedule_events_error",
            "news_events_error",
            "research_events_error",
        )):
            return "公告目录、公告正文、财报预约、新闻和研报来源均获取失败，无法验证未来6—12个月催化"
        return None


__all__ = ["CatalystEventsEvaluator", "normalize_catalyst_details"]
