from __future__ import annotations

from unittest.mock import patch

import pytest

from api.v1.endpoints.agent.tools import _compact_tool_result
from src.tools.export_financial_feed import export_financial_feed
from src.tools.inspect_financial_source import inspect_financial_source
from src.tools.list_financial_sources import list_financial_sources
from src.tools.read_financial_article import read_financial_article
from src.tools.read_financial_feed import read_financial_feed
from src.tools.registry import ToolRegistry
from src.tools.transform_webpage_to_feed import transform_webpage_to_feed


CATALOG = {
    "routes": [
        {
            "route_path": "/cls/telegraph/:category?",
            "name": "财联社电报",
            "namespace": "cls",
            "namespace_name": "财联社",
            "description": "实时财经电报",
            "example": "/cls/telegraph",
            "params": [],
        },
        {
            "route_path": "/cls/subject/:id?",
            "name": "财联社主题",
            "namespace": "cls",
            "namespace_name": "财联社",
            "description": "指定主题资讯",
            "example": "/cls/subject/1000",
            "params": [{"name": "id"}],
        },
    ],
    "_fetched_at": "2026-07-19T09:00:00+08:00",
    "_stale": False,
    "_error": None,
}


def test_agent_registers_every_infos_capability() -> None:
    names = set(ToolRegistry().get_tool_names())
    assert {
        "list_financial_sources",
        "inspect_financial_source",
        "read_financial_feed",
        "read_financial_article",
        "transform_webpage_to_feed",
        "export_financial_feed",
    } <= names


def test_list_and_inspect_financial_sources_preserve_route_contract() -> None:
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG):
        listed = list_financial_sources(keyword="电报")

    assert listed["success"] is True
    assert listed["catalog_count"] == 2
    assert listed["matched_count"] == 1
    assert listed["query_scope"] == "source_catalog_metadata"
    assert listed["item_count"] == 1
    assert listed["items"][0]["route_path"] == "/cls/telegraph/:category?"

    dynamic = {"subjects": [{"subjectId": "1000", "name": "机器人"}], "_fetched_at": "2026-07-19T09:01:00+08:00"}
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG), patch(
        "src.tools.inspect_financial_source._dynamic_options", return_value=dynamic,
    ):
        inspected = inspect_financial_source("/cls/subject/:id?")

    assert inspected["success"] is True
    assert inspected["route"]["params"] == [{"name": "id"}]
    assert inspected["dynamic_options"]["subjects"][0]["name"] == "机器人"


def test_source_keyword_ai_does_not_match_pinyin_substrings() -> None:
    catalog = {
        **CATALOG,
        "routes": [
            {"route_path": "/eeo/kuaixun", "name": "快讯", "namespace": "eeo", "namespace_name": "经济观察网"},
            {"route_path": "/futunn/main", "name": "要闻", "namespace": "futunn", "namespace_name": "富途牛牛"},
            {"route_path": "/demo/ai", "name": "AI 资讯", "namespace": "demo", "namespace_name": "示例"},
        ],
    }
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=catalog):
        result = list_financial_sources(keyword="AI")

    assert result["catalog_count"] == 3
    assert result["matched_count"] == 1
    assert [item["route_path"] for item in result["items"]] == ["/demo/ai"]


def test_read_financial_feed_forwards_options_and_returns_reader_items() -> None:
    payload = {
        "feed_title": "财联社电报",
        "items": [{
            "id": "1",
            "title": "测试消息",
            "link": "https://example.com/1",
            "summary": "摘要",
            "published": "2026-07-19T09:00:00+08:00",
            "author": "财联社",
            "tags": [],
            "content_html": "<p>正文</p>",
            "attachments": [],
        }],
        "errors": [],
        "_fetched_at": "2026-07-19T09:02:00+08:00",
        "_cached": False,
    }
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG), patch(
        "api.v1.endpoints.rss.get_rss_feeds_by_spec", return_value=payload,
    ) as endpoint:
        result = read_financial_feed(
            "/cls/telegraph/:category?",
            options={"filter_title": "机器人", "sorted": True},
            limit=12,
            force=True,
        )

    request = endpoint.call_args.args[0]
    assert request.options == {"filter_title": "机器人", "sorted": True}
    assert request.limit == 12
    assert request.force is True
    assert result["success"] is True
    assert result["items"][0]["content_html"] == "<p>正文</p>"


def test_read_financial_article_segments_full_text_without_hidden_truncation() -> None:
    body = "<p>" + ("完整正文" * 2000) + "</p>"
    payload = {
        "title": "长消息",
        "link": "https://example.com/long",
        "published": "2026-07-19T09:00:00+08:00",
        "content_html": body,
        "attachments": [{"url": "https://example.com/report.pdf", "mime_type": "application/pdf"}],
    }
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG), patch(
        "api.v1.endpoints.rss.get_rss_feed_item_detail", return_value=payload,
    ) as endpoint:
        first = read_financial_article(
            "/cls/telegraph/:category?",
            "长消息",
            list_content_html="<p>列表正文</p>",
            list_image="https://example.com/cover.png",
            published="2026-07-19T08:00:00+08:00",
            author="财联社",
            tags=["A股"],
            attachments=[{"url": "https://example.com/fallback.pdf", "mime_type": "application/pdf"}],
            max_chars=1000,
        )
        second = read_financial_article(
            "/cls/telegraph/:category?", "长消息", offset=first["next_offset"], max_chars=1000,
        )

    request = endpoint.call_args_list[0].args[0]
    assert request.content_html == "<p>列表正文</p>"
    assert request.image == "https://example.com/cover.png"
    assert request.tags == ["A股"]
    assert request.attachments[0]["url"].endswith("fallback.pdf")
    assert first["has_more"] is True
    assert first["next_offset"] == 1000
    assert second["offset"] == 1000
    assert first["content_length"] > len(first["content_text"])
    assert first["attachments"][0]["mime_type"] == "application/pdf"


def test_transform_and_export_feed_keep_assistant_reader_contract() -> None:
    transformed = {
        "route_path": "/rsshub/transform/html",
        "namespace": "rsshub",
        "feed_title": "自定义网页",
        "items": [{"id": "1", "title": "条目", "link": "https://example.com/a", "summary": "内容"}],
        "errors": [],
        "_fetched_at": "2026-07-19T09:03:00+08:00",
    }
    with patch("api.v1.endpoints.rss.transform_html", return_value=transformed) as endpoint:
        result = transform_webpage_to_feed(
            "https://example.com/list",
            item=".news",
            item_title="h2",
            item_title_attr="title",
            item_link="a",
            item_link_attr="href",
            item_desc=".summary",
            item_desc_attr="data-summary",
            item_pubdate="time",
            item_pubdate_attr="datetime",
        )

    request = endpoint.call_args.args[0]
    assert request.item == ".news"
    assert request.item_title == "h2"
    assert request.item_title_attr == "title"
    assert request.item_link_attr == "href"
    assert request.item_desc_attr == "data-summary"
    assert request.item_pubdate_attr == "datetime"
    assert result["success"] is True
    assert result["params"]["itemLink"] == "a"

    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG):
        exported = export_financial_feed(
            "/cls/telegraph/:category?", options={"filter": "机器人"}, format="json", limit=17,
        )
    assert exported["download_ready"] is True
    assert exported["available_formats"] == ["rss", "atom", "json", "rss3"]
    assert exported["options"]["limit"] == 17
    assert exported["limit"] == 17
    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG), pytest.raises(ValueError, match="不支持的导出格式"):
        export_financial_feed("/cls/telegraph/:category?", format="csv")

    with patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG), pytest.raises(ValueError, match="47 个股市资讯源"):
        export_financial_feed("/not-curated", format="rss")


def test_semantic_news_compaction_keeps_article_reader_coordinates() -> None:
    compact = _compact_tool_result("search_financial_news", {
        "success": True,
        "items": [{
            "id": "entry-1",
            "title": "机器人消息",
            "link": "https://example.com/a",
            "rss_route": "/cls/telegraph",
            "rss_params": {"route_path": "/cls/telegraph", "params": {}, "options": {}},
        }],
    })

    assert compact["items"][0]["id"] == "entry-1"
    assert compact["items"][0]["rss_params"]["route_path"] == "/cls/telegraph"
