# -*- coding: utf-8 -*-
"""Social sentiment: Guba posts + diagnose score trend."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

from ._helpers import (
    _normalize_symbol, _safe_float, _safe_str, _parse_date,
    _latest_content_time, _content_is_stale, _resolve_post_publish_time,
)
from ._cache import _daily_cache_get, _daily_cache_put
from ._fetch_sentiment import _classify_sentiment


def _fetch_guba_posts(
    code: str,
    cutoff: datetime,
    headers: dict,
) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    errors: list[str] = []
    try:
        import requests

        url = f"https://guba.eastmoney.com/list,{code},1,f.html"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            pattern = (
                r'<div class="read">(\d+)</div>.*?'
                r'<div class="reply">(\d+)</div>.*?'
                r'<a data-postid="(\d+)" href="/news,\w+,(\d+)\.html">(.*?)</a>.*?'
                r'<div class="update">(.*?)</div>'
            )
            posts = re.findall(pattern, r.text, re.DOTALL)
            for p in posts:
                read_count, reply_count, post_id, _, title, update_time = p
                title = _safe_str(title)
                pub_dt = _resolve_post_publish_time(update_time)

                score = _classify_sentiment(title)
                label = "positive" if score > 0.01 else ("negative" if score < -0.01 else "neutral")

                items.append({
                    "title": title,
                    "content": "",
                    "source": "股吧",
                    "url": f"https://guba.eastmoney.com/news,{code},{post_id}.html",
                    "read_count": int(read_count),
                    "reply_count": int(reply_count),
                    "sentiment_score": round(score, 3),
                    "label": label,
                    "publish_time": pub_dt.isoformat() if pub_dt else None,
                })
            logger.info(f"[SocialSentiment] Guba OK for {code}: {len(items)} posts")
    except Exception as exc:
        errors.append(f"股吧: {exc}")
        logger.warning(f"[SocialSentiment] Guba failed for {code}: {exc}")
    return items, errors


def _fetch_diagnose_score_trend(
    code: str,
    cutoff: datetime,
) -> tuple[list[dict], float | None, list[str]]:
    score_trend: list[dict] = []
    current_score: float | None = None
    errors: list[str] = []
    try:
        import requests

        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "filter": f'(SECURITY_CODE="{code}")',
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "reportName": "RPT_STOCK_HISTORYMARK",
            "sortColumns": "DIAGNOSE_DATE",
            "sortTypes": "1",
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("result") and data["result"].get("data"):
            rows = data["result"]["data"]
            for row in rows:
                raw_date = row.get("DIAGNOSE_DATE", "")
                date_obj = _parse_date(raw_date)
                if not date_obj or date_obj < cutoff:
                    continue
                score = _safe_float(row.get("TOTAL_SCORE"))
                close = _safe_float(row.get("CLOSE"))
                score_trend.append({
                    "date": date_obj.date().isoformat(),
                    "score": round(score, 1) if score is not None else None,
                    "close": close,
                })
            if rows:
                current_score = _safe_float(rows[-1].get("TOTAL_SCORE"))
    except Exception as exc:
        errors.append(f"千股千评: {exc}")
    return score_trend, current_score, errors


def _fetch_social_sentiment(symbol: str, days: int) -> dict:
    """获取社交媒体讨论热度和情绪。

    数据源:
      1. 东方财富股吧 — 帖子列表 (标题+阅读+评论)
      2. 东方财富千股千评 — 每日评分趋势
      3. jieba 分词 + 金融情绪词典 — NLP 情绪分析
    """
    import time as _time

    t0 = _time.time()
    code = _normalize_symbol(symbol)
    cutoff = datetime.now() - timedelta(days=days)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    errors: list[str] = []

    guba_items, guba_errors = _fetch_guba_posts(code, cutoff, headers)
    errors.extend(guba_errors)

    score_trend, current_score, score_errors = _fetch_diagnose_score_trend(code, cutoff)
    errors.extend(score_errors)

    all_items = guba_items

    positive_count = sum(1 for i in all_items if i["label"] == "positive")
    negative_count = sum(1 for i in all_items if i["label"] == "negative")
    neutral_count = sum(1 for i in all_items if i["label"] == "neutral")

    daily_counts: dict[str, dict] = {}
    for item in all_items:
        date_str = (item.get("publish_time") or "")[:10]
        if date_str:
            daily = daily_counts.setdefault(date_str, {
                "total": 0, "positive": 0, "negative": 0, "neutral": 0,
                "read_total": 0, "reply_total": 0,
            })
            daily["total"] += 1
            daily[item["label"]] += 1
            daily["read_total"] += item.get("read_count", 0)
            daily["reply_total"] += item.get("reply_count", 0)

    for st in score_trend:
        if st["date"] in daily_counts:
            daily_counts[st["date"]]["score"] = st["score"]
        else:
            daily_counts[st["date"]] = {
                "total": 0, "positive": 0, "negative": 0, "neutral": 0,
                "read_total": 0, "reply_total": 0, "score": st["score"],
            }

    total = positive_count + negative_count + neutral_count
    if total > 0:
        overall_score = round(((positive_count - negative_count) / total) * 100, 1)
    else:
        overall_score = 0

    total_read = sum(i.get("read_count", 0) for i in all_items)
    total_reply = sum(i.get("reply_count", 0) for i in all_items)

    logger.info(f"[SocialSentiment] total {_time.time() - t0:.1f}s for {code}: "
                f"{len(all_items)} guba posts, score={overall_score}")
    latest_time = _latest_content_time(all_items, ["publish_time"])

    return {
        "symbol": code,
        "days": days,
        "overall_score": overall_score,
        "total_discussion": total,
        "total_read": total_read,
        "total_reply": total_reply,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "diagnose_score": round(current_score, 1) if current_score is not None else None,
        "score_trend": score_trend,
        "daily_trend": [
            {"date": k, **v}
            for k, v in sorted(daily_counts.items())
        ],
        "items": all_items[:50],
        "errors": errors,
        "data_time": latest_time,
        "is_stale": _content_is_stale(all_items, days, ["publish_time"]),
        "fallback_used": bool(errors),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }
