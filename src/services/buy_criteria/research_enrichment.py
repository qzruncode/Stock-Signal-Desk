"""Data-driven research scope and public-source enrichment for buy analysis.

The scope is derived from the user's structured thesis and the company's latest
official segment disclosures.  It never contains stock-specific theme mappings.
Public search then covers the same fixed analyst questions for every company:
structural trend, cycle/supply-demand, competition, company position and the
current A-share narrative.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


_MATERIAL_SEGMENT_CATEGORIES = {"product", "industry"}
_GENERIC_SEGMENT_PREFIXES = ("其他", "其它", "other")
_PRIMARY_SOURCE_HOST_PARTS = (
    "gov.cn",
    "cninfo.com.cn",
    "sse.com.cn",
    "szse.cn",
    "bse.cn",
)
_PROFESSIONAL_SOURCE_HOSTS = {
    "stcn.com",
    "www.stcn.com",
    "cs.com.cn",
    "www.cs.com.cn",
    "cnstock.com",
    "www.cnstock.com",
    "yicai.com",
    "www.yicai.com",
    "cls.cn",
    "www.cls.cn",
    "finance.eastmoney.com",
    "fund.eastmoney.com",
    "finance.sina.cn",
    "finance.sina.com.cn",
    "stock.finance.sina.com.cn",
    "finance.jrj.com.cn",
    "www.zhitongcaijing.com",
}
_USER_GENERATED_SOURCE_HOSTS = {
    "xueqiu.com",
    "www.xueqiu.com",
    "caifuhao.eastmoney.com",
    "toutiao.com",
    "www.toutiao.com",
    "zhihu.com",
    "www.zhihu.com",
    "docin.com",
    "www.docin.com",
    "ai.so.com",
    "www.360kuai.com",
}
_SOURCE_QUALITY_RANK = {
    "primary": 0,
    "professional": 1,
    "unrated_public_source": 2,
    "ugc_lead_only": 3,
}
_MARKET_CONSENSUS_MAX_AGE_DAYS = 186


def _clean_label(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).strip("，,、;；")


def _source_quality(url: Any) -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        host = ""
    if any(part in host for part in _PRIMARY_SOURCE_HOST_PARTS):
        return "primary"
    if host in _PROFESSIONAL_SOURCE_HOSTS:
        return "professional"
    if host in _USER_GENERATED_SOURCE_HOSTS:
        return "ugc_lead_only"
    return "unrated_public_source"


def _published_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _is_current_market_consensus(item: dict[str, Any]) -> bool:
    timestamp = _published_timestamp(item.get("published_date"))
    if timestamp <= 0:
        return True
    age_days = (datetime.now(timezone.utc).timestamp() - timestamp) / 86_400
    return age_days <= _MARKET_CONSENSUS_MAX_AGE_DAYS


def _requested_subjects(stock_info: dict[str, Any]) -> list[dict[str, Any]]:
    context = stock_info.get("_investment_thesis_context")
    context = context if isinstance(context, dict) else {}
    values: list[tuple[str, str]] = []
    for domain in context.get("domains") or []:
        if not isinstance(domain, dict):
            continue
        values.append((_clean_label(domain.get("label")), "structured_domain"))
        values.extend((_clean_label(item), "resolved_board") for item in domain.get("board_queries") or [])
    values.append((_clean_label(context.get("summary")), "thesis_summary"))
    values.append((_clean_label(stock_info.get("_investment_thesis")), "user_thesis"))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, basis in values:
        normalized = label.lower()
        if len(label) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        result.append({"label": label[:100], "basis": basis})
    return result


def derive_research_scope(
    stock_info: dict[str, Any],
    company_packet: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive material research subjects without guessing from the stock name."""
    packet = company_packet if isinstance(company_packet, dict) else {}
    segments = packet.get("business_segments")
    segments = segments if isinstance(segments, dict) else {}
    rows = [item for item in segments.get("items") or [] if isinstance(item, dict)]
    latest_report = max(
        (str(item.get("report_date") or "") for item in rows),
        default="",
    )
    ranked: list[dict[str, Any]] = []
    for item in rows:
        category = str(item.get("category") or "").strip().lower()
        label = _clean_label(item.get("segment_name"))
        if category not in _MATERIAL_SEGMENT_CATEGORIES:
            continue
        if latest_report and str(item.get("report_date") or "") != latest_report:
            continue
        if len(label) < 2 or label.lower().startswith(_GENERIC_SEGMENT_PREFIXES):
            continue
        share = item.get("revenue_share_pct")
        try:
            share_value = float(share) if share is not None else None
        except (TypeError, ValueError):
            share_value = None
        ranked.append(
            {
                "label": label[:100],
                "basis": f"official_{category}_segment",
                "report_date": str(item.get("report_date") or "") or None,
                "revenue_share_pct": share_value,
                "source_url": segments.get("source_url"),
            }
        )
    ranked.sort(
        key=lambda item: (
            item.get("revenue_share_pct") is not None,
            item.get("revenue_share_pct") or 0,
        ),
        reverse=True,
    )

    business_subjects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        normalized = item["label"].lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        business_subjects.append(item)
        if len(business_subjects) >= 4:
            break

    if not business_subjects:
        for key, basis in (
            ("product_name", "company_profile_product"),
            ("product_type", "company_profile_product_type"),
            ("industry", "declared_industry"),
        ):
            label = _clean_label(stock_info.get(key))
            normalized = label.lower()
            if len(label) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            business_subjects.append({"label": label[:100], "basis": basis})
            if len(business_subjects) >= 3:
                break

    requested = _requested_subjects(stock_info)
    requested_domains = [item for item in requested if item.get("basis") != "thesis_summary"]
    thesis_summaries = [item for item in requested if item.get("basis") == "thesis_summary"]
    primary: list[dict[str, Any]] = []
    primary_seen: set[str] = set()
    for item in [*requested_domains, *business_subjects, *thesis_summaries]:
        normalized = str(item.get("label") or "").lower()
        if not normalized or normalized in primary_seen:
            continue
        primary_seen.add(normalized)
        primary.append(item)
        if len(primary) >= 4:
            break

    return {
        "requested_subjects": requested,
        "official_business_subjects": business_subjects,
        "primary_subjects": primary,
        "primary_labels": [str(item["label"]) for item in primary],
        "latest_segment_report": latest_report or None,
        "derivation": ("structured_user_thesis_then_latest_official_material_segments"),
    }


_RESEARCH_LENSES: tuple[tuple[str, str], ...] = (
    (
        "structural_trend",
        "产业趋势 中长期需求 政策 技术路线 资本开支",
    ),
    (
        "cycle_supply_demand",
        "供需 景气 订单 出货 价格 库存 产能利用率",
    ),
    (
        "competition_structure",
        "竞争格局 市场份额 价格 产能 技术壁垒 客户",
    ),
)


def _search_one(
    lens: str,
    subject: str,
    focus: str,
    company_name: str,
) -> dict[str, Any]:
    from src.tools.websearch import websearch

    year = datetime.now().astimezone().year
    if lens == "company_position":
        query = f"{company_name} {subject} {year} 市场地位 技术 产品 客户 产能"
    elif lens == "market_consensus":
        query = focus or (f"{year} A股 近三个月 中期市场主线 主导产业趋势 当前市场叙事")
    else:
        query = f"{subject} {year} {focus}"
    result = websearch(
        query,
        num_results=5,
        livecrawl="fallback",
        search_type="deep",
        context_max_characters=8_000,
        include_content=False,
    )
    items = [
        {
            "lens": lens,
            "subject": subject or None,
            "query": query,
            "title": item.get("title"),
            "url": item.get("url"),
            "snippet": str(item.get("snippet") or "")[:1_000],
            "source": item.get("source"),
            "published_date": item.get("published_date"),
            "search_provider": item.get("search_provider"),
            "source_quality": _source_quality(item.get("url")),
        }
        for item in result.get("results") or []
        if isinstance(item, dict) and item.get("url")
    ]
    return {
        "lens": lens,
        "subject": subject or None,
        "query": query,
        "status": (
            "retrieved" if items else "retrieval_failed" if result.get("errors") else "no_matching_public_material"
        ),
        "items": items,
        "provider": result.get("provider"),
        "errors": result.get("errors") or [],
        "warnings": result.get("warnings") or [],
        "retrieved_at": result.get("retrieved_at"),
    }


def collect_public_research(
    stock_info: dict[str, Any],
    *,
    requested_lenses: set[str] | None = None,
) -> dict[str, Any]:
    """Fill predictable research blind spots before any analyst judgment."""
    scope = stock_info.get("_derived_research_scope")
    scope = scope if isinstance(scope, dict) else {}
    subjects = [str(item or "").strip() for item in scope.get("primary_labels") or [] if str(item or "").strip()][:2]
    company_name = _clean_label(stock_info.get("name") or stock_info.get("short_name") or stock_info.get("symbol"))
    now = datetime.now().astimezone()
    active_lenses = requested_lenses or {
        "market_consensus",
        "structural_trend",
        "cycle_supply_demand",
        "competition_structure",
        "company_position",
    }
    requests: list[tuple[str, str, str, str]] = []
    if "market_consensus" in active_lenses:
        requests.extend(
            [
                (
                    "market_consensus",
                    "机构策略",
                    f"{now:%Y年%m月} A股 近三个月 券商策略 市场主线 机构观点",
                    company_name,
                ),
                (
                    "market_consensus",
                    "市场复盘",
                    f"{now:%Y年%m月%d日} A股 市场复盘 热点主线 成交结构",
                    company_name,
                ),
                (
                    "market_consensus",
                    "中期主线细分",
                    f"{now:%Y年%m月} A股 中期主线 产业方向 券商策略",
                    company_name,
                ),
            ]
        )
    for subject in subjects:
        requests.extend(
            (lens, subject, focus, company_name) for lens, focus in _RESEARCH_LENSES if lens in active_lenses
        )
        if "company_position" in active_lenses:
            requests.append(("company_position", subject, "", company_name))

    attempts: list[dict[str, Any]] = []
    if requests:
        with ThreadPoolExecutor(max_workers=min(6, len(requests))) as pool:
            futures = {pool.submit(_search_one, *request): request for request in requests}
            for future in as_completed(futures):
                request = futures[future]
                try:
                    attempts.append(future.result())
                except Exception as exc:
                    attempts.append(
                        {
                            "lens": request[0],
                            "subject": request[1] or None,
                            "query": "",
                            "status": "retrieval_failed",
                            "items": [],
                            "errors": [f"{type(exc).__name__}: {str(exc)[:240]}"],
                            "warnings": [],
                        }
                    )
    attempts.sort(
        key=lambda item: (
            str(item.get("lens") or ""),
            str(item.get("subject") or ""),
        )
    )
    items = [item for attempt in attempts for item in attempt.get("items") or [] if isinstance(item, dict)]
    lens_status: dict[str, str] = {}
    for lens in (
        "market_consensus",
        "structural_trend",
        "cycle_supply_demand",
        "competition_structure",
        "company_position",
    ):
        if lens not in active_lenses:
            continue
        relevant = [item for item in attempts if item.get("lens") == lens]
        if any(item.get("status") == "retrieved" for item in relevant):
            lens_status[lens] = "retrieved"
        elif any(item.get("status") == "retrieval_failed" for item in relevant):
            lens_status[lens] = "retrieval_failed"
        else:
            lens_status[lens] = "no_matching_public_material"
    # Bound each lens independently so alphabetical attempt ordering can never
    # crowd an entire analyst question out of the model packet.
    balanced_items: list[dict[str, Any]] = []
    for lens in (
        "market_consensus",
        "structural_trend",
        "cycle_supply_demand",
        "competition_structure",
        "company_position",
    ):
        candidates = [
            candidate
            for candidate in items
            if candidate.get("lens") == lens and (lens != "market_consensus" or _is_current_market_consensus(candidate))
        ]
        candidates.sort(
            key=lambda item: (
                _SOURCE_QUALITY_RANK.get(
                    str(item.get("source_quality") or ""),
                    9,
                ),
                -_published_timestamp(item.get("published_date")),
                str(item.get("subject") or ""),
            )
        )
        balanced_items.extend(candidates[:5])
    return {
        "scope": scope,
        "attempts": attempts,
        "items": balanced_items,
        "lens_status": lens_status,
        "retrieved_source_count": len({str(item.get("url") or "") for item in items if item.get("url")}),
        "retrieval_complete": all(status != "retrieval_failed" for status in lens_status.values()),
        "method": "data_driven_scope_with_multi_source_public_search",
    }


def research_summary_for_lenses(
    enrichment: dict[str, Any],
    lenses: set[str],
) -> str:
    """Render a bounded, source-linked supplement for selected dimensions."""
    statuses = enrichment.get("lens_status")
    statuses = statuses if isinstance(statuses, dict) else {}
    lines = [
        "## 自动补全的公开研究",
        "- 研究范围来自用户结构化主题与公司最新正式分部披露，不从股票名称猜题材。",
        "- 检索失败表示本轮取证未完成；有效空结果表示所查公开来源未提供匹配材料，二者不得混写。",
        "- source_quality=ugc_lead_only 的内容只能作为继续核验的线索，不能单独建立事实或结论；正式披露和专业来源优先。",
    ]
    for lens in sorted(lenses):
        lines.append(f"- {lens} 检索状态：{statuses.get(lens) or 'not_requested'}")
    selected = [item for item in enrichment.get("items") or [] if isinstance(item, dict) and item.get("lens") in lenses]
    for item in selected[:18]:
        lines.append(
            f"- [{item.get('lens')}] [{item.get('subject') or '全市场'}] "
            f"[{item.get('source_quality') or 'unrated_public_source'}] "
            f"{item.get('published_date') or '日期未标注'} "
            f"{item.get('source') or '公开来源'}：{item.get('title') or '无标题'}；"
            f"{str(item.get('snippet') or '')[:360]} {item.get('url') or ''}"
        )
    if not selected:
        lines.append("- 本轮没有取得可展示的匹配材料。")
    return "\n".join(lines)


__all__ = [
    "collect_public_research",
    "derive_research_scope",
    "research_summary_for_lenses",
]
