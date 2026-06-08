# -*- coding: utf-8 -*-

from api.v1.endpoints import rss


def test_rsshub_route_templates_cover_finance_sources():
    assert rss._build_feed_url("wallstreetcn").endswith("/wallstreetcn/news/global")
    assert rss._build_feed_url("cls").endswith("/cls/telegraph")
    assert rss._build_feed_url("sina_finance", category="stock/usstock").endswith(
        "/sina/finance/stock/usstock"
    )
    assert rss._build_feed_url("36kr", category="information/web_news").endswith(
        "/36kr/information/web_news"
    )
    assert rss._build_feed_url("eastmoney_search", keyword="贵州茅台").endswith(
        "/eastmoney/search/%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0"
    )
    assert rss._build_feed_url("eastmoney_guba_user", uid="12345").endswith(
        "/eastmoney/gerenzhongxin/guba/12345"
    )
    assert rss._build_feed_url("szse_disclosure", stock_code="SZ000001").endswith(
        "/szse/disclosure/listed/notice/stock=000001"
    )
    assert rss._build_feed_url("bloomberg_markets").endswith("/bloomberg/markets")
    assert rss._build_feed_url("chinamoney").endswith("/chinamoney")


def test_rss_sources_expose_dynamic_inputs():
    sources = {source["id"]: source for source in rss.get_rss_sources()["sources"]}

    assert sources["eastmoney_search"]["requires_keyword"] is True
    assert sources["eastmoney_guba_user"]["requires_uid"] is True
    assert sources["szse_disclosure"]["stock_placeholder"] == "股票代码 (如 000001)"
