# -*- coding: utf-8 -*-
"""
===================================
RSS 订阅源端点
===================================

通过 RSSHub 聚合财经资讯，作为 search_news 的补充数据源。
不修改原有数据获取逻辑，独立提供 RSS 能力。
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.v1.schemas.common import ErrorResponse
from src.config import Config
from src.storage import DatabaseManager, RssSubscriptionTitleConflict
from api.v1.endpoints._rss_routes import RSSHUB_ROUTES
from api.v1.endpoints._rss_fetch import (
    _build_feed_url_generic,
    _fetch_rss_feed,
    _fetch_rss_feed_json,
)
from api.v1.endpoints._rss_cache import (
    _rss_cache_key_generic,
    _cache_get,
    _cache_put,
)
from api.v1.endpoints._rss_namespace import (
    get_namespaces_flat,
    get_namespace_detail,
    get_categories,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 路由发现（namespace discovery）───────────────────────────────────────

@router.get(
    "/namespaces",
    summary="获取 RSSHub 全量路由（扁平化）",
    responses={500: {"model": ErrorResponse}},
)
def get_rss_namespaces(
    force: bool = Query(False, description="强制刷新缓存"),
    finance_only: bool = Query(True, description="仅返回股市相关路由(财经分类,排除纯加密)"),
):
    """代理 RSSHub /api/namespace，返回扁平化路由列表供前端浏览/搜索/订阅。

    默认 finance_only=true：只返回影响股市的路由(A股/港美股/外汇/大宗/宏观/央行)，
    排除纯加密货币。传 finance_only=false 可看全量。
    """
    try:
        return get_namespaces_flat(force=force, finance_only=finance_only)
    except Exception as exc:
        logger.error("Failed to fetch RSS namespaces: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取 RSSHub 路由失败"},
        )


@router.get(
    "/namespaces/{namespace}",
    summary="获取单个命名空间详情",
    responses={500: {"model": ErrorResponse}},
)
def get_rss_namespace(namespace: str, force: bool = Query(False)):
    """代理 RSSHub /api/namespace/{ns}，返回单个命名空间的路由详情。"""
    try:
        return get_namespace_detail(namespace, force=force)
    except Exception as exc:
        logger.error("Failed to fetch namespace detail: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取命名空间详情失败"},
        )


@router.get(
    "/categories",
    summary="获取全部路由分类",
    responses={500: {"model": ErrorResponse}},
)
def get_rss_categories(
    force: bool = Query(False),
    finance_only: bool = Query(True, description="仅股市相关路由的分类"),
):
    """返回路由分类（供前端筛选 chip）。默认仅股市相关。"""
    try:
        return {"categories": get_categories(force=force, finance_only=finance_only)}
    except Exception as exc:
        logger.error("Failed to fetch RSS categories: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取分类失败"},
        )


@router.get(
    "/featured",
    summary="获取精选财经 RSS 源",
    responses={500: {"model": ErrorResponse}},
)
def get_rss_featured():
    """返回静态精选财经源（与发现的扁平结构同形），供快入口。"""
    try:
        routes = []
        for source_id, info in RSSHUB_ROUTES.items():
            routes.append({
                "namespace": _namespace_from_path(info["path"]),
                "namespace_name": info.get("label", source_id),
                "route_path": info["path"] if info["path"].startswith("/") else f"/{info['path']}",
                "name": info.get("label", source_id),
                "url": "",
                "example": info["path"],
                "categories": ["finance"],
                "description": info.get("label", ""),
                "parameters": {},
                "features": {},
                "maintainers": [],
                "source_id": source_id,
                "source_info": info,
            })
        return {"routes": routes}
    except Exception as exc:
        logger.error("Failed to fetch featured sources: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取精选源失败"},
        )


def _namespace_from_path(path: str) -> str:
    """从路径取首个段作为 namespace（如 /xueqiu/... -> xueqiu）。"""
    p = (path or "").strip("/")
    return p.split("/", 1)[0] if p else ""


# ── 通用 Feed 取数（POST /feeds）─────────────────────────────────────────

class FeedSpecRequest(BaseModel):
    route_path: str = Field(..., description="RSSHub 路由模板，如 /wallstreetcn/news/:category?")
    params: Dict[str, Any] = Field(default_factory=dict, description="路径参数值")
    options: Dict[str, Any] = Field(default_factory=dict, description="RSSHub 通用选项 (limit/filter/mode/format/...)")
    namespace: Optional[str] = Field(None, description="命名空间（用于参数格式化器）")
    limit: int = Field(30, ge=1, le=100, description="返回条数")
    force: bool = Field(False, description="强制刷新（跳过缓存）")


@router.post(
    "/feeds",
    summary="按通用 FeedSpec 获取 RSS 内容",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def get_rss_feeds_by_spec(body: FeedSpecRequest):
    """通用取数：用 params 渲染 route_path，追加 options，抓取并缓存。

    特殊路由 /rsshub/transform/html（HTML→RSS 万能转换器）：params 携带 url + CSS
    选择器，按转换器规则构建 URL。
    """
    effective_options = dict(body.options or {})
    # The app is a reader, so content-level truncation is never applied even if
    # an older saved subscription still contains RSSHub's `brief` option.
    effective_options.pop("brief", None)
    try:
        effective_limit = max(1, min(100, int(effective_options.get("limit") or body.limit)))
    except (TypeError, ValueError):
        effective_limit = body.limit

    try:
        if body.route_path.rstrip("/") == "/rsshub/transform/html":
            feed_url = _build_html_transform_url_from_params(body.params, effective_options, effective_limit)
        else:
            feed_url = _build_feed_url_generic(
                body.route_path, body.params, effective_options, namespace=body.namespace,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )

    cache_key = _rss_cache_key_generic(body.route_path, body.params, effective_options)
    if not body.force:
        cached = _cache_get(cache_key)
        if cached and isinstance(cached, dict) and cached.get("items"):
            cached["_cached"] = True
            return cached

    # Default to JSON (rich items: image/attachments/content_html); fall back to
    # XML parse if the JSON path fails so we degrade gracefully per-route.
    result = _fetch_rss_feed_json(feed_url, limit=effective_limit)
    if result.get("errors") and not result.get("items"):
        logger.info("[RSS] JSON fetch failed, falling back to XML: %s", result["errors"][0])
        result = _fetch_rss_feed(feed_url, limit=effective_limit)

    response = {
        "route_path": body.route_path,
        "params": body.params,
        "options": effective_options,
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


class FeedItemDetailRequest(BaseModel):
    route_path: str
    params: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    namespace: Optional[str] = None
    item_id: str = ""
    title: str = ""
    link: str = ""


@router.post(
    "/feeds/item",
    summary="获取单条 RSS 消息全文",
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
def get_rss_feed_item_detail(body: FeedItemDetailRequest):
    """只抓取当前选中消息的全文，供项目内详情阅读。"""
    detail_options = dict(body.options or {})
    detail_options.pop("brief", None)
    detail_options.update({
        "mode": "fulltext",
        "limit": 5,
    })
    if body.title:
        detail_options["filter_title"] = f"^{re.escape(body.title)}$"

    try:
        feed_url = _build_feed_url_generic(
            body.route_path,
            body.params,
            detail_options,
            namespace=body.namespace,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )

    cache_key = _rss_cache_key_generic(body.route_path, body.params, detail_options)
    cached = _cache_get(cache_key)
    if cached and isinstance(cached, dict) and cached.get("items"):
        result = cached
    else:
        result = _fetch_rss_feed_json(feed_url, limit=5, timeout=45.0)
        if result.get("errors") and not result.get("items"):
            result = _fetch_rss_feed(feed_url, limit=5, timeout=30.0)
        if result.get("items"):
            _cache_put(cache_key, result)

    if result.get("errors") and not result.get("items"):
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": result["errors"][0]},
        )

    items = result.get("items") or []
    selected = next(
        (
            item for item in items
            if (body.item_id and item.get("id") == body.item_id)
            or (body.link and item.get("link") == body.link)
            or (body.title and item.get("title") == body.title)
        ),
        items[0] if items else None,
    )
    if not selected:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "未找到该消息内容"},
        )
    return selected


# ── 原始 Feed 透传（多格式下载）──────────────────────────────────────────

_MEDIA_TYPES = {
    "rss": "application/rss+xml; charset=utf-8",
    "atom": "application/atom+xml; charset=utf-8",
    "json": "application/feed+json; charset=utf-8",
    "rss3": "application/json; charset=utf-8",
}


class RawFeedRequest(BaseModel):
    route_path: str
    params: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    namespace: Optional[str] = None
    format: str = Field("rss", description="输出格式: rss/atom/json/rss3")
    limit: int = Field(30, ge=1, le=100)


@router.post(
    "/feeds/raw",
    summary="透传 RSSHub 原始 feed 字节（多格式下载）",
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
def get_rss_feeds_raw(body: RawFeedRequest):
    """按 FeedSpec 构建带 format 的 RSSHub URL，原样透传字节与正确 Content-Type。"""
    fmt = (body.format or "rss").lower()
    if fmt not in _MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": f"不支持的格式: {fmt}"},
        )
    try:
        # Force the requested format into options for URL building.
        opts = dict(body.options or {})
        opts["format"] = fmt
        opts.setdefault("limit", body.limit)
        feed_url = _build_feed_url_generic(
            body.route_path, body.params, opts, namespace=body.namespace,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )

    try:
        import requests as _requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        resp = _requests.get(feed_url, headers=headers, timeout=30.0)
        resp.raise_for_status()
    except _requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": f"RSSHub 请求失败: {exc}"},
        )

    return Response(content=resp.content, media_type=_MEDIA_TYPES[fmt])


# ── HTML→RSS 万能转换器───────────────────────────────────────────────────

class HtmlTransformRequest(BaseModel):
    url: str = Field(..., description="目标网页 URL")
    title: Optional[str] = Field(None, description="Feed 标题（默认取页面 <title>）")
    item: str = Field("html", description="单条 item 的 CSS 选择器")
    item_title: Optional[str] = Field(None, description="标题元素选择器")
    item_title_attr: Optional[str] = Field(None)
    item_link: Optional[str] = Field(None, description="链接元素选择器")
    item_link_attr: Optional[str] = Field(None)
    item_desc: Optional[str] = Field(None, description="描述元素选择器")
    item_desc_attr: Optional[str] = Field(None)
    item_pubdate: Optional[str] = Field(None, description="发布时间元素选择器")
    item_pubdate_attr: Optional[str] = Field(None)
    item_content: Optional[str] = Field(None, description="二次抓取完整正文的选择器")
    encoding: Optional[str] = Field(None, description="页面编码（默认 utf-8）")
    limit: int = Field(30, ge=1, le=100)
    options: Dict[str, Any] = Field(default_factory=dict, description="额外 RSSHub 通用选项")


def _build_html_transform_url(body: "HtmlTransformRequest") -> str:
    """构建 RSSHub /rsshub/transform/html/:url/:routeParams URL。"""
    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    encoded_url = quote(body.url.strip(), safe="")

    route_params: Dict[str, str] = {}
    field_map = {
        "title": body.title, "item": body.item, "itemTitle": body.item_title,
        "itemTitleAttr": body.item_title_attr, "itemLink": body.item_link,
        "itemLinkAttr": body.item_link_attr, "itemDesc": body.item_desc,
        "itemDescAttr": body.item_desc_attr, "itemPubDate": body.item_pubdate,
        "itemPubDateAttr": body.item_pubdate_attr, "itemContent": body.item_content,
        "encoding": body.encoding,
    }
    for k, v in field_map.items():
        if v is not None and str(v).strip():
            route_params[k] = str(v).strip()
    # extra options merged (limit/filter/...)
    for k, v in (body.options or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        route_params[str(k)] = str(v)

    encoded_params = quote(urlencode(route_params), safe="")
    return f"{base_url}/rsshub/transform/html/{encoded_url}/{encoded_params}"


# camelCase → param key map for the persisted (subscription) form.
_HTML_PARAM_KEYS = {
    "url", "title", "item", "itemTitle", "itemTitleAttr", "itemLink", "itemLinkAttr",
    "itemDesc", "itemDescAttr", "itemPubDate", "itemPubDateAttr", "itemContent", "encoding",
}


def _build_html_transform_url_from_params(params: Dict[str, Any], options: Dict[str, Any], limit: int) -> str:
    """从持久化的 params（url + 选择器，camelCase）构建 HTML 转换器 URL。

    供订阅了 HTML 转换器路由的 feed 取数复用。
    """
    params = params or {}
    url = str(params.get("url") or "").strip()
    if not url:
        raise ValueError("HTML 转换器缺少 url 参数")
    route_params: Dict[str, str] = {}
    for k in _HTML_PARAM_KEYS:
        if k == "url":
            continue
        v = params.get(k)
        if v is not None and str(v).strip():
            route_params[k] = str(v).strip()
    # merge options (filter/limit/...) but drop format (we request JSON ourselves)
    for k, v in (options or {}).items():
        if k == "format":
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        route_params[str(k)] = str(v)
    route_params.setdefault("limit", str(limit))

    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    encoded_url = quote(url, safe="")
    encoded_params = quote(urlencode(route_params), safe="")
    return f"{base_url}/rsshub/transform/html/{encoded_url}/{encoded_params}"


@router.post(
    "/transform/html",
    summary="HTML→RSS 万能转换器（代理 RSSHub /rsshub/transform/html）",
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
def transform_html(body: HtmlTransformRequest):
    """把任意网页转成 RSS feed。需 RSSHub 实例配置 ALLOW_USER_SUPPLY_UNSAFE_DOMAIN=true。"""
    if not body.url or not body.url.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": "url 不能为空"},
        )
    try:
        feed_url = _build_html_transform_url(body)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": f"构建 URL 失败: {exc}"},
        )

    # Prefer JSON for rich items; fall back to XML.
    result = _fetch_rss_feed_json(feed_url, limit=body.limit)
    if result.get("errors") and not result.get("items"):
        result = _fetch_rss_feed(feed_url, limit=body.limit)

    if result.get("errors") and not result.get("items"):
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": result["errors"][0]},
        )

    # Also return the buildable route_path so the frontend can persist it as a
    # subscription (selectors packed into params).
    return {
        "route_path": "/rsshub/transform/html/:url/:routeParams",
        "namespace": "rsshub",
        "feed_title": result.get("feed_title", ""),
        "feed_link": result.get("feed_link", ""),
        "items": result.get("items", []),
        "errors": result.get("errors", []),
        "_fetched_at": datetime.now().isoformat(),
        "_cached": False,
        # Persistable spec for "save as subscription":
        "persist_params": {
            "url": body.url.strip(),
            **{k: v for k, v in {
                "title": body.title, "item": body.item,
                "itemTitle": body.item_title, "itemLink": body.item_link,
                "itemDesc": body.item_desc, "itemPubDate": body.item_pubdate,
                "itemContent": body.item_content, "encoding": body.encoding,
            }.items() if v},
        },
    }


# ── RSS 订阅 CRUD（持久化 FeedSpec）──────────────────────────────────────

class RssSubscriptionUpsertRequest(BaseModel):
    title: str
    namespace: str
    route_path: str
    params: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)


class RssSubscriptionPatchRequest(BaseModel):
    title: Optional[str] = None
    namespace: Optional[str] = None
    route_path: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = None


@router.get(
    "/subscriptions",
    summary="列出全部 RSS 订阅",
    responses={500: {"model": ErrorResponse}},
)
def list_rss_subscriptions():
    """返回全部 RSS 订阅（按 sortOrder 升序）。"""
    try:
        db = DatabaseManager.get_instance()
        return {"subscriptions": db.list_rss_subscriptions()}
    except Exception as exc:
        logger.error("Failed to list RSS subscriptions: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取订阅列表失败"},
        )


@router.post(
    "/subscriptions",
    summary="创建或更新 RSS 订阅（按 title upsert）",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def upsert_rss_subscription(body: RssSubscriptionUpsertRequest):
    """按 title 创建订阅，同名则更新。"""
    try:
        db = DatabaseManager.get_instance()
        return db.upsert_rss_subscription(
            body.title, body.namespace, body.route_path, body.params, body.options,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Failed to upsert RSS subscription: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "保存订阅失败"},
        )


class RssSubscriptionReorderRequest(BaseModel):
    ordered_ids: List[str] = Field(..., description="按新顺序排列的订阅 id 列表")


@router.patch(
    "/subscriptions/reorder",
    summary="重排 RSS 订阅顺序",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def reorder_rss_subscriptions(body: RssSubscriptionReorderRequest):
    """按给定 id 顺序重排订阅的 sort_order。"""
    try:
        db = DatabaseManager.get_instance()
        ok = db.reorder_rss_subscriptions(body.ordered_ids)
        return {"reordered": ok}
    except Exception as exc:
        logger.error("Failed to reorder RSS subscriptions: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "重排订阅失败"},
        )


@router.patch(
    "/subscriptions/{sub_id}",
    summary="更新 RSS 订阅",
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def patch_rss_subscription(sub_id: str, body: RssSubscriptionPatchRequest):
    """按 id 更新订阅字段。"""
    try:
        db = DatabaseManager.get_instance()
        sub = db.update_rss_subscription(
            sub_id,
            title=body.title,
            namespace=body.namespace,
            route_path=body.route_path,
            params=body.params,
            options=body.options,
        )
        if sub is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": "订阅不存在"},
            )
        return sub
    except RssSubscriptionTitleConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "title_conflict", "message": f"订阅标题已存在: {exc}"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update RSS subscription: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "更新订阅失败"},
        )


@router.delete(
    "/subscriptions/{sub_id}",
    summary="删除 RSS 订阅",
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def delete_rss_subscription(sub_id: str):
    """按 id 删除订阅。"""
    try:
        db = DatabaseManager.get_instance()
        deleted = db.delete_rss_subscription(sub_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": "订阅不存在"},
            )
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to delete RSS subscription: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "删除订阅失败"},
        )


# ── 实例 Cookie 生效测试 ──────────────────────────────────────────────────
#
# XUEQIU_COOKIES 是 RSSHub 实例级环境变量（含 HttpOnly 的 xq_a_token），只能由
# 管理员写入 services/rsshub/app/.env 并重启实例，页面无法直接配置。这里只提供
# "测试当前实例配置的 Cookie 是否生效"——实际请求一次 xueqiu/timeline 看能否取到内容。

def _rsshub_env_path() -> Path:
    """RSSHub 实例 .env 路径（项目根/services/rsshub/app/.env）。"""
    return Path(__file__).resolve().parent.parent.parent.parent / "services" / "rsshub" / "app" / ".env"


def _xueqiu_cookies_configured() -> bool:
    """实例 .env 是否配置了非空的 XUEQIU_COOKIES。"""
    env_path = _rsshub_env_path()
    if not env_path.exists():
        return False
    try:
        values = dotenv_values(env_path)
    except Exception:
        return False
    return bool((values.get("XUEQIU_COOKIES") or "").strip())


@router.post(
    "/instance/cookies/test",
    summary="测试 RSSHub 实例配置的雪球 Cookie 是否生效",
    responses={500: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
def test_xueqiu_cookie():
    """实际请求一次 xueqiu/timeline，判断实例当前 XUEQIU_COOKIES 是否生效。

    不写 .env、不重启实例——只读当前状态并做一次真实取数验证。供前端"测试
    Cookie 是否生效"按钮调用。管理员配好 .env 并重启实例后，用户点此即可确认。
    """
    configured = _xueqiu_cookies_configured()
    if not configured:
        return {
            "configured": False,
            "verified": False,
            "item_count": 0,
            "message": "实例未配置 XUEQIU_COOKIES，请联系管理员在 services/rsshub/app/.env 配置（需含 xq_a_token）并重启 RSSHub 实例。",
        }

    # 实际请求 timeline（与正式取数同路径：format=json 富取数）。
    try:
        feed_url = _build_feed_url_generic("/xueqiu/timeline/:usergroup_id?", {}, {})
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"构建测试 URL 失败: {exc}"},
        )

    result = _fetch_rss_feed_json(feed_url, limit=3)
    errors = result.get("errors") or []
    items = result.get("items") or []
    if items:
        return {
            "configured": True,
            "verified": True,
            "item_count": len(items),
            "message": f"Cookie 已生效，timeline 成功返回 {len(items)} 条内容。",
        }
    # 配了但取不到内容：通常是 xq_a_token 缺失或过期。
    detail = errors[0] if errors else "timeline 未返回内容"
    return {
        "configured": True,
        "verified": False,
        "item_count": 0,
        "message": f"Cookie 已配置但未生效（{detail}）。可能是 xq_a_token 缺失或已过期，请联系管理员更新。",
    }
