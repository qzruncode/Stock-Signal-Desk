# -*- coding: utf-8 -*-
"""Explore-visibility filter for RSSHub routes — the single source of truth.

The frontend used to maintain three blacklist ``Set``s in ``rssRoute.ts`` and
filter client-side. They are retained for the finance-oriented Explore view
and as recommendation metadata. They are not an authorization boundary: the
Agent full-instance catalog keeps every RSSHub route discoverable.

These are **empirically fixed constants**, not live probes:
- ``KNOWN_BROKEN_ROUTES``: persistently 503/404 (verified by curl-ing each
  route's example). 503 root causes are classified upstream (anti-crawl,
  missing browser binary, upstream structure change) — none fixable by params.
- ``ENGLISH_ONLY_ROUTES``: return real items but <15% CJK (verified by
  fetching each example feed and scoring title+summary CJK ratio). Out of
  scope for a Chinese stock reader; NOT broken.
- ``UNUSEFUL_ROUTES``: return real, mostly-Chinese items but off-topic for a
  stock-analysis page (e-commerce, lifestyle, single-fund NAV, private-id
  user timelines, calendar feeds that duplicate news routes, etc.).

Re-verify periodically before pruning — upstreams recover.
"""

from __future__ import annotations

from typing import Any

# Persistently broken (503/404/garbage). Mirrors rssRoute.ts KNOWN_BROKEN_ROUTES.
KNOWN_BROKEN_ROUTES: set[str] = {
    "/bse/:category?/:keyword?",
    "/stream-capital/search",
    "/jin10/topic/:id",
    "/21caijing/channel/:name{.+}?",
    "/barronschina/:id?",
    "/caijing/roll",
    "/dtcj/datahero/:category?",
    "/dtcj/datainsight/:id?",
    "/nbd/:id?",
    "/taoguba/blog/:id",
    "/xueqiu/snb/:id",
    "/xueqiu/stock_comments/:id",
    "/xueqiu/stock_info/:id/:type?",
    "/xueqiu/today",
    "/xueqiu/favorite/:id",
    "/xueqiu/user/:id/:type?",
    "/xueqiu/user_stock/:id",
    "/xueqiu/column/:id",
    "/bloomberg/authors/:id/:slug/:source?",
    "/followin/tag/:tagId/:lang?",
    "/followin/topic/:topicId/:lang?",
    "/finology/bullets",
    "/finology/category/:category",
    "/finology/most-viewed",
    "/finology/tag/:topic",
    "/spglobal/ratings/:language?",
    "/seekingalpha/:symbol/:category?",
    "/eastmoney/gerenzhongxin/trpl/:uid",
    "/eastmoney/gerenzhongxin/cfh/:uid",
    "/eastmoney/gerenzhongxin/guba/:uid",
    "/eastmoney/ttjj/user/:uid",
    "/eastmoney/gerenzhongxin/gather/:uid",
    # cs/video: fetch works but item body is an embedded <video> whose fulltext
    # re-fetch loses controls/poster → renders as a blank rectangle, and the http
    # src is mixed-content blocked on https. Unplayable in-page == unusable.
    "/cs/video/:category?",
}

# English-only feeds (real items, <15% CJK). Mirrors ENGLISH_ONLY_ROUTES.
ENGLISH_ONLY_ROUTES: set[str] = {
    "/ainvest/article",
    "/ainvest/news",
    "/blockworks/",
    "/bloomberg/:site?",
    "/bullionvault/gold-news/:category?",
    "/fastbull/express-news",
    "/finviz/:category?",
    "/finviz/news/:ticker",
    "/followin/:categoryId?/:lang?",
    "/followin/kol/:kolId/:lang?",
    "/fx-markets/:channel",
    "/jpmorganchase/",
    "/stockedge/daily-updates/news",
    "/unusualwhales/news",
}

# Works + mostly Chinese, but not useful on a stock explore page.
# Mirrors UNUSEFUL_ROUTES.
UNUSEFUL_ROUTES: set[str] = {
    "/zhizhuan100/analytic",
    "/zhitongcaijing/:id?/:category?",
    "/youzhiyouxing/materials/:id?",
    "/xueqiu/fund/:id",
    "/xueqiu/timeline/:usergroup_id?",
    "/wallstreetcn/calendar/:section?",
    "/ulapia/research/latest",
    "/ulapia/reports/:category?",
    "/baidu/gushitong/index",
    "/bigquant/collections",
    "/chinamoney/:channelId?",
    "/chinaratings/CreditResearch/:category{.+}?",
    "/fastbull/news",
    "/futunn/video",
    "/gelonghui/user/:id",
    "/gov/pbc/goutongjiaoliu",
    "/gov/pbc/gzlw",
    "/huijin-inv/news",
    "/jisilu/category/:id",
    "/jisilu/explore/:filter?",
    "/jisilu/people/:id/:type?",
    "/jisilu/topic/:id",
    "/jiuyangongshe/community",
    "/laohu8/personal/:id",
    "/lhratings/research/:type?",
    "/mrm/:category?",
    "/nbd/daily",
    "/sse/convert/:query?",
    "/sselawsrules/:category{.+}?",
    "/taoguba/:category?",
    "/szse/rule/:channel{.+}?",
}

# Union for O(1) membership test.
_HIDDEN_ROUTES: set[str] = KNOWN_BROKEN_ROUTES | ENGLISH_ONLY_ROUTES | UNUSEFUL_ROUTES


def is_hidden_from_explore(route: Any) -> bool:
    """Whether a route should be hidden from the explore list / assistant catalog.

    Accepts either a ``route_path`` string or a route descriptor dict carrying
    ``route_path`` (matches both call sites).
    """
    if isinstance(route, str):
        path = route
    elif isinstance(route, dict):
        path = route.get("route_path") or ""
    else:
        return False
    return path in _HIDDEN_ROUTES


def route_readiness(
    route: Any,
) -> tuple[str, bool, str | None]:
    """Describe availability separately from default recommendation.

    Historical explore-page curation must never become an authorization
    boundary for the Agent. Broken routes stay discoverable as unavailable;
    English and off-topic routes stay available but are not auto-selected for
    Chinese stock analysis.
    """
    if isinstance(route, str):
        path = route
        features: dict[str, Any] = {}
    elif isinstance(route, dict):
        path = str(route.get("route_path") or "")
        raw_features = route.get("features")
        features = raw_features if isinstance(raw_features, dict) else {}
    else:
        path = ""
        features = {}
    if path in KNOWN_BROKEN_ROUTES:
        return "unavailable", False, "历史健康检查显示该路由持续不可用"
    if bool(features.get("requireConfig")):
        return "requires_configuration", False, "路由需要实例侧配置"
    if path in ENGLISH_ONLY_ROUTES:
        return "available", False, "英文来源，仅在明确请求或相关性足够时使用"
    if path in UNUSEFUL_ROUTES:
        return "available", False, "不属于默认股票研究来源，但可被明确调用"
    return "available", True, None


__all__ = [
    "ENGLISH_ONLY_ROUTES",
    "KNOWN_BROKEN_ROUTES",
    "UNUSEFUL_ROUTES",
    "is_hidden_from_explore",
    "route_readiness",
]
