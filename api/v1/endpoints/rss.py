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
from src.storage import DatabaseManager
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
from api.v1.endpoints._gelonghui_subjects import get_subjects as get_gelonghui_subjects_list
from api.v1.endpoints._nanhua_tree import get_nanhua_tree
from api.v1.endpoints._cih_index_categories import get_cih_index_categories
from api.v1.endpoints._cls_subjects import get_cls_subjects as get_cls_subjects_list
from api.v1.endpoints._futunn_topics import get_futunn_topics

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
    "/gelonghui/subjects",
    summary="格隆汇主题列表（供 /gelonghui/subject/:id 路由选参）",
    responses={500: {"model": ErrorResponse}},
)
def get_gelonghui_subjects(
    force: bool = Query(False, description="强制刷新缓存"),
    keyword: Optional[str] = Query(None, description="按主题名/简介过滤"),
):
    """代理格隆汇主题列表 API，返回 ``{subjectId, name, followCount, summary, link}``。

    供 ``/gelonghui/subject/:id`` 路由的参数选择器使用：用户搜主题名、看到关注数与
    简介、点选后填入 ``subjectId``。6h 缓存，抓取失败回退 stale 缓存。
    """
    try:
        return get_gelonghui_subjects_list(force=force, keyword=keyword)
    except Exception as exc:
        logger.error("Failed to fetch gelonghui subjects: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取格隆汇主题失败"},
        )


@router.get(
    "/nanhua/report-types",
    summary="南华期货研报分类树（供 /nanhua/report/:type1/:type2 路由选参）",
    responses={500: {"model": ErrorResponse}},
)
def get_nanhua_report_types(
    force: bool = Query(False, description="强制刷新缓存"),
):
    """代理南华官网分类树接口，返回 ``{types: [{type, name, children: [{type, name}]}]}``。

    供 ``/nanhua/report/:type1/:type2`` 路由的级联选择器使用：选了 type1 后联动出该
    分类下的合法 type2，避免填出 ``HOT/WEEK_black`` 这类非法组合（上游返回空、RSSHub
    抛 503 ``this route is empty``）。6h 缓存，抓取失败回退 stale 缓存。
    """
    try:
        return get_nanhua_tree(force=force)
    except Exception as exc:
        logger.error("Failed to fetch nanhua report types: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取南华分类树失败"},
        )


@router.get(
    "/cih-index/report-categories",
    summary="中指指数报告一级分类（供 /cih-index/report/list/:report? 路由选参）",
    responses={500: {"model": ErrorResponse}},
)
def get_cih_index_report_categories(
    force: bool = Query(False, description="强制刷新缓存"),
):
    """代理中指指数报告列表页，返回 ``{categories: [{classId, className}]}``。

    供 ``/cih-index/report/list/:report?`` 路由的分类选择器使用。该路由的 ``report``
    参数是复合路径段（``f<classId>-p1-oaddtime-ddesc`` 形式，前缀表见
    ``_cih_index_categories``），上游元数据只给散文无可选列表，但报告列表页
    ``__INITIAL_STATE__.indNavLists`` 内嵌了 8 个一级分类。前端选分类后拼出合法
    路径段，避免盲填。6h 缓存，抓取失败回退 stale 缓存。
    """
    try:
        return get_cih_index_categories(force=force)
    except Exception as exc:
        logger.error("Failed to fetch cih-index categories: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取中指指数分类失败"},
        )


@router.get(
    "/cls/subjects",
    summary="财联社话题列表（供 /cls/subject/:id? 路由选参）",
    responses={500: {"model": ErrorResponse}},
)
def get_cls_subjects(
    force: bool = Query(False, description="强制刷新缓存"),
    keyword: Optional[str] = Query(None, description="按话题名过滤"),
):
    """代理财联社话题列表，返回 ``{subjects: [{subjectId, name, attention_num, link}]}``。

    供 ``/cls/subject/:id?`` 路由的参数选择器使用。财联社没有公开话题索引接口，
    这里以默认话题（``1103`` 盘面直播、``1151`` 有声早报）为种子分页抓取其文章 API，
    从文章附带的 ``subjects`` 字段收割话题去重、按关注度降序输出，让前端按话题名
    点选后填入 ``subjectId``。``id`` 可选，留空走 RSSHub 默认（盘面直播）。6h 缓存，
    抓取失败回退 stale 缓存。
    """
    try:
        return get_cls_subjects_list(force=force, keyword=keyword)
    except Exception as exc:
        logger.error("Failed to fetch cls subjects: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取财联社话题失败"},
        )


@router.get(
    "/futunn/topics",
    summary="富途牛牛话题列表（供 /futunn/topic/:id 路由选参）",
    responses={500: {"model": ErrorResponse}},
)
def get_futunn_topics_route(
    force: bool = Query(False, description="强制刷新缓存"),
    keyword: Optional[str] = Query(None, description="按话题名/简介过滤"),
):
    """代理富途牛牛话题列表，返回 ``{topics: [{topicId, title, detail, subscribed, timestamp, link}]}``。

    供 ``/futunn/topic/:id`` 路由的参数选择器使用。上游元数据只写
    "Topic ID, can be found in URL"，无可选列表，但富途公开话题列表接口
    ``news-site-api/main/get-topics-list``（分页，无需签名/cookie）返回每个话题的
    ``idx``/``title``/``detail``/``subscribed``。这里翻页累积全量、按订阅数降序输出，
    让前端按话题名点选后填入 ``idx``。``id`` 必填（路由无默认）。6h 缓存，抓取失败回退
    stale 缓存。
    """
    try:
        return get_futunn_topics(force=force, keyword=keyword)
    except Exception as exc:
        logger.error("Failed to fetch futunn topics: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取富途话题失败"},
        )


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
    # The list-mode item's already-rendered body, sent by the frontend so the
    # detail endpoint can fall back to it when the fulltext re-fetch produces a
    # poorer body (e.g. cih-index reports are image-only SPAs — fulltext returns
    # an empty `.page_main` shell, stripping the <img> pages the list already had),
    # or when the re-fetch comes back empty (see the fallback below).
    content_html: str = ""
    summary: str = ""
    image: str = ""
    # Metadata the list item already carries, forwarded so a synthesized
    # fallback (when the re-fetch comes back empty) keeps the published time /
    # author / tags / attachments the detail view renders.
    published: str = ""
    author: str = ""
    tags: List[str] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


def _build_detail_fallback(body: "FeedItemDetailRequest") -> Optional[Dict[str, Any]]:
    """Synthesize a detail item from the list-mode item when the fulltext
    re-fetch comes back empty.

    Returns ``None`` when the list item carried no content at all (no body, no
    summary, no image, no attachments) — in that case there is genuinely nothing
    to show and the caller should 404. Otherwise returns an item dict carrying
    the list item's title/link/body/metadata so the detail view renders the
    already-available content instead of a dead-end error.
    """
    has_body = any([
        (body.content_html or "").strip(),
        (body.summary or "").strip(),
        (body.image or "").strip(),
        bool(body.attachments),
    ])
    if not has_body:
        return None
    return {
        "id": body.item_id,
        "title": body.title,
        "link": body.link,
        "summary": body.summary,
        "published": body.published or None,
        "author": body.author,
        "tags": list(body.tags or []),
        "image": body.image,
        "content_html": body.content_html,
        "attachments": list(body.attachments or []),
    }


def _fulltext_lost_content(list_html: str, new_html: str) -> bool:
    """True when the fulltext re-fetch clearly failed to capture the article
    body — its text barely overlaps the list item's summary, meaning the
    re-fetch grabbed page chrome (nav/promo/footer) or an anti-crawl payload
    instead of real content. The list item's body is then the better source.

    The length-only fallback below can't catch this: a promo shell or a WAF
    noise page is often *longer* than the list summary, so it wins on size
    despite carrying zero article content. Concrete cases observed:
    /eastmoney/search — fulltext returns the SPA page shell (title echo +
    "东方财富APP …" promo block, 649 chars) while the list summary has the
    real 75-char excerpt; /xueqiu/timeline — fulltext returns a 34k-char WAF
    anti-crawl token blob. Both beat the list summary on length yet share no
    article text with it.
    """
    from api.v1.endpoints._rss_fetch import _html_to_text

    list_txt = _html_to_text(list_html)
    new_txt = _html_to_text(new_html)
    if not list_txt or not new_txt:
        return False  # nothing comparable; defer to the length fallback
    # n-grams are unreliable for very short summaries — use a plain substring.
    if len(list_txt) < 16:
        return list_txt not in new_txt
    n = 8
    grams = [list_txt[i:i + n] for i in range(0, len(list_txt) - n + 1, n)]
    if not grams:
        return list_txt not in new_txt
    hit = sum(1 for g in grams if g in new_txt)
    return (hit / len(grams)) < 0.3


@router.post(
    "/feeds/item",
    summary="获取单条 RSS 消息全文",
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
def get_rss_feed_item_detail(body: FeedItemDetailRequest):
    """只抓取当前选中消息的全文，供项目内详情阅读。"""
    # Re-fetch the whole feed in fulltext mode and match the selected item by
    # id/link/title. limit is sized to the typical list batch so the target is
    # likely present: RSSHub applies `filter_title` *after* `limit` truncation,
    # so a tiny limit can keep the target out of the filtered batch entirely
    # (e.g. /eeo/kuaixun returns a *different* item set at limit=5 than at the
    # list's default limit — the filter then matches nothing → 404).
    detail_limit = 30
    detail_options = dict(body.options or {})
    detail_options.pop("brief", None)
    detail_options.update({
        "mode": "fulltext",
        "limit": detail_limit,
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
        result = _fetch_rss_feed_json(feed_url, limit=detail_limit, timeout=45.0)
        if result.get("errors") and not result.get("items"):
            result = _fetch_rss_feed(feed_url, limit=detail_limit, timeout=30.0)
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
        # The fulltext re-fetch came back empty (filter_title found nothing in
        # the truncated batch, or the upstream returned no items). The list item
        # the user clicked already has its rendered body — fall back to it
        # instead of showing a misleading "未找到该消息内容" error. Many flash-news /
        # list feeds (e.g. /eeo/kuaixun) carry the *full* body in list mode, so
        # this is lossless; for feeds where fulltext genuinely adds more, the
        # user simply gets the list body (still readable) rather than a dead end.
        fallback = _build_detail_fallback(body)
        if fallback is not None:
            return fallback
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "未找到该消息内容"},
        )

    # Fulltext re-fetch can produce a *worse* body than the list already had.
    # Fall back to the list item's rendered fields when the re-fetched body is:
    #   - empty, or markedly shorter (image-only/SPA shells, e.g. cih-index
    #     fulltext returns an empty `.page_main`, stripping the list's <img>s), OR
    #   - captured page chrome / an anti-crawl payload instead of the article
    #     (e.g. /eastmoney/search fulltext returns the SPA promo shell "东方财富
    #     APP …" that is *longer* than the list excerpt but shares no article
    #     text; /xueqiu/timeline returns a 34k WAF token blob). Detected by a
    #     near-zero 8-gram overlap between the list summary and the re-fetched
    #     text — a longer-but-irrelevant body would otherwise win on size.
    list_html = (body.content_html or "").strip()
    new_html = str(selected.get("content_html") or "").strip()
    if list_html and (
        not new_html
        or len(new_html) < max(80, int(len(list_html) * 0.6))
        or _fulltext_lost_content(list_html, new_html)
    ):
        selected["content_html"] = body.content_html
        if not str(selected.get("summary") or "").strip() and body.summary:
            selected["summary"] = body.summary
        if not str(selected.get("image") or "").strip() and body.image:
            selected["image"] = body.image
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
    }


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


# ── PDF 代理（供前端 PDF.js 同源渲染）─────────────────────────────────────

@router.get(
    "/pdf/proxy",
    summary="代理下载 PDF 文件（供前端 PDF.js 同源渲染）",
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
def proxy_pdf(url: str = Query(..., description="PDF 原始 URL（须在白名单 host 内）")):
    """代理外部 PDF 字节，返回同源 ``Content-Disposition: inline`` 响应。

    解决两类问题：(1) 上游（如 ``mall.nanhua.net``）跨域无 CORS 头，前端 PDF.js
    直接 fetch 会失败；(2) 上游带 ``Content-Disposition: attachment`` 触发下载而非
    内联渲染。代理改写为 ``inline``，前端 PDF.js 用 canvas 渲染。

    SSRF 防护见 ``_pdf_proxy.py``：host 白名单 + 内网 IP 拦截 + 重定向二次校验 +
    ``%PDF-`` magic 校验。
    """
    from api.v1.endpoints._pdf_proxy import fetch_pdf, is_safe_pdf_url

    if not is_safe_pdf_url(url):
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": "不被允许的 PDF 来源"},
        )
    try:
        result = fetch_pdf(url)
    except Exception as exc:
        logger.warning("[RSS] PDF proxy fetch failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": f"PDF 下载失败: {exc}"},
        )
    if result is None:
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": "PDF 校验失败（非有效 PDF 或被重定向到非法地址）"},
        )
    content, _content_type = result
    headers = {
        "Content-Disposition": "inline",  # 覆盖上游 attachment，强制内联
        "Cache-Control": "private, max-age=3600",  # 1h 浏览器缓存，翻页不重复打代理
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type="application/pdf", headers=headers)
