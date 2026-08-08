from __future__ import annotations

from unittest.mock import patch

import pytest

from api.v1.endpoints.agent.tool_registry_meta import _build_tool_meta
from api.v1.endpoints.agent.tools import _compact_tool_result
from src.tools.export_financial_feed import export_financial_feed
from src.tools.inspect_financial_source import inspect_financial_source
from src.tools.list_financial_sources import list_financial_sources
from src.tools.read_financial_article import read_financial_article
from src.tools.read_financial_feed import read_financial_feed
from src.tools.registry import ToolRegistry
from src.tools.rss_route_tools import _picker_result
from src.tools.source_operations import RSS_CATALOG_SOURCE_CATALOG, RSS_SOURCE_CATALOG
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


def test_agent_registers_generic_rss_operations_with_a_complete_source_directory() -> None:
    registry = ToolRegistry()
    names = set(registry.get_tool_names())
    assert len(RSS_SOURCE_CATALOG) == 47
    assert len(RSS_CATALOG_SOURCE_CATALOG) == 5
    assert {
        "read_rss_source",
        "list_rss_source_catalog",
        "read_rss_item",
        "read_text_document",
    } <= names
    assert {
        "read_rss_cls_telegraph",
        "read_rss_wallstreetcn_live",
        "list_rss_cls_subjects",
    }.isdisjoint(names)
    assert {
        "discover_rss_sources",
        "inspect_rss_source",
        "read_rss_feed",
        "list_financial_sources",
        "inspect_financial_source",
        "read_financial_feed",
        "read_financial_article",
        "export_financial_feed",
        "export_rss_feed",
        "transform_webpage_to_feed",
    }.isdisjoint(names)
    source_ids = {item["id"] for item in RSS_SOURCE_CATALOG}
    assert {"cls_telegraph", "wallstreetcn_live", "sse_disclosures"}.issubset(source_ids)


def test_generic_rss_operation_binds_one_model_selected_source() -> None:
    payload = {
        "feed_title": "财联社电报",
        "items": [],
        "errors": [],
        "_fetched_at": "2026-07-19T09:02:00+08:00",
        "_cached": False,
    }
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog") as catalog,
        patch(
            "api.v1.endpoints.rss.get_rss_feeds_by_spec",
            return_value=payload,
        ) as endpoint,
    ):
        result = ToolRegistry().execute(
            "read_rss_source",
            {
                "source_id": "cls_telegraph",
                "source_params": {"category": "global"},
                "limit": 12,
            },
        )

    request = endpoint.call_args.args[0]
    assert request.route_path == "/cls/telegraph/:category?"
    assert request.namespace == "cls"
    assert request.params == {"category": "global"}
    assert request.limit == 12
    assert result["source"]["provider"] == "财联社"
    catalog.assert_not_called()


def test_atomic_rss_source_uses_article_publish_time_not_transport_time() -> None:
    payload = {
        "feed_title": "财联社电报",
        "items": [
            {
                "id": "one",
                "title": "机器人产业链消息",
                "link": "https://example.com/one",
                "published": "2026-07-19T09:00:00+08:00",
            }
        ],
        "errors": [],
        "_fetched_at": "2026-08-08T20:00:00+08:00",
    }
    with patch(
        "api.v1.endpoints.rss.get_rss_feeds_by_spec",
        return_value=payload,
    ):
        result = ToolRegistry().execute(
            "read_rss_source",
            {"source_id": "cls_telegraph", "limit": 12},
        )

    assert result["data_time"] == "2026-07-19T09:00:00+08:00"
    assert result["data_time_provenance"] == "source"
    assert result["freshness_unknown"] is False


def test_rss_catalog_does_not_promote_transport_fetch_time_to_data_time() -> None:
    result = _picker_result(
        {
            "subjects": [{"subjectId": "1000", "name": "机器人"}],
            "_fetched_at": "2026-08-08T20:00:00+08:00",
            "_stale": False,
        },
        provider="财联社",
        catalog_name="话题目录",
    )

    assert result["data_time"] is None
    assert result["data_time_provenance"] == "unavailable"
    assert result["freshness_unknown"] is True
    assert "_fetched_at" in result["data_time_note"]


def test_atomic_rss_source_preserves_optional_path_positions_with_route_defaults() -> None:
    payload = {
        "feed_title": "华尔街见闻快讯",
        "items": [],
        "errors": [],
        "_fetched_at": "2026-07-19T09:02:00+08:00",
        "_cached": False,
    }
    with patch(
        "api.v1.endpoints.rss.get_rss_feeds_by_spec",
        return_value=payload,
    ) as endpoint:
        ToolRegistry().execute(
            "read_rss_source",
            {
                "source_id": "wallstreetcn_live",
                "source_params": {"score": "2"},
                "limit": 5,
            },
        )

    request = endpoint.call_args.args[0]
    assert request.route_path == "/wallstreetcn/live/:category?/:score?"
    assert request.params == {"category": "global", "score": "2"}


def test_read_rss_item_returns_only_its_body_and_attachment_metadata() -> None:
    payload = {
        "title": "带附件的消息",
        "link": "https://example.com/article",
        "published": "2026-07-19T09:00:00+08:00",
        "content_html": "<p>条目正文</p>",
        "attachments": [
            {"url": "https://example.com/report.pdf", "mime_type": "application/pdf"}
        ],
    }
    with patch(
        "api.v1.endpoints.rss.get_rss_feed_item_detail",
        return_value=payload,
    ):
        result = ToolRegistry().execute(
            "read_rss_item",
            {
                "item_ref": {
                    "route_path": "/cls/telegraph/:category?",
                    "namespace": "cls",
                    "content_hash": "a" * 64,
                    "title": "带附件的消息",
                    "link": "https://example.com/article",
                }
            },
        )

    assert result["success"] is True
    assert result["content_text"] == "条目正文"
    assert result["attachments"][0]["url"].endswith("report.pdf")
    assert "resources" not in result
    assert result["coverage"]["text_documents_extracted"] == 0
    assert result["evidence_collection"]["coverage_complete"] is False


def test_read_rss_item_marks_same_item_list_body_fallback_as_partial() -> None:
    payload = {
        "title": "列表正文回退",
        "link": "https://example.com/article",
        "published": "2026-07-19T09:00:00+08:00",
        "content_html": "<p>列表项已有正文</p>",
        "attachments": [],
        "_content_origin": "list_item_fallback",
    }
    with patch(
        "api.v1.endpoints.rss.get_rss_feed_item_detail",
        return_value=payload,
    ):
        result = ToolRegistry().execute(
            "read_rss_item",
            {
                "item_ref": {
                    "route_path": "/cls/telegraph/:category?",
                    "namespace": "cls",
                    "content_hash": "b" * 64,
                    "title": "列表正文回退",
                    "link": "https://example.com/article",
                }
            },
        )

    assert result["success"] is True
    assert result["partial"] is True
    assert result["fallback_used"] is True
    assert result["content_origin"] == "list_item_fallback"
    assert result["evidence_collection"]["coverage_complete"] is False
    assert any("完整文章覆盖" in item for item in result["warnings"])


def test_read_rss_item_rejects_unregistered_route_and_untrusted_list_body() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="已注册的 read_rss"):
        registry.execute(
            "read_rss_item",
            {
                "item_ref": {
                    "route_path": "/unregistered/source",
                    "content_hash": "c" * 64,
                }
            },
        )

    with pytest.raises(ValueError, match="内容哈希与 item_ref 不一致"):
        registry.execute(
            "read_rss_item",
            {
                "item_ref": {
                    "route_path": "/cls/telegraph/:category?",
                    "namespace": "cls",
                    "content_hash": "d" * 64,
                },
                "item": {
                    "title": "模型不能伪造的列表正文",
                    "content_html": "<p>不可信内容</p>",
                },
            },
        )


def test_registry_metadata_exposes_generic_rss_schema_and_source_directory() -> None:
    spec = ToolRegistry().get_tool("read_rss_source")
    assert spec is not None

    meta = _build_tool_meta(spec)

    assert meta.retrieval_description == meta.description
    assert {parameter.name: parameter.type for parameter in meta.parameters} == {
        "source_id": "string",
        "source_params": "object",
        "options": "object",
        "limit": "integer",
        "force": "boolean",
    }
    assert len(meta.source_catalog) == 47
    assert any(item["id"] == "cls_telegraph" for item in meta.source_catalog)


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
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG),
        patch(
            "src.tools.inspect_financial_source._dynamic_options",
            return_value=dynamic,
        ),
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
        "items": [
            {
                "id": "1",
                "title": "测试消息",
                "link": "https://example.com/1",
                "summary": "摘要",
                "published": "2026-07-19T09:00:00+08:00",
                "author": "财联社",
                "tags": [],
                "content_html": "<p>正文</p>",
                "attachments": [],
            }
        ],
        "errors": [],
        "_fetched_at": "2026-07-19T09:02:00+08:00",
        "_cached": False,
    }
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG),
        patch(
            "api.v1.endpoints.rss.get_rss_feeds_by_spec",
            return_value=payload,
        ) as endpoint,
    ):
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
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG),
        patch(
            "api.v1.endpoints.rss.get_rss_feed_item_detail",
            return_value=payload,
        ) as endpoint,
    ):
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
            "/cls/telegraph/:category?",
            "长消息",
            offset=first["next_offset"],
            max_chars=1000,
        )

    request = endpoint.call_args_list[0].args[0]
    assert request.content_html == "<p>列表正文</p>"
    assert request.image == ""
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
            "/cls/telegraph/:category?",
            options={"filter": "机器人"},
            format="json",
            limit=17,
        )
    assert exported["download_ready"] is True
    assert exported["available_formats"] == ["rss", "atom", "json", "rss3"]
    assert exported["options"]["limit"] == 17
    assert exported["limit"] == 17
    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG),
        pytest.raises(ValueError, match="不支持的导出格式"),
    ):
        export_financial_feed("/cls/telegraph/:category?", format="csv")

    with (
        patch("api.v1.endpoints._rss_catalog.get_rss_catalog", return_value=CATALOG),
        pytest.raises(ValueError, match="已筛选来源目录"),
    ):
        export_financial_feed("/not-curated", format="rss")


def test_generic_rss_compaction_keeps_article_reader_coordinates() -> None:
    compact = _compact_tool_result(
        "read_rss_source",
        {
            "success": True,
            "items": [
                {
                    "id": "entry-1",
                    "title": "机器人消息",
                    "link": "https://example.com/a",
                    "rss_route": "/cls/telegraph",
                    "rss_params": {"route_path": "/cls/telegraph", "params": {}, "options": {}},
                }
            ],
        },
    )

    assert compact["items"][0]["id"] == "entry-1"
    assert compact["items"][0]["rss_params"]["route_path"] == "/cls/telegraph"
