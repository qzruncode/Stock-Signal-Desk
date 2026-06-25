# -*- coding: utf-8 -*-
"""Shared utility functions for financials package."""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Symbol / type conversion helpers
# ---------------------------------------------------------------------------

def _to_em_symbol(symbol: str) -> str:
    """Convert plain symbol to East Money format with market prefix.

    SH: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx
    SZ: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx, 301xxx
    BJ: 8xxxxx, 9xxxxx
    """
    code = symbol.strip()
    if code.startswith(('SH', 'SZ', 'BJ')):
        return code
    if code[0] in ('6', '9'):
        return f"SH{code}"
    return f"SZ{code}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip().replace(",", "").replace("%", "")
        if not val or val.lower() in ('false', 'none', 'nan', '-'):
            return None
    try:
        v = float(val)
    except (ValueError, TypeError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _safe_amount(val) -> Optional[float]:
    """Parse amount string like '747.34亿', '1.47亿', '628.00万' to float (in 元)."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ('false', 'none', 'nan', '-'):
        return None
    multiplier = 1.0
    if '亿' in s:
        s = s.replace('亿', '')
        multiplier = 1e8
    elif '万' in s:
        s = s.replace('万', '')
        multiplier = 1e4
    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return None


def _safe_str(val) -> str:
    if val is None:
        return ""
    text = str(val).strip()
    return "" if text in ("", "-", "nan", "None", "NaT") else text


def _normalize_symbol(symbol: str) -> str:
    code = symbol.strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            return code[2:]
    return code


def _to_ts_code(symbol: str) -> str:
    code = _normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _to_top_holder_symbol(symbol: str) -> str:
    return _to_em_symbol(_normalize_symbol(symbol)).lower()


def _pick_col(columns, keywords: list[str], *, exclude: list[str] | None = None) -> Any:
    exclude = exclude or []
    for col in columns:
        col_s = str(col)
        if any(k.lower() in col_s.lower() for k in keywords) and not any(
            x.lower() in col_s.lower() for x in exclude
        ):
            return col
    return None


def _row_pick(row, keywords: list[str], *, exclude: list[str] | None = None) -> Any:
    col = _pick_col(row.index, keywords, exclude=exclude)
    if col is None:
        return None
    return row.get(col)


def _parse_date(val) -> Optional[datetime]:
    if val is None:
        return None
    try:
        import pandas as pd
        parsed = pd.to_datetime(val)
    except Exception:
        return None
    if parsed is None:
        return None
    try:
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()
    except Exception:
        return None


def _latest_quarter_dates(limit: int = 8) -> list[str]:
    today = datetime.now().date()
    candidates: list[str] = []
    for year in range(today.year, today.year - 4, -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            d = datetime(year, month, day).date()
            if d <= today:
                candidates.append(d.strftime("%Y%m%d"))
    return candidates[:limit]


def _safe_pct(val) -> Optional[float]:
    """Parse percentage string like '23.38%' to float 23.38."""
    if val is None:
        return None
    s = str(val).strip().replace("%", "")
    if not s or s.lower() in ('false', 'none', 'nan', '-'):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None



# ---------------------------------------------------------------------------
# Additional helpers used by external callers
# ---------------------------------------------------------------------------

def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _safe_int_like(val) -> Optional[int]:
    num = _safe_float(val)
    return int(num) if num is not None else None


def _parse_chinese_share_amount(value: Any) -> Optional[float]:
    text = _safe_str(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return _safe_float(text)
    amount = float(match.group(1))
    if "亿" in text:
        amount *= 1e8
    elif "万" in text:
        amount *= 1e4
    return amount


# ---------------------------------------------------------------------------
# RSSHub helpers (shared by news, announcements, risk events, sentiment, research, social)
# ---------------------------------------------------------------------------

def _rss_stock_keywords(code: str) -> list[str]:
    keywords = [code]
    try:
        from src.data.stock_index_loader import get_index_stock_name

        stock_name = get_index_stock_name(code)
        if stock_name:
            keywords.append(stock_name)
            normalized_name = stock_name.replace("Ａ", "A").replace("Ｂ", "B")
            if normalized_name != stock_name:
                keywords.append(normalized_name)
    except Exception as exc:
        logger.debug("[RSSHub] stock name lookup failed for %s: %s", code, exc)

    deduped = []
    seen_keywords = set()
    for keyword in keywords:
        value = _safe_str(keyword)
        if value and value not in seen_keywords:
            seen_keywords.add(value)
            deduped.append(value)
    return deduped


def _rss_stock_industry_keywords(code: str) -> list[str]:
    keywords: list[str] = []
    try:
        import akshare as ak

        df = ak.stock_individual_info_em(symbol=code, timeout=10)
        if df is not None and not df.empty:
            info_map = {str(row.get("item", "")): row.get("value") for _, row in df.iterrows()}
            industry = _safe_str(info_map.get("行业"))
            if industry:
                keywords.append(industry)
    except Exception as exc:
        logger.debug("[RSSHub] stock industry lookup failed for %s: %s", code, exc)

    stock_name = next((kw for kw in _rss_stock_keywords(code) if kw != code), "")
    fallback_map = {
        "中金岭南": ["有色金属", "铅锌", "锌", "铅"],
        "贵州茅台": ["白酒", "食品饮料"],
        "平安银行": ["银行"],
        "宁德时代": ["电池", "新能源"],
    }
    keywords.extend(fallback_map.get(stock_name, []))

    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        value = _safe_str(keyword)
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _normalize_rss_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", "", _safe_str(value))
    return text.replace("Ａ", "A").replace("Ｂ", "B").upper()


def _rss_entry_matches_keywords(entry: dict, keywords: list[str]) -> bool:
    text = " ".join(
        _normalize_rss_text(entry.get(field))
        for field in ("title", "summary", "author", "link")
    )
    return any(_normalize_rss_text(keyword) in text for keyword in keywords)


def _rss_entry_is_recent(entry: dict, cutoff: datetime) -> bool:
    value = entry.get("published")
    if not value:
        return True
    parsed = _parse_date(value)
    if parsed is None:
        return True
    if getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed >= cutoff


def _rss_stock_feed_specs(keywords: list[str]) -> list[tuple[str, dict, str, bool]]:
    feed_specs: list[tuple[str, dict, str, bool]] = []
    for keyword in keywords:
        feed_specs.append((
            "eastmoney_search",
            {"keyword": keyword},
            f"东方财富搜索:{keyword}",
            True,
        ))
    feed_specs.extend([
        ("cls", {"category": "telegraph"}, "财联社电报", True),
        ("cls", {"category": "depth"}, "财联社深度", True),
        ("wallstreetcn_live", {}, "华尔街见闻实时快讯", True),
        ("wallstreetcn", {"category": "shares"}, "华尔街见闻股市", True),
        ("wallstreetcn_hot", {}, "华尔街见闻热门", True),
        ("sina_roll", {"category": "2517"}, "新浪股市滚动", True),
        ("sina_roll", {"category": "2516"}, "新浪财经滚动", True),
        ("sina_finance", {"category": "rollnews"}, "新浪财经频道", True),
        ("yicai", {"category": "brief"}, "第一财经快讯", True),
        ("yicai", {"category": "latest"}, "第一财经最新", True),
        ("yicai", {"category": "news"}, "第一财经新闻", True),
        ("36kr", {"category": "newsflashes"}, "36氪快讯", True),
        ("36kr", {"category": "information/web_news"}, "36氪网页新闻", True),
    ])
    return feed_specs


def _rsshub_is_slow_spec(spec: tuple[str, dict, str, bool]) -> bool:
    source_id, params, _, _ = spec
    category = _safe_str(params.get("category"))
    keyword = _safe_str(params.get("keyword"))

    if source_id == "36kr" and category == "information/web_news":
        return True
    if source_id == "yicai" and category in {"latest", "news"}:
        return True
    if source_id == "sina_finance" and category == "rollnews":
        return True
    if source_id == "cls" and category == "depth":
        return True
    if source_id == "wallstreetcn_hot":
        return True
    if source_id == "eastmoney_search" and keyword and len(keyword) > 6:
        return True
    return False


def _rsshub_enough_entries(entries: list[dict], target: int) -> bool:
    if len(entries) < target:
        return False
    return len(_dedupe_rss_entries(entries)) >= target


def _fetch_rsshub_entries(
    code: str,
    days: int,
    feed_specs: list[tuple[str, dict, str, bool]],
    *,
    keywords: Optional[list[str]] = None,
    limit: int = 50,
    max_workers: int = 8,
    timeout: float = 6.0,
    target_items: int = 8,
) -> tuple[list[dict], list[str], list[str]]:
    errors: list[str] = []
    entries: list[dict] = []
    cutoff = datetime.now() - timedelta(days=days)
    keywords = keywords or _rss_stock_keywords(code)

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from api.v1.endpoints.rss import _build_feed_url, _fetch_rss_feed

        def _fetch_one(spec: tuple[str, dict, str, bool], phase_timeout: float) -> tuple[str, bool, dict]:
            source_id, params, label, require_match = spec
            feed_url = _build_feed_url(source_id, **params)
            return label, require_match, _fetch_rss_feed(feed_url, limit=limit, timeout=phase_timeout)

        def _collect(specs: list[tuple[str, dict, str, bool]], *, phase_timeout: float, phase_workers: int) -> None:
            if not specs:
                return
            with ThreadPoolExecutor(max_workers=max(1, min(phase_workers, len(specs) or 1))) as pool:
                futures = {pool.submit(_fetch_one, spec, phase_timeout): spec for spec in specs}
                for future in as_completed(futures):
                    _, _, label, _ = futures[future]
                    try:
                        fetched_label, require_match, rss_result = future.result()
                    except Exception as exc:
                        errors.append(f"RSSHub {label}: {exc}")
                        logger.warning("[RSSHub] feed failed for %s/%s: %s", code, label, exc)
                        continue

                    for error in rss_result.get("errors", []) or []:
                        errors.append(f"RSSHub {fetched_label}: {error}")

                    for entry in rss_result.get("items", []) or []:
                        if not _rss_entry_is_recent(entry, cutoff):
                            continue
                        if require_match and not _rss_entry_matches_keywords(entry, keywords):
                            continue
                        entry = dict(entry)
                        entry["_rss_source_id"] = futures[future][0]
                        entry["_rss_source_label"] = fetched_label
                        entries.append(entry)

        fast_specs = [spec for spec in feed_specs if not _rsshub_is_slow_spec(spec)]
        slow_specs = [spec for spec in feed_specs if _rsshub_is_slow_spec(spec)]

        _collect(
            fast_specs,
            phase_timeout=max(1.5, min(timeout, 4.0)),
            phase_workers=max(1, min(max_workers, 4)),
        )
        if slow_specs and not _rsshub_enough_entries(entries, target_items):
            _collect(
                slow_specs,
                phase_timeout=max(1.5, min(timeout, 2.5)),
                phase_workers=max(1, min(max_workers, 2)),
            )
    except Exception as exc:
        errors.append(f"RSSHub 聚合: {exc}")
        logger.warning("[RSSHub] aggregate failed for %s: %s", code, exc)

    return entries, keywords, errors


def _rss_entry_text(entry: dict) -> str:
    return re.sub(r"<[^>]+>", "", _safe_str(entry.get("title") or entry.get("summary")))


def _rss_entry_summary(entry: dict, limit: int = 180) -> str:
    text = re.sub(r"<[^>]+>", "", _safe_str(entry.get("summary")))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _rss_entry_date(entry: dict) -> Optional[datetime]:
    parsed = _parse_date(entry.get("published"))
    if parsed and getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _dedupe_rss_entries(entries: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for entry in entries:
        key = re.sub(r"\s+", "", _rss_entry_text(entry)).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    unique.sort(key=lambda item: _rss_entry_date(item) or datetime.min, reverse=True)
    return unique



# ---------------------------------------------------------------------------
# Structured analysis + content freshness (shared)
# ---------------------------------------------------------------------------

def _lazy_classify_sentiment(text: str) -> float:
    """Lazy import wrapper to avoid circular dependency with _fetch_sentiment."""
    from ._fetch_sentiment import _classify_sentiment
    return _classify_sentiment(text)


def _classify_financial_text(text: str) -> dict:
    normalized = _safe_str(text)
    event_rules = [
        ("earnings", "业绩", ("业绩", "净利润", "营收", "年报", "半年报", "季报", "预告", "快报", "盈利", "亏损")),
        ("capital_action", "资本动作", ("增发", "定增", "发行", "并购", "收购", "重组", "资产", "投资", "募资")),
        ("shareholder", "股东变化", ("股东", "增持", "减持", "回购", "质押", "解押")),
        ("governance", "治理变动", ("董事", "监事", "高管", "总经理", "财务总监", "辞职", "聘任", "变更")),
        ("risk", "风险监管", ("问询", "监管", "处罚", "诉讼", "仲裁", "违规", "退市", "立案", "风险")),
        ("market", "市场交易", ("涨停", "跌停", "大宗交易", "龙虎榜", "主力资金", "净流入", "净流出")),
        ("research", "研究评级", ("研报", "评级", "买入", "增持", "中性", "减持", "卖出", "盈利预测", "目标价")),
        ("industry", "行业主题", ("行业", "板块", "景气", "周期", "需求", "供给", "价格")),
    ]
    event_type = "general"
    event_label = "一般资讯"
    tags: list[str] = []
    for key, label, words in event_rules:
        matched = [word for word in words if word in normalized]
        if matched:
            if event_type == "general":
                event_type = key
                event_label = label
            tags.extend(matched[:3])

    score = _lazy_classify_sentiment(normalized) if normalized else 0.0
    polarity = "positive" if score > 0.01 else ("negative" if score < -0.01 else "neutral")
    high_words = ("重大", "终止", "停牌", "复牌", "退市", "处罚", "立案", "亏损", "预增", "预减", "收购", "重组", "分红", "回购")
    medium_words = ("公告", "业绩", "评级", "增持", "减持", "资金", "问询", "诉讼", "投资")
    importance = "high" if any(word in normalized for word in high_words) else (
        "medium" if any(word in normalized for word in medium_words) else "low"
    )
    return {
        "event_type": event_type,
        "event_label": event_label,
        "polarity": polarity,
        "sentiment_score": round(score, 3),
        "importance": importance,
        "tags": list(dict.fromkeys(tags))[:8],
    }


def _build_structured_analysis(items: list[dict], *, days: int, dimension: str, source_key: str = "source") -> dict:
    from collections import Counter, defaultdict

    source_counter = Counter(_safe_str(item.get(source_key)) or "未知" for item in items)
    notice_type_counter = Counter(_safe_str(item.get("notice_type")) or "未分类" for item in items if _safe_str(item.get("notice_type")))
    event_counter = Counter(_safe_str(item.get("event_type")) or _safe_str(item.get("notice_type")) or _safe_str(item.get("category")) or "general" for item in items)
    polarity_counter = Counter(_safe_str(item.get("polarity") or item.get("label")) or "neutral" for item in items)
    importance_counter = Counter(_safe_str(item.get("importance")) or "low" for item in items)
    daily_counter: dict[str, int] = defaultdict(int)
    for item in items:
        date_value = (
            _safe_str(item.get("publish_date"))
            or _safe_str(item.get("publish_time"))[:10]
            or _safe_str(item.get("date_str"))
        )
        if date_value:
            daily_counter[date_value[:10]] += 1

    key_events = [
        {
            "title": item.get("title", ""),
            "date": item.get("publish_date") or _safe_str(item.get("publish_time"))[:10] or item.get("date_str"),
            "source": item.get(source_key) or item.get("org") or "未知",
            "event_type": item.get("event_type") or item.get("notice_type") or item.get("category") or "general",
            "polarity": item.get("polarity") or item.get("label") or "neutral",
            "importance": item.get("importance") or "low",
            "tags": item.get("tags", []),
        }
        for item in items
        if item.get("importance") in ("high", "medium")
    ][:12]

    coverage_level = "none"
    if len(items) >= 20:
        coverage_level = "good"
    elif len(items) >= 8:
        coverage_level = "fair"
    elif items:
        coverage_level = "thin"

    return {
        "dimension": dimension,
        "data_quality": {
            "item_count": len(items),
            "source_count": len(source_counter),
            "days": days,
            "coverage_level": coverage_level,
            "proxy_item_count": sum(1 for item in items if item.get("is_proxy")),
        },
        "source_distribution": dict(source_counter.most_common()),
        "notice_type_distribution": dict(notice_type_counter.most_common()),
        "event_distribution": dict(event_counter.most_common()),
        "polarity_distribution": dict(polarity_counter.most_common()),
        "importance_distribution": dict(importance_counter.most_common()),
        "daily_distribution": dict(sorted(daily_counter.items())),
        "key_events": key_events,
        "ai_summary_hints": [
            f"{dimension}覆盖度: {coverage_level}, 共{len(items)}条, 来源{len(source_counter)}个",
            f"主要事件类型: {', '.join([k for k, _ in event_counter.most_common(3)]) or '无'}",
            f"情绪分布: {dict(polarity_counter.most_common())}",
        ],
    }


def _latest_content_time(items: list[dict], keys: list[str]) -> str | None:
    latest: datetime | None = None
    for item in items:
        for key in keys:
            raw = item.get(key)
            if not raw:
                continue
            parsed = _parse_date(raw)
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
                break
    return latest.isoformat() if latest is not None else None


def _content_is_stale(items: list[dict], max_age_days: int, keys: list[str]) -> bool:
    latest = _latest_content_time(items, keys)
    if not latest:
        return True
    parsed = _parse_date(latest)
    if parsed is None:
        return True
    return parsed < (datetime.now() - timedelta(days=max_age_days))


def _resolve_post_publish_time(update_time: str, now: datetime | None = None) -> datetime | None:
    """Parse East Money guba post timestamp.

    Format examples: ``"06-01 08:00"`` (no year) or ``"2025-12-31"`` (with year).

    For year-less timestamps we anchor to the current year. If the resulting
    datetime appears to be in the future, only roll back to the previous year
    when the offset exceeds 12 hours — this prevents clock skew on Jan/early-Jan
    from pushing recent posts back a full year.
    """
    if now is None:
        now = datetime.now()
    date_match = re.match(r'(\d{2})-(\d{2})\s+(\d{2}):(\d{2})', update_time)
    if date_match:
        month, day, hour, minute = date_match.groups()
        try:
            pub_dt = datetime(now.year, int(month), int(day), int(hour), int(minute))
        except ValueError:
            return None
        if pub_dt - now > timedelta(hours=12):
            pub_dt = pub_dt.replace(year=now.year - 1)
        return pub_dt
    return _parse_date(update_time)

