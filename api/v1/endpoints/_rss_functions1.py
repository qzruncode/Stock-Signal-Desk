"""Route function group 1 extracted from api/v1/endpoints/rss.py."""

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

__all__ = ['get_rss_namespaces', 'get_rss_namespace', 'get_rss_categories', 'get_rss_catalog_endpoint', 'get_gelonghui_subjects', 'get_nanhua_report_types', 'get_cih_index_report_categories', 'get_cls_subjects', 'get_futunn_topics_route', 'get_rss_feeds_by_spec', '_build_detail_fallback', '_fulltext_lost_content']

@router.get(
    "/namespaces",
    summary="获取 RSSHub 全量路由（扁平化）",
    responses={500: {"model": ErrorResponse}},
)
def get_rss_namespaces(
    force: bool = Query(False, description="强制刷新缓存"),
    finance_only: bool = Query(True, description="仅返回股市相关路由(财经分类,排除纯加密)"),
    include_hidden: bool = Query(False, description="包含降级、英文和非默认推荐路由"),
):
    """代理 RSSHub /api/namespace，返回扁平化路由列表供前端浏览/搜索/订阅。

    默认 finance_only=true：只返回影响股市的路由(A股/港美股/外汇/大宗/宏观/央行)，
    排除纯加密货币。传 finance_only=false 可看全量。
    """
    try:
        return get_namespaces_flat(
            force=force,
            finance_only=finance_only,
            include_hidden=include_hidden,
        )
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
    "/catalog",
    summary="获取 AI 助手友好的 RSS 源目录（精筛财经路由 + 参数提示）",
    responses={500: {"model": ErrorResponse}},
)
def get_rss_catalog_endpoint(
    force: bool = Query(False, description="强制刷新缓存"),
):
    """返回 agent 友好的 RSS 源目录：基于已过滤的 ~47 条财经路由，每条带
    中文用途描述 + 参数提示（名称/必填/hint/默认/选项）。供 AI 助手的
    Infos 页面和语义财经资讯工具共用，6h 缓存。
    """
    try:
        return _get_rss_catalog(force=force)
    except Exception as exc:
        logger.error("Failed to build RSS catalog: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "获取 RSS 目录失败"},
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
                body.route_path,
                body.params,
                effective_options,
                namespace=body.namespace,
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
        # This is still the same selected RSS item, but it is not a successful
        # fulltext re-fetch.  Consumers that need complete article coverage
        # must be able to distinguish it from a source-provided full body.
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
    from api.v1.endpoints._rss_fetch import _html_to_text

    list_txt = _html_to_text(list_html)
    new_txt = _html_to_text(new_html)
    if not list_txt or not new_txt:
        return False  # nothing comparable; defer to the length fallback
    # n-grams are unreliable for very short summaries — use a plain substring.
    if len(list_txt) < 16:
        return list_txt not in new_txt
    n = 8
    grams = [list_txt[i : i + n] for i in range(0, len(list_txt) - n + 1, n)]
    if not grams:
        return list_txt not in new_txt
    hit = sum(1 for g in grams if g in new_txt)
    return (hit / len(grams)) < 0.3
