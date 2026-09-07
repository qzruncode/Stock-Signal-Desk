"""Existing rich RSS reader acquisition, now service-owned."""

from __future__ import annotations
import base64
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode
from fastapi import HTTPException
from pydantic import BaseModel, Field
from market_data_service.providers.rss_config import Config
from market_data_service.providers.rss_fetch import (
    _build_feed_url_generic,
    _fetch_rss_feed,
    _fetch_rss_feed_json,
)
from market_data_service.providers.rss_cache import (
    _rss_cache_key_generic,
    _cache_get,
    _cache_put,
)

logger = logging.getLogger(__name__)


class FeedSpecRequest(BaseModel):
    route_path: str = Field(
        ..., description="RSSHub 路由模板，如 /wallstreetcn/news/:category?"
    )
    params: Dict[str, Any] = Field(default_factory=dict, description="路径参数值")
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description="RSSHub 通用选项 (limit/filter/mode/format/...)",
    )
    namespace: Optional[str] = Field(None, description="命名空间（用于参数格式化器）")
    limit: int = Field(30, ge=1, le=100, description="返回条数")
    force: bool = Field(False, description="强制刷新（跳过缓存）")


class FeedItemDetailRequest(BaseModel):
    route_path: str
    params: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    namespace: Optional[str] = None
    item_id: str = ""
    title: str = ""
    link: str = ""
    force: bool = Field(False, description="强制刷新（跳过缓存，重新 fulltext 抓取）")
    content_html: str = ""
    summary: str = ""
    image: str = ""
    published: str = ""
    author: str = ""
    tags: List[str] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class RawFeedRequest(BaseModel):
    route_path: str
    params: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    namespace: Optional[str] = None
    format: str = Field("rss", description="输出格式: rss/atom/json/rss3")
    limit: int = Field(30, ge=1, le=100)
    text_only: bool = Field(True, description="移除图片、音频和视频资源")


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
    options: Dict[str, Any] = Field(
        default_factory=dict, description="额外 RSSHub 通用选项"
    )


_MEDIA_TYPES = {
    "rss": "application/rss+xml; charset=utf-8",
    "atom": "application/atom+xml; charset=utf-8",
    "json": "application/feed+json; charset=utf-8",
    "rss3": "application/json; charset=utf-8",
}
_HTML_PARAM_KEYS = {
    "url",
    "title",
    "item",
    "itemTitle",
    "itemTitleAttr",
    "itemLink",
    "itemLinkAttr",
    "itemDesc",
    "itemDescAttr",
    "itemPubDate",
    "itemPubDateAttr",
    "itemContent",
    "encoding",
}


def get_rss_feeds_by_spec(body: FeedSpecRequest):
    """通用取数：用 params 渲染 route_path，追加 options，抓取并缓存。

    特殊路由 /rsshub/transform/html（HTML→RSS 万能转换器）：params 携带 url + CSS
    选择器，按转换器规则构建 URL。
    """
    effective_options = dict(body.options or {})
    effective_options.pop("brief", None)
    try:
        effective_limit = max(
            1, min(100, int(effective_options.get("limit") or body.limit))
        )
    except (TypeError, ValueError):
        effective_limit = body.limit
    try:
        if body.route_path.rstrip("/") == "/rsshub/transform/html":
            feed_url = _build_html_transform_url_from_params(
                body.params, effective_options, effective_limit
            )
        else:
            feed_url = _build_feed_url_generic(
                body.route_path,
                body.params,
                effective_options,
                namespace=body.namespace,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "validation_error", "message": str(exc)}
        )
    cache_key = _rss_cache_key_generic(body.route_path, body.params, effective_options)
    if not body.force:
        cached = _cache_get(cache_key)
        if cached and isinstance(cached, dict) and cached.get("items"):
            cached["_cached"] = True
            return cached
    result = _fetch_rss_feed_json(feed_url, limit=effective_limit)
    if result.get("errors") and (not result.get("items")):
        logger.info(
            "[RSS] JSON fetch failed, falling back to XML: %s", result["errors"][0]
        )
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


def _build_detail_fallback(body: "FeedItemDetailRequest") -> Optional[Dict[str, Any]]:
    """Synthesize a detail item from the list-mode item when the fulltext
    re-fetch comes back empty.

    Returns ``None`` when the list item carried no content at all (no body, no
    summary, no image, no attachments) — in that case there is genuinely nothing
    to show and the caller should 404. Otherwise returns an item dict carrying
    the list item's title/link/body/metadata so the detail view renders the
    already-available content instead of a dead-end error.
    """
    has_body = any(
        [
            (body.content_html or "").strip(),
            (body.summary or "").strip(),
            (body.image or "").strip(),
            bool(body.attachments),
        ]
    )
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
        "_content_origin": "list_item_fallback",
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
    from market_data_service.providers.rss_fetch import _html_to_text

    list_txt = _html_to_text(list_html)
    new_txt = _html_to_text(new_html)
    if not list_txt or not new_txt:
        return False
    if len(list_txt) < 16:
        return list_txt not in new_txt
    n = 8
    grams = [list_txt[i : i + n] for i in range(0, len(list_txt) - n + 1, n)]
    if not grams:
        return list_txt not in new_txt
    hit = sum((1 for g in grams if g in new_txt))
    return hit / len(grams) < 0.3


def get_rss_feed_item_detail(body: FeedItemDetailRequest):
    """只抓取当前选中消息的全文，供项目内详情阅读。"""
    detail_limit = 30
    detail_options = dict(body.options or {})
    detail_options.pop("brief", None)
    detail_options.update({"mode": "fulltext", "limit": detail_limit})
    if body.title:
        detail_options["filter_title"] = f"^{re.escape(body.title)}$"
    try:
        feed_url = _build_feed_url_generic(
            body.route_path, body.params, detail_options, namespace=body.namespace
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "validation_error", "message": str(exc)}
        )
    cache_key = _rss_cache_key_generic(body.route_path, body.params, detail_options)
    cached = None if body.force else _cache_get(cache_key)
    if cached and isinstance(cached, dict) and cached.get("items"):
        result = cached
    else:
        result = _fetch_rss_feed_json(feed_url, limit=detail_limit, timeout=45.0)
        if result.get("errors") and (not result.get("items")):
            result = _fetch_rss_feed(feed_url, limit=detail_limit, timeout=30.0)
        if result.get("items"):
            _cache_put(cache_key, result)
    if result.get("errors") and (not result.get("items")):
        fallback = _build_detail_fallback(body)
        if fallback is not None:
            _cache_put(cache_key, {"items": [fallback]})
            from market_data_service.providers.rss_text import normalize_text_item

            return normalize_text_item(fallback)
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": result["errors"][0]},
        )
    items = result.get("items") or []
    selected = next(
        (
            item
            for item in items
            if body.item_id
            and item.get("id") == body.item_id
            or (body.link and item.get("link") == body.link)
            or (body.title and item.get("title") == body.title)
        ),
        None,
    )
    if not selected:
        fallback = _build_detail_fallback(body)
        if fallback is not None:
            _cache_put(cache_key, {"items": [fallback]})
            from market_data_service.providers.rss_text import normalize_text_item

            return normalize_text_item(fallback)
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "未找到该消息内容"},
        )
    list_html = (body.content_html or "").strip()
    new_html = str(selected.get("content_html") or "").strip()
    if list_html and (
        not new_html
        or len(new_html) < max(80, int(len(list_html) * 0.6))
        or _fulltext_lost_content(list_html, new_html)
    ):
        selected["content_html"] = body.content_html
        selected["_content_origin"] = "list_item_fallback"
        if not str(selected.get("summary") or "").strip() and body.summary:
            selected["summary"] = body.summary
        if not str(selected.get("image") or "").strip() and body.image:
            selected["image"] = body.image
    from market_data_service.providers.rss_text import normalize_text_item

    return normalize_text_item(selected)


def get_rss_feeds_raw(body: RawFeedRequest):
    """按 FeedSpec 构建带 format 的 RSSHub URL，原样透传字节与正确 Content-Type。"""
    fmt = (body.format or "rss").lower()
    if fmt not in _MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": f"不支持的格式: {fmt}"},
        )
    try:
        opts = dict(body.options or {})
        opts["format"] = fmt
        opts.setdefault("limit", body.limit)
        feed_url = _build_feed_url_generic(
            body.route_path, body.params, opts, namespace=body.namespace
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "validation_error", "message": str(exc)}
        )
    try:
        import requests as _requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = _requests.get(feed_url, headers=headers, timeout=30.0)
        resp.raise_for_status()
    except _requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": f"RSSHub 请求失败: {exc}"},
        )
    content = resp.content
    if body.text_only:
        from market_data_service.providers.rss_text import filter_raw_feed_bytes

        content = filter_raw_feed_bytes(content, fmt)
    return {
        "body_base64": base64.b64encode(content).decode("ascii"),
        "media_type": _MEDIA_TYPES[fmt],
    }


def _build_html_transform_url(body: "HtmlTransformRequest") -> str:
    """构建 RSSHub /rsshub/transform/html/:url/:routeParams URL。"""
    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    encoded_url = quote(body.url.strip(), safe="")
    route_params: Dict[str, str] = {}
    field_map = {
        "title": body.title,
        "item": body.item,
        "itemTitle": body.item_title,
        "itemTitleAttr": body.item_title_attr,
        "itemLink": body.item_link,
        "itemLinkAttr": body.item_link_attr,
        "itemDesc": body.item_desc,
        "itemDescAttr": body.item_desc_attr,
        "itemPubDate": body.item_pubdate,
        "itemPubDateAttr": body.item_pubdate_attr,
        "itemContent": body.item_content,
        "encoding": body.encoding,
    }
    for k, v in field_map.items():
        if v is not None and str(v).strip():
            route_params[k] = str(v).strip()
    for k, v in (body.options or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and (not v.strip()):
            continue
        route_params[str(k)] = str(v)
    encoded_params = quote(urlencode(route_params), safe="")
    return f"{base_url}/rsshub/transform/html/{encoded_url}/{encoded_params}"


def _build_html_transform_url_from_params(
    params: Dict[str, Any], options: Dict[str, Any], limit: int
) -> str:
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
    for k, v in (options or {}).items():
        if k == "format":
            continue
        if v is None:
            continue
        if isinstance(v, str) and (not v.strip()):
            continue
        route_params[str(k)] = str(v)
    route_params.setdefault("limit", str(limit))
    base_url = Config.get_instance().rsshub_base_url.rstrip("/")
    encoded_url = quote(url, safe="")
    encoded_params = quote(urlencode(route_params), safe="")
    return f"{base_url}/rsshub/transform/html/{encoded_url}/{encoded_params}"


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
    result = _fetch_rss_feed_json(feed_url, limit=body.limit)
    if result.get("errors") and (not result.get("items")):
        result = _fetch_rss_feed(feed_url, limit=body.limit)
    if result.get("errors") and (not result.get("items")):
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": result["errors"][0]},
        )
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
