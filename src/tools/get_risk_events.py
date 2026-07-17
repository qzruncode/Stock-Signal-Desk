"""Evidence-first company risk-event aggregation for the Stock Agent."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.tools._akshare import bare_symbol
from src.tools.base import ToolSpec, object_schema


_MITIGATION_PHRASES = (
    "解除质押",
    "解除冻结",
    "撤销风险警示",
    "撤回减持",
    "终止减持",
    "减持完毕",
    "减持完成",
    "撤诉",
    "和解",
    "已偿还",
    "完成整改",
    "回复问询函",
    "问询函回复",
    "未触及退市",
)

_RISK_RULES: tuple[tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (
        "regulatory",
        "监管执法",
        (
            ("high", ("立案调查", "证监会立案", "行政处罚", "重大违法", "财务造假", "欺诈发行")),
            ("medium", ("监管函", "警示函", "问询函", "纪律处分", "通报批评", "责令改正")),
        ),
    ),
    (
        "delisting",
        "退市与风险警示",
        (
            ("high", ("终止上市", "退市风险警示", "可能被终止上市", "强制退市")),
            ("medium", ("其他风险警示", "被实施st", "被实施*st")),
        ),
    ),
    (
        "litigation",
        "诉讼与资产受限",
        (
            ("high", ("重大诉讼", "重大仲裁", "司法冻结", "轮候冻结", "查封", "强制执行")),
            ("medium", ("诉讼", "仲裁", "被告", "法律纠纷")),
        ),
    ),
    (
        "debt_liquidity",
        "债务与流动性",
        (
            ("high", ("债务逾期", "实质性违约", "不能清偿", "无力偿还", "破产重整", "流动性危机")),
            ("medium", ("流动性紧张", "偿债压力", "逾期票据", "违规担保")),
        ),
    ),
    (
        "earnings_asset_quality",
        "业绩与资产质量",
        (
            ("high", ("预亏", "首亏", "大额亏损", "商誉减值", "大幅下滑", "业绩预告下修")),
            ("medium", ("同比下降", "净利润下降", "资产减值", "计提减值", "业绩下修")),
        ),
    ),
    (
        "shareholder_financing",
        "股东质押与减持",
        (
            ("high", ("强制平仓", "被动减持", "爆仓", "清仓式减持", "高比例质押")),
            ("medium", ("股份质押", "股权质押", "补充质押", "减持计划", "拟减持")),
        ),
    ),
    (
        "governance_audit",
        "治理与审计",
        (
            ("high", ("无法表示意见", "否定意见", "无法保证真实", "董事长失联")),
            ("medium", ("保留意见", "非标准审计意见", "内部控制缺陷", "内控缺陷")),
            ("low", ("董事辞职", "监事辞职", "高管辞职")),
        ),
    ),
    (
        "operations",
        "生产经营",
        (
            ("high", ("重大事故", "安全生产事故", "重大火灾")),
            ("medium", ("停产", "停工", "产品召回", "环保处罚")),
        ),
    ),
)

_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


def _classify_risk_event(text: str, *, source_kind: str = "unknown") -> dict[str, Any] | None:
    del source_kind
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return None

    mitigations = [phrase for phrase in _MITIGATION_PHRASES if phrase in normalized]
    # “说明相关问题”也可能只是问询函要求公司后续说明，并不代表公司已经
    # 回复。只有文本明确出现回复/答复动作时，才把问询事件标为已缓释。
    if "问询函" in normalized and any(word in normalized for word in ("回复", "答复")):
        mitigations.append("问询函回复或答复")
    candidates: list[dict[str, Any]] = []
    for category, label, levels in _RISK_RULES:
        for severity, phrases in levels:
            hits = [phrase for phrase in phrases if phrase in normalized]
            if not hits:
                continue
            candidates.append({
                "risk_category": category,
                "risk_label": label,
                "severity": severity,
                "tags": hits,
            })
            break

    if not candidates and mitigations:
        category = "shareholder_financing" if any("质押" in phrase or "减持" in phrase for phrase in mitigations) else "resolved_risk"
        label = "股东质押与减持" if category == "shareholder_financing" else "风险缓释进展"
        return {
            "risk_category": category,
            "risk_label": label,
            "severity": "low",
            "status": "mitigated",
            "tags": mitigations[:8],
        }
    if not candidates:
        return None

    candidates.sort(key=lambda item: _SEVERITY_RANK[item["severity"]], reverse=True)
    selected = candidates[0]
    tags = list(dict.fromkeys([*selected["tags"], *mitigations]))[:8]
    if mitigations:
        selected = {**selected, "severity": "low", "status": "mitigated", "tags": tags}
    else:
        selected = {**selected, "status": "active", "tags": tags}
    return selected


def _date_value(item: dict[str, Any]) -> str | None:
    value = str(item.get("published") or item.get("publish_date") or item.get("date") or "").strip()
    return value[:10] or None


def _evidence_text(item: dict[str, Any]) -> tuple[str, str, str]:
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    summary = re.sub(r"\s+", " ", str(item.get("summary") or item.get("source_notice_type") or "")).strip()
    return title, summary, f"{title} {summary}".strip()


def _risk_item(item: dict[str, Any], *, source_type: str) -> dict[str, Any] | None:
    title, summary, evidence = _evidence_text(item)
    risk = _classify_risk_event(evidence, source_kind=source_type)
    if risk is None:
        return None
    title_hits = [tag for tag in risk["tags"] if tag in title]
    confidence = "high" if title_hits else "medium" if summary else "low"
    return {
        "title": title,
        "date": _date_value(item),
        "source": item.get("source") or ("公司公告" if source_type == "announcement" else "新闻"),
        "source_type": source_type,
        "url": item.get("url") or item.get("link") or "",
        "severity": risk["severity"],
        "status": risk["status"],
        "risk_category": risk["risk_category"],
        "risk_label": risk["risk_label"],
        "risk_summary": summary[:280] or title,
        "tags": risk["tags"],
        "confidence": confidence,
        "evidence_basis": "title_and_available_summary",
        "requires_fulltext_verification": risk["severity"] == "high" or confidence != "high",
        "classification_method": "deterministic_risk_phrase_rules",
    }


def get_risk_events(symbol: str, days: int = 90, limit: int = 30) -> dict[str, Any]:
    code = bare_symbol(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"无法识别 A 股代码: {symbol}")
    days = int(days)
    limit = int(limit)
    if not 1 <= days <= 730:
        raise ValueError("days 必须在 1 到 730 之间")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    from src.tools.get_announcements import get_announcements
    from src.tools.search_news import search_news

    news = search_news(code, days=min(days, 365), limit=min(max(limit * 2, 20), 50))
    announcements = get_announcements(code, days=days, type="all", limit=min(max(limit * 2, 30), 100))

    evidence: list[dict[str, Any]] = []
    for item in news.get("items") or []:
        normalized = _risk_item(item, source_type="news")
        if normalized:
            evidence.append(normalized)
    for item in announcements.get("items") or []:
        normalized = _risk_item(item, source_type="announcement")
        if normalized:
            evidence.append(normalized)

    # The same formal announcement is often syndicated as news.  Prefer the
    # announcement copy because it is the primary evidence.
    evidence.sort(
        key=lambda item: (
            item.get("date") or "",
            1 if item.get("source_type") == "announcement" else 0,
            _SEVERITY_RANK.get(str(item.get("severity")), 0),
        ),
        reverse=True,
    )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        key = re.sub(r"\s+", "", f"{item.get('title', '')}|{item.get('date', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    items = deduped[:limit]

    severity_distribution = {level: 0 for level in ("high", "medium", "low")}
    status_distribution: dict[str, int] = {}
    category_distribution: dict[str, int] = {}
    for item in items:
        severity_distribution[item["severity"]] += 1
        status_distribution[item["status"]] = status_distribution.get(item["status"], 0) + 1
        category_distribution[item["risk_label"]] = category_distribution.get(item["risk_label"], 0) + 1

    news_ok = bool(news.get("success"))
    announcements_ok = bool(announcements.get("success"))
    acquisition_succeeded = news_ok or announcements_ok
    partial = acquisition_succeeded and not (news_ok and announcements_ok)
    errors = list(dict.fromkeys([
        *[str(error) for error in news.get("errors") or []],
        *[str(error) for error in announcements.get("errors") or []],
    ]))
    warnings = list(dict.fromkeys([
        *[str(warning) for warning in news.get("warnings") or []],
        *[str(warning) for warning in announcements.get("warnings") or []],
    ]))
    if not items and acquisition_succeeded:
        warnings.append(f"在最近 {days} 天已获取的新闻和公告样本中未识别到规则覆盖的风险事件")
    latest = next((item.get("date") for item in items if item.get("date")), None)
    name = announcements.get("name") or news.get("name")
    return {
        "symbol": code,
        "name": name,
        "days": days,
        "limit": limit,
        "items": items,
        "item_count": len(items),
        "has_risk_events": bool(items),
        "analysis": {
            "severity_distribution": severity_distribution,
            "status_distribution": status_distribution,
            "risk_category_distribution": dict(sorted(category_distribution.items(), key=lambda pair: (-pair[1], pair[0]))),
            "active_high_severity_count": sum(1 for item in items if item["severity"] == "high" and item["status"] == "active"),
            "coverage": {
                "requested_days": days,
                "news_coverage_days": min(days, 365),
                "announcement_coverage_days": days,
                "news_sample_count": len(news.get("items") or []),
                "announcement_sample_count": len(announcements.get("items") or []),
            },
        },
        "source": "search_news + get_announcements",
        "source_chain": list(dict.fromkeys([
            *([str(news.get("source"))] if news.get("source") else []),
            *[str(source) for source in announcements.get("source_chain") or []],
        ])),
        "source_scope": "deterministic_screening_of_retrieved_news_and_formal_announcements",
        "success": acquisition_succeeded,
        "partial": partial,
        "data_time": latest,
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "is_stale": False if latest else None,
        "freshness_unknown": latest is None,
        "fallback_used": bool(news.get("fallback_used") or announcements.get("fallback_used")),
        "fallback_recommended": not acquisition_succeeded,
        "errors": errors[:10],
        "warnings": warnings[:10],
    }


TOOL = ToolSpec(
    name="get_risk_events",
    description=(
        "从已获取的公司新闻与正式公告中筛选风险事件证据，区分监管、退市、诉讼、债务、"
        "业绩、质押减持、治理和经营风险，并标注 active 或 mitigated。结果是可追溯的规则筛查，"
        "不是对公司整体风险的最终结论；高风险项应按 URL 继续核验原文。"
    ),
    parameters=object_schema({
        "symbol": {"type": "string", "description": "A 股代码或名称"},
        "days": {"type": "integer", "minimum": 1, "maximum": 730, "default": 90},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
    }, ["symbol"]),
    executor=get_risk_events,
    category="risk",
)
