"""Route function group 2 extracted from api/v1/endpoints/rss.py."""

from __future__ import annotations

from api.v1.endpoints.rss import (
    logging,
    re,
    datetime,
    Path,
    Any,
    Dict,
    List,
    Optional,
    quote,
    urlencode,
    dotenv_values,
    APIRouter,
    HTTPException,
    Query,
    Response,
    BaseModel,
    Field,
    ErrorResponse,
    Config,
    DatabaseManager,
    _build_feed_url_generic,
    _fetch_rss_feed,
    _fetch_rss_feed_json,
    _rss_cache_key_generic,
    _cache_get,
    _cache_put,
    get_namespaces_flat,
    get_namespace_detail,
    get_categories,
    _get_rss_catalog,
    get_gelonghui_subjects_list,
    get_nanhua_tree,
    get_cih_index_categories,
    get_cls_subjects_list,
    get_futunn_topics,
    logger,
    router,
    FeedSpecRequest,
    FeedItemDetailRequest,
    _MEDIA_TYPES,
    RawFeedRequest,
    HtmlTransformRequest,
    _HTML_PARAM_KEYS,
 )

__all__ = ['get_rss_feed_item_detail', 'get_rss_feeds_raw', '_build_html_transform_url', '_build_html_transform_url_from_params', 'transform_html', '_rsshub_env_path', '_xueqiu_cookies_configured', 'test_xueqiu_cookie', 'proxy_pdf']

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
    detail_options.update(
        {
            "mode": "fulltext",
            "limit": detail_limit,
        }
    )
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
    cached = None if body.force else _cache_get(cache_key)
    if cached and isinstance(cached, dict) and cached.get("items"):
        result = cached
    else:
        result = _fetch_rss_feed_json(feed_url, limit=detail_limit, timeout=45.0)
        if result.get("errors") and not result.get("items"):
            result = _fetch_rss_feed(feed_url, limit=detail_limit, timeout=30.0)
        if result.get("items"):
            _cache_put(cache_key, result)

    if result.get("errors") and not result.get("items"):
        # The list item is already on screen and may contain the complete body,
        # image or attachment.  A transient fulltext refresh failure must not
        # turn that readable item into a dead-end detail error.
        fallback = _build_detail_fallback(body)
        if fallback is not None:
            _cache_put(cache_key, {"items": [fallback]})
            return fallback
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_error", "message": result["errors"][0]},
        )

    items = result.get("items") or []
    # Match the clicked item by id/link/title. We deliberately do NOT fall back
    # to items[0] when nothing matches: detail_options carries filter_title=
    # ^{title}$, so a non-empty `items` that misses the target means the filter
    # returned *other* entries (title differs by whitespace/entity/width between
    # list and fulltext modes). Returning items[0] there would surface the wrong
    # article's full text. Instead fall through to the list-item fallback below —
    # the body the user clicked already has is the correct, lossless choice for
    # list-mode feeds, and a dead-end 404 is more honest than a wrong article.
    selected = next(
        (
            item
            for item in items
            if (body.item_id and item.get("id") == body.item_id)
            or (body.link and item.get("link") == body.link)
            or (body.title and item.get("title") == body.title)
        ),
        None,
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
            _cache_put(cache_key, {"items": [fallback]})
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
            body.route_path,
            body.params,
            opts,
            namespace=body.namespace,
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
    # extra options merged (limit/filter/...)
    for k, v in (body.options or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        route_params[str(k)] = str(v)

    encoded_params = quote(urlencode(route_params), safe="")
    return f"{base_url}/rsshub/transform/html/{encoded_url}/{encoded_params}"

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
