# -*- coding: utf-8 -*-
"""
===================================
RSS 订阅源端点
===================================

通过 RSSHub 聚合财经资讯，作为 search_news 的补充数据源。
不修改原有数据获取逻辑，独立提供 RSS 能力。
"""

import logging
import json
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import feedparser
import requests
from fastapi import APIRouter, Query

from src.config import Config

logger = logging.getLogger(__name__)
router = APIRouter()


# ── RSSHub 路由映射 ──────────────────────────────────────────────

RSSHUB_ROUTES: dict[str, dict[str, Any]] = {
    "xueqiu_info": {
        "path": "/xueqiu/stock_info/{id}/{type}",
        "label": "雪球个股资讯",
        "group": "个股",
        "requires_stock": True,
        "requires_type": True,
        "type_options": [
            {"value": "announcement", "label": "公告"},
            {"value": "news", "label": "资讯"},
            {"value": "research", "label": "研报"},
        ],
        "default_type": "news",
    },
    "xueqiu_comments": {
        "path": "/xueqiu/stock_comments/{id}",
        "label": "雪球个股评论",
        "group": "个股",
        "requires_stock": True,
    },
    "wallstreetcn": {
        "path": "/wallstreetcn/news/{category}",
        "label": "华尔街见闻新闻",
        "group": "快讯",
        "requires_category": True,
        "category_options": [
            {"value": "global", "label": "最新"},
            {"value": "shares", "label": "股市"},
            {"value": "bonds", "label": "债市"},
            {"value": "commodities", "label": "商品"},
            {"value": "forex", "label": "外汇"},
        ],
        "default_category": "global",
    },
    "wallstreetcn_hot": {
        "path": "/wallstreetcn/hot",
        "label": "华尔街见闻热门",
        "group": "快讯",
    },
    "wallstreetcn_live": {
        "path": "/wallstreetcn/live",
        "label": "华尔街见闻实时快讯",
        "group": "快讯",
    },
    "wallstreetcn_calendar": {
        "path": "/wallstreetcn/calendar",
        "label": "华尔街见闻财经日历",
        "group": "宏观",
    },
    "cls": {
        "path": "/cls/{category}",
        "label": "财联社",
        "group": "快讯",
        "requires_category": True,
        "category_options": [
            {"value": "telegraph", "label": "电报快讯"},
            {"value": "depth", "label": "深度文章"},
            {"value": "hot", "label": "热门文章"},
        ],
        "default_category": "telegraph",
    },
    "sina_roll": {
        "path": "/sina/rollnews/{category}",
        "label": "新浪财经滚动",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "2509", "label": "全部"},
            {"value": "2517", "label": "股市"},
            {"value": "2516", "label": "财经"},
            {"value": "2518", "label": "美股"},
        ],
        "default_category": "2517",
    },
    "sina_finance": {
        "path": "/sina/finance/{category}",
        "label": "新浪财经频道",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "china", "label": "中国财经"},
            {"value": "rollnews", "label": "财经滚动"},
            {"value": "stock/usstock", "label": "美股资讯"},
        ],
        "default_category": "china",
    },
    "eastmoney_report": {
        "path": "/eastmoney/report/{category}",
        "label": "东方财富研报",
        "group": "研报",
        "requires_category": True,
        "category_options": [
            {"value": "stock", "label": "个股研报"},
            {"value": "industry", "label": "行业研报"},
            {"value": "strategyreport", "label": "策略研报"},
            {"value": "macresearch", "label": "宏观研报"},
        ],
        "default_category": "stock",
    },
    "eastmoney_search": {
        "path": "/eastmoney/search/{keyword}",
        "label": "东方财富搜索新闻",
        "group": "个股",
        "requires_keyword": True,
        "keyword_placeholder": "关键词/股票代码 (如 600519)",
    },
    "eastmoney_guba_user": {
        "path": "/eastmoney/gerenzhongxin/guba/{uid}",
        "label": "东方财富股吧用户帖子",
        "group": "个股",
        "requires_uid": True,
        "uid_placeholder": "股吧用户 UID",
    },
    "yicai": {
        "path": "/yicai/{category}",
        "label": "第一财经",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "latest", "label": "最新资讯"},
            {"value": "brief", "label": "快讯"},
            {"value": "headline", "label": "头条"},
            {"value": "news", "label": "新闻"},
            {"value": "vip", "label": "会员文章"},
            {"value": "video", "label": "视频"},
            {"value": "dt", "label": "读书"},
        ],
        "default_category": "latest",
    },
    "36kr": {
        "path": "/36kr/{category}",
        "label": "36氪",
        "group": "科技创投",
        "requires_category": True,
        "category_options": [
            {"value": "newsflashes", "label": "快讯"},
            {"value": "information/web_news", "label": "网页新闻"},
            {"value": "hot-list", "label": "热榜"},
        ],
        "default_category": "newsflashes",
    },
    "szse_disclosure": {
        "path": "/szse/disclosure/listed/notice/{query}",
        "label": "深交所公告",
        "group": "交易所/监管",
        "requires_stock": True,
        "stock_placeholder": "股票代码 (如 000001)",
    },
    "sse_inquire": {
        "path": "/sse/inquire",
        "label": "上交所监管问询",
        "group": "交易所/监管",
    },
    "sse_disclosure": {
        "path": "/sse/disclosure",
        "label": "上交所披露",
        "group": "交易所/监管",
    },
    "bloomberg_markets": {
        "path": "/bloomberg/markets",
        "label": "Bloomberg 市场资讯",
        "group": "海外",
    },
    "jrj": {
        "path": "/jrj/{category}",
        "label": "金融界资讯",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "103", "label": "财经资讯"},
            {"value": "102", "label": "美股资讯"},
            {"value": "104", "label": "基金资讯"},
            {"value": "107", "label": "期货资讯"},
        ],
        "default_category": "103",
    },
    "chinamoney": {
        "path": "/chinamoney",
        "label": "中国外汇交易中心公告",
        "group": "监管/公告",
    },
    "ulapia": {
        "path": "/ulapia/reports/{category}",
        "label": "ulapia 研报",
        "group": "研报",
        "requires_category": True,
        "category_options": [
            {"value": "stock_research", "label": "个股研报"},
            {"value": "industry_research", "label": "行业研报"},
            {"value": "strategy_research", "label": "策略研报"},
            {"value": "macro_research", "label": "宏观研报"},
            {"value": "brokerage_news", "label": "券商晨报"},
        ],
        "default_category": "stock_research",
    },
}


# ── 工具函数 ─────────────────────────────────────────────────────


def _stock_code_to_rsshub_id(code: str) -> str:
    """将 6 位股票代码转为 RSSHub/雪球格式 (SH600519, SZ000002, BJ430001)。"""
    c = code.strip().upper()
    # 去掉已有前缀
    for prefix in ("SH", "SZ", "BJ"):
        if c.startswith(prefix):
            c = c[len(prefix):]
            break
    # 去掉后缀 (.SH, .SZ)
    if "." in c:
        c = c.split(".")[0]
    if not c.isdigit() or len(c) != 6:
        return code  # 非标准代码原样返回
    if c[0] in ("6", "9"):
        return f"SH{c}"
    elif c[0] in ("0", "2", "3"):
        return f"SZ{c}"
    else:
        return f"BJ{c}"


def _normalize_stock_code(code: str) -> str:
    """将股票代码统一为 6 位纯数字，供交易所公告路由使用。"""
    raw_code = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if raw_code.startswith(prefix):
            raw_code = raw_code[len(prefix):]
            break
    if "." in raw_code:
        raw_code = raw_code.split(".")[0]
    return raw_code


def _build_feed_url(source: str, stock_code: Optional[str] = None,
                    type: Optional[str] = None,
                    category: Optional[str] = None,
                    keyword: Optional[str] = None,
                    uid: Optional[str] = None) -> str:
    """根据 source 和参数构建 RSSHub feed URL。"""
    route_info = RSSHUB_ROUTES.get(source)
    if not route_info:
        raise ValueError(f"未知的 RSS 源: {source}")

    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    path: str = route_info["path"]

    # 替换 {id} — 股票代码
    if "{id}" in path:
        if not stock_code:
            raise ValueError(f"源 {source} 需要提供 stock_code 参数")
        rsshub_id = _stock_code_to_rsshub_id(stock_code)
        path = path.replace("{id}", rsshub_id)

    # 替换 {type} — 子类型 (雪球资讯)
    if "{type}" in path:
        t = type or route_info.get("default_type", "news")
        path = path.replace("{type}", t)

    # 替换 {category}
    if "{category}" in path:
        cat = category or route_info.get("default_category", "")
        path = path.replace("{category}", cat)

    # 替换 {keyword} — 搜索关键词
    if "{keyword}" in path:
        if not keyword:
            raise ValueError(f"源 {source} 需要提供 keyword 参数")
        path = path.replace("{keyword}", quote(keyword.strip(), safe=""))

    # 替换 {uid} — 用户 UID
    if "{uid}" in path:
        if not uid:
            raise ValueError(f"源 {source} 需要提供 uid 参数")
        path = path.replace("{uid}", quote(uid.strip(), safe=""))

    # 替换 {query} — 深交所公告
    if "{query}" in path:
        if not stock_code:
            raise ValueError(f"源 {source} 需要提供 stock_code 参数")
        # 深交所只需纯数字代码，去掉前缀
        raw_code = _normalize_stock_code(stock_code)
        path = path.replace("{query}", f"stock={raw_code}")

    return f"{base_url}{path}"


def _fetch_rss_feed(url: str, limit: int = 20, timeout: float = 15.0) -> dict:
    """抓取并解析 RSS/Atom feed。"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"[RSS] 请求失败 {url}: {exc}")
        return {"items": [], "errors": [f"请求失败: {exc}"]}

    feed = feedparser.parse(resp.text)

    # feedparser 解析失败时 bozo=1，但仍可能有部分数据
    if not feed.entries and feed.bozo and not getattr(feed, "feed", None):
        error_msg = getattr(feed, "bozo_exception", "未知解析错误")
        logger.warning(f"[RSS] 解析失败 {url}: {error_msg}")
        return {"items": [], "errors": [f"解析失败: {error_msg}"]}

    items = []
    for entry in feed.entries[:limit]:
        # 提取摘要，优先 summary，其次 description，截断到 500 字
        summary = ""
        if hasattr(entry, "summary"):
            summary = entry.summary or ""
        elif hasattr(entry, "description"):
            summary = entry.description or ""
        summary = re.sub(r"<[^>]+>", "", summary).strip()
        if len(summary) > 500:
            summary = summary[:497] + "..."

        # 提取发布时间
        published = None
        for attr in ("published", "updated", "created"):
            val = getattr(entry, attr, None)
            if val:
                try:
                    # feedparser 返回的时间结构
                    parsed = getattr(entry, f"{attr}_parsed", None)
                    if parsed:
                        from time import mktime
                        published = datetime.fromtimestamp(mktime(parsed)).isoformat()
                    else:
                        published = str(val)
                except Exception:
                    published = str(val)
                break

        # 提取标签
        tags = []
        if hasattr(entry, "tags"):
            tags = [t.get("term", "") for t in entry.tags if t.get("term")]

        items.append({
            "title": getattr(entry, "title", ""),
            "link": getattr(entry, "link", ""),
            "summary": summary,
            "published": published,
            "author": getattr(entry, "author", ""),
            "tags": tags,
        })

    feed_title = ""
    feed_link = ""
    if hasattr(feed, "feed"):
        feed_title = getattr(feed.feed, "title", "")
        feed_link = getattr(feed.feed, "link", "")

    return {
        "feed_title": feed_title,
        "feed_link": feed_link,
        "items": items,
        "errors": [],
    }


# ── 缓存 ─────────────────────────────────────────────────────────


def _rss_cache_key(source: str, stock_code: str = "", type: str = "",
                   category: str = "", keyword: str = "", uid: str = "") -> str:
    """按小时粒度构建缓存 key（新闻更新频率高于日线）。"""
    hour = datetime.now().strftime("%Y%m%d%H")
    parts = [f"rss:{source}"]
    if stock_code:
        parts.append(f"stock={stock_code}")
    if type:
        parts.append(f"type={type}")
    if category:
        parts.append(f"cat={category}")
    if keyword:
        parts.append(f"kw={keyword}")
    if uid:
        parts.append(f"uid={uid}")
    parts.append(hour)
    return ":".join(parts)


def _cache_get(key: str) -> Optional[dict]:
    """从数据库获取缓存的 RSS 数据。"""
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        data = db.get_kline_snapshot(key)
        return data
    except Exception:
        logger.warning("[RSS] _cache_get failed for key=%s", key, exc_info=True)
        return None


def _cache_put(key: str, data: dict) -> None:
    """将 RSS 数据写入缓存。"""
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        db.save_kline_snapshot(key, json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        logger.warning(f"[RSS] 缓存写入失败: {exc}")


# ── 端点 ─────────────────────────────────────────────────────────


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
    # 校验 source
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

    # 参数校验
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

    # 缓存查询
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

    # 构建 URL 并抓取
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

    # 组装返回
    response = {
        "source": source,
        "feed_title": result.get("feed_title", ""),
        "feed_link": result.get("feed_link", ""),
        "items": result.get("items", []),
        "errors": result.get("errors", []),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
    }

    # 写入缓存
    if response["items"]:
        _cache_put(cache_key, response)

    return response
