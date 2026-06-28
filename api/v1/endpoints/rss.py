# -*- coding: utf-8 -*-
"""
===================================
RSS 订阅源端点
===================================

通过 RSSHub 聚合财经资讯，作为 search_news 的补充数据源。
不修改原有数据获取逻辑，独立提供 RSS 能力。
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

from api.v1.endpoints._rss_routes import RSSHUB_ROUTES
from api.v1.endpoints._rss_fetch import _build_feed_url, _fetch_rss_feed
from api.v1.endpoints._rss_cache import _rss_cache_key, _cache_get, _cache_put

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sources", summary="获取可用的 RSS 源列表")
def get_rss_sources():
    """返回所有可用的 RSS 源及其参数要求，供前端渲染选择器。"""
    sources = []
    for source_id, info in RSSHUB_ROUTES.items():
        entry = {
            "id": source_id,
            "label": info["label"],
            "group": info.get("group", ""),
            "requires_stock": info.get("requires_stock", False),
            "stock_placeholder": info.get("stock_placeholder", "股票代码 (如 600519)"),
            "requires_type": info.get("requires_type", False),
            "type_options": info.get("type_options", []),
            "default_type": info.get("default_type", ""),
            "requires_category": info.get("requires_category", False),
            "category_options": info.get("category_options", []),
            "default_category": info.get("default_category", ""),
            "requires_keyword": info.get("requires_keyword", False),
            "keyword_placeholder": info.get("keyword_placeholder", "关键词"),
            "requires_uid": info.get("requires_uid", False),
            "uid_placeholder": info.get("uid_placeholder", "用户 UID"),
        }
        sources.append(entry)
    return {"sources": sources}


@router.get("/feeds", summary="获取 RSS 订阅内容")
def get_rss_feeds(
    source: str = Query(..., description="RSS 源标识"),
    stock_code: Optional[str] = Query(None, description="股票代码 (雪球/深交所需要)"),
    type: Optional[str] = Query(None, description="子类型 (雪球资讯: announcement/news/research)"),
    category: Optional[str] = Query(None, description="分类 (各源不同)"),
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    uid: Optional[str] = Query(None, description="用户 UID"),
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    force: bool = Query(False, description="强制刷新（跳过缓存）"),
):
    """获取指定 RSS 源的订阅内容。"""
    if source not in RSSHUB_ROUTES:
        return {
            "source": source,
            "feed_title": "",
            "feed_link": "",
            "items": [],
            "errors": [f"未知的 RSS 源: {source}，可用源: {', '.join(RSSHUB_ROUTES.keys())}"],
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
        }

    route_info = RSSHUB_ROUTES[source]

    if route_info.get("requires_stock") and not stock_code:
        return {
            "source": source,
            "feed_title": "",
            "feed_link": "",
            "items": [],
            "errors": [f"源 {route_info['label']} 需要提供 stock_code 参数"],
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
        }
    if route_info.get("requires_keyword") and not keyword:
        return {
            "source": source,
            "feed_title": "",
            "feed_link": "",
            "items": [],
            "errors": [f"源 {route_info['label']} 需要提供 keyword 参数"],
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
        }
    if route_info.get("requires_uid") and not uid:
        return {
            "source": source,
            "feed_title": "",
            "feed_link": "",
            "items": [],
            "errors": [f"源 {route_info['label']} 需要提供 uid 参数"],
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
        }

    cache_key = _rss_cache_key(
        source,
        stock_code or "",
        type or "",
        category or "",
        keyword or "",
        uid or "",
    )
    if not force:
        cached = _cache_get(cache_key)
        if cached and isinstance(cached, dict) and cached.get("items"):
            cached["_cached"] = True
            return cached

    try:
        feed_url = _build_feed_url(source, stock_code, type, category, keyword, uid)
    except ValueError as exc:
        return {
            "source": source,
            "feed_title": "",
            "feed_link": "",
            "items": [],
            "errors": [str(exc)],
            "_fetched_at": datetime.now().isoformat(),
            "_cached": False,
        }

    result = _fetch_rss_feed(feed_url, limit=limit)

    response = {
        "source": source,
        "feed_title": result.get("feed_title", ""),
        "feed_link": result.get("feed_link", ""),
        "items": result.get("items", []),
        "errors": result.get("errors", []),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }

    if response["items"]:
        _cache_put(cache_key, response)

    return response