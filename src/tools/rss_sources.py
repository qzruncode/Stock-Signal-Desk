"""Audited capability map for the 47 finance routes shown on the Infos page.

The route catalog is dynamic metadata from RSSHub.  This file is the stable
business interpretation used by Stock Agent tools: it states what each route
is allowed to answer, so semantic selection cannot accidentally use an exchange
inquiry feed as a research-report source.
"""

from __future__ import annotations


RSS_ROUTE_CAPABILITIES: dict[str, frozenset[str]] = {
    "/10jqka/realtimenews/:tag?": frozenset({"market", "realtime"}),
    "/cih-index/report/list/:report?": frozenset({"research", "industry"}),
    "/cls/telegraph/:category?": frozenset({"market", "realtime"}),
    "/cls/depth/:category?": frozenset({"market", "company", "industry"}),
    "/cls/hot": frozenset({"market", "social"}),
    "/cls/subject/:id?": frozenset({"market", "company", "industry"}),
    "/eastmoney/search/:keyword": frozenset({"company", "industry"}),
    "/eastmoney/report/:category": frozenset({"research", "company", "industry", "macro"}),
    "/eeo/kuaixun": frozenset({"market", "realtime"}),
    "/followin/news/:lang?": frozenset({"market", "company"}),
    "/futunn/live/:lang?": frozenset({"market", "realtime"}),
    "/futunn/main": frozenset({"market", "company"}),
    "/futunn/topic/:id": frozenset({"company", "industry"}),
    "/fx678/kx": frozenset({"market", "macro", "realtime"}),
    "/gelonghui/live": frozenset({"market", "realtime"}),
    "/gelonghui/hot-article/:type?": frozenset({"market", "social"}),
    "/gelonghui/subject/:id": frozenset({"company", "industry"}),
    "/gelonghui/home/:tag?": frozenset({"market", "company", "industry"}),
    "/gelonghui/keyword/:keyword": frozenset({"company", "industry"}),
    "/gov/pbc/tradeAnnouncement": frozenset({"macro", "monetary_policy", "regulatory"}),
    "/hexun/pe/news": frozenset({"company", "industry"}),
    "/investor/:id{.+}?": frozenset({"company", "industry"}),
    "/jin10/category/:id": frozenset({"market", "macro", "realtime"}),
    "/jin10/:important?": frozenset({"market", "macro", "realtime"}),
    "/jrj/:channelNum": frozenset({"market", "company"}),
    "/mckinsey/cn/:category?": frozenset({"research", "industry", "macro"}),
    "/moodysmismicrosite/report/:industry?": frozenset({"research", "industry", "macro"}),
    "/nanhua/report/:type1/:type2": frozenset({"research", "industry", "macro"}),
    "/nifd/research/:categoryGuid?": frozenset({"research", "macro", "industry"}),
    "/qianzhan/analyst/column/:type?": frozenset({"research", "industry"}),
    "/qianzhan/analyst/rank/:type?": frozenset({"research", "industry", "ranking"}),
    "/sse/disclosure/:query?": frozenset({"announcement", "regulatory", "company"}),
    "/sse/inquire": frozenset({"announcement", "regulatory"}),
    "/sse/renewal": frozenset({"announcement", "regulatory", "project"}),
    "/stcn/article/list/:id?": frozenset({"market", "company", "industry"}),
    "/stcn/article/list/kx": frozenset({"market", "realtime"}),
    "/stcn/article/rank/:id?": frozenset({"market", "social"}),
    "/szse/inquire/:category?/:select?/:keyword?": frozenset({"announcement", "regulatory"}),
    "/szse/projectdynamic/:type?/:stage?/:status?": frozenset({"announcement", "regulatory", "project"}),
    "/szse/disclosure/listed/notice/:query?": frozenset({"announcement", "regulatory", "company"}),
    "/szse/notice": frozenset({"announcement", "regulatory", "company", "listing"}),
    "/wabei/hot-recommend": frozenset({"market", "company", "social"}),
    "/wallstreetcn/hot/:period?": frozenset({"market", "social"}),
    "/wallstreetcn/news/:category?": frozenset({"market", "company", "macro", "industry"}),
    "/wallstreetcn/live/:category?/:score?": frozenset({"market", "macro", "realtime"}),
    "/wkjyqh/research": frozenset({"research", "industry", "macro"}),
    "/xueqiu/hots": frozenset({"social", "market"}),
}


def routes_for_capability(capability: str) -> frozenset[str]:
    return frozenset(
        path
        for path, capabilities in RSS_ROUTE_CAPABILITIES.items()
        if capability in capabilities
    )


TOPIC_ROUTE_PATHS: dict[str, frozenset[str]] = {
    topic: routes_for_capability(topic)
    for topic in ("market", "company", "announcement", "research", "macro", "industry", "social")
}


__all__ = ["RSS_ROUTE_CAPABILITIES", "TOPIC_ROUTE_PATHS", "routes_for_capability"]
