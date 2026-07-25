# -*- coding: utf-8 -*-
"""RSSHub content fetching and matching helpers for financials package."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from api.v1.endpoints.financials._symbol import _safe_str, _parse_date

logger = logging.getLogger(__name__)


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
        feed_specs.append((
            "gelonghui_keyword",
            {"keyword": keyword},
            f"格隆汇搜索:{keyword}",
            True,
        ))
    feed_specs.extend([
        ("cls", {"category": "telegraph"}, "财联社电报", True),
        ("cls", {"category": "depth"}, "财联社深度", True),
        ("wallstreetcn_live", {}, "华尔街见闻实时快讯", True),
        ("wallstreetcn", {"category": "shares"}, "华尔街见闻股市", True),
        ("wallstreetcn_hot", {}, "华尔街见闻热门", True),
        ("jqka_realtime", {}, "同花顺7×24快讯", True),
        ("stcn_kx", {}, "证券时报快讯", True),
    ])
    return feed_specs


def _rsshub_is_slow_spec(spec: tuple[str, dict, str, bool]) -> bool:
    source_id, params, _, _ = spec
    category = _safe_str(params.get("category"))
    keyword = _safe_str(params.get("keyword"))

    if source_id == "cls" and category == "depth":
        return True
    if source_id == "wallstreetcn_hot":
        return True
    if source_id == "eastmoney_search" and keyword and len(keyword) > 6:
        return True
    if source_id == "gelonghui_keyword" and keyword and len(keyword) > 6:
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
        from api.v1.endpoints._rss_fetch import _build_feed_url, _fetch_rss_feed

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
