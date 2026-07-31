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
from api.v1.endpoints._rss_catalog import get_rss_catalog as _get_rss_catalog
from api.v1.endpoints._gelonghui_subjects import get_subjects as get_gelonghui_subjects_list
from api.v1.endpoints._nanhua_tree import get_nanhua_tree
from api.v1.endpoints._cih_index_categories import get_cih_index_categories
from api.v1.endpoints._cls_subjects import get_cls_subjects as get_cls_subjects_list
from api.v1.endpoints._futunn_topics import get_futunn_topics

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 路由发现（namespace discovery）───────────────────────────────────────



class FeedSpecRequest(BaseModel):
    route_path: str = Field(..., description="RSSHub 路由模板，如 /wallstreetcn/news/:category?")
    params: Dict[str, Any] = Field(default_factory=dict, description="路径参数值")
    options: Dict[str, Any] = Field(default_factory=dict, description="RSSHub 通用选项 (limit/filter/mode/format/...)")
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


from . import _rss_functions1 as _rss_functions1
from . import _rss_functions2 as _rss_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_rss_functions1, _rss_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))
