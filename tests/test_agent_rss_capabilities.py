from __future__ import annotations

from unittest.mock import patch

import pytest

from api.v1.endpoints.agent.tool_registry_meta import _build_tool_meta
from api.v1.endpoints.agent.tools import _compact_tool_result
from src.tools.registry import ToolRegistry
from src.tools.rss_route_tools import _picker_result
from src.tools.source_operations import RSS_CATALOG_SOURCE_CATALOG, RSS_SOURCE_CATALOG


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
