# -*- coding: utf-8 -*-

import pytest

from api.v1.endpoints import _rss_reader, rss
from api.v1.endpoints._rss_fetch import (
    _build_feed_url,
    _build_feed_url_generic,
    _derive_item_title,
    _html_to_text,
    _is_unresolvable_truncated_item,
    _normalize_content_html,
    _readable_http_url,
)
from api.v1.endpoints._pdf_proxy import _acw_cookie_from_challenge, _allowed_hosts


def test_rsshub_route_templates_cover_finance_sources():
    assert _build_feed_url("wallstreetcn").endswith("/wallstreetcn/news/global")
    assert _build_feed_url("cls").endswith("/cls/telegraph")
    assert _build_feed_url("sina_finance", category="stock/usstock").endswith(
        "/sina/finance/stock/usstock"
    )
    assert _build_feed_url("36kr", category="information/web_news").endswith(
        "/36kr/information/web_news"
    )
    assert _build_feed_url("eastmoney_search", keyword="贵州茅台").endswith(
        "/eastmoney/search/%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0"
    )
    assert _build_feed_url("szse_disclosure", stock_code="SZ000001").endswith(
        "/szse/disclosure/listed/notice/stock=000001"
    )
    assert _build_feed_url("bloomberg_markets").endswith("/bloomberg/markets")
    assert _build_feed_url("chinamoney").endswith("/chinamoney")


def test_generic_route_builder_validates_and_renders_dynamic_params():
    url = _build_feed_url_generic(
        "/jin10/category/:id/:type?",
        {"id": "36"},
        {"limit": 20},
        namespace="jin10",
    )
    assert url.endswith("/jin10/category/36?limit=20")
    with pytest.raises(ValueError, match="缺少必填参数: id"):
        _build_feed_url_generic("/jin10/category/:id", {})


def test_xueqiu_fund_code_is_not_prefixed_with_exchange():
    # /xueqiu/fund/:id takes a 6-digit fund code (e.g. 040008). It must NOT get
    # the SH/SZ/BJ stock prefix the xueqiu namespace default applies — that
    # would turn 040008 into SZ040008 and upstream returns 503.
    url = _build_feed_url_generic(
        "/xueqiu/fund/:id",
        {"id": "040008"},
        {},
        namespace="xueqiu",
    )
    assert url.endswith("/xueqiu/fund/040008")


def test_xueqiu_stock_code_still_gets_exchange_prefix():
    # The namespace default must still apply to stock routes (regression guard).
    url = _build_feed_url_generic(
        "/xueqiu/stock_info/:id",
        {"id": "000002"},
        {},
        namespace="xueqiu",
    )
    assert url.endswith("/xueqiu/stock_info/SZ000002")


def test_rss_item_link_only_accepts_readable_web_urls():
    assert _readable_http_url("https://example.com/news/1") == "https://example.com/news/1"
    assert _readable_http_url("http://example.com/news/1") == "http://example.com/news/1"
    assert _readable_http_url("2a5b077b-224c-43fc-bdf2-4bb5efdfe480") == ""
    assert _readable_http_url("/relative/article") == ""


def test_rss_drops_only_truncated_items_without_a_resolvable_article():
    assert _is_unresolvable_truncated_item("Incomplete headline...", "") is True
    assert _is_unresolvable_truncated_item("Complete flash headline", "") is False
    assert _is_unresolvable_truncated_item("Article headline...", "https://example.com/article") is False
    assert _is_unresolvable_truncated_item("Headline...", "", "Complete full message") is False


def test_rss_html_is_normalized_without_losing_message_text():
    html = '&lt;p&gt;监管部门核实，消息不属实。&lt;/p&gt;'
    assert _normalize_content_html(html) == '<p>监管部门核实，消息不属实。</p>'
    assert _html_to_text('<p><a href="https://example.com">希音</a>已获备案。</p>') == "希音已获备案。"
    long_text = "重要消息" * 200
    assert _html_to_text(f"<p>{long_text}</p>") == long_text
    assert _derive_item_title("", "第一行正文\n第二行") == "第一行正文"
    assert _derive_item_title("", "") == ""


def test_pdf_proxy_defaults_cover_exchange_announcement_hosts():
    hosts = _allowed_hosts()
    assert "static.sse.com.cn" in hosts
    assert "disc.static.szse.cn" in hosts


def test_sse_acw_challenge_cookie_is_computed_deterministically():
    challenge = b"<script>var arg1='74748CC96A87DE5AB56B6E5330755545B9AB6DD8';</script>"
    assert _acw_cookie_from_challenge(challenge) == "6a53ad1da6e1e7e33bddd4ead6532cc49ff5950e"
    assert _acw_cookie_from_challenge(b"<html>ordinary error</html>") is None


def test_detail_uses_list_item_when_fulltext_upstream_fails(monkeypatch):
    failed = {"items": [], "errors": ["RSSHub 503"]}
    monkeypatch.setattr(rss, "_cache_get", lambda _key: None)
    monkeypatch.setattr(rss, "_fetch_rss_feed_json", lambda *_args, **_kwargs: failed)
    monkeypatch.setattr(rss, "_fetch_rss_feed", lambda *_args, **_kwargs: failed)

    item = rss.get_rss_feed_item_detail(rss.FeedItemDetailRequest(
        route_path="/example/news",
        title="可读消息",
        link="https://example.com/news/1",
        content_html="<p>列表中已有完整正文。</p>",
    ))

    assert item["title"] == "可读消息"
    assert item["content_html"] == "<p>列表中已有完整正文。</p>"


def test_assistant_reader_uses_list_item_when_fulltext_upstream_fails(monkeypatch):
    failed = {"items": [], "errors": ["RSSHub 503"]}
    monkeypatch.setattr(_rss_reader, "_cache_get", lambda _key: None)
    monkeypatch.setattr(_rss_reader, "_fetch_rss_feed_json", lambda *_args, **_kwargs: failed)
    monkeypatch.setattr(_rss_reader, "_fetch_rss_feed", lambda *_args, **_kwargs: failed)

    item = _rss_reader.read_item(
        "/example/news",
        title="可读消息",
        link="https://example.com/news/1",
        list_content_html="<p>列表中已有完整正文。</p>",
    )

    assert item["content_text"] == "列表中已有完整正文。"
    assert item["_fallback"] is True
    assert item["_not_found"] is False
