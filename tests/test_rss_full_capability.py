from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from api.v1.endpoints import rss
from api.v1.endpoints._rss_text import (
    filter_raw_feed_bytes,
    normalize_text_item,
)
from api.v1.endpoints._rss_fetch import _normalize_options
from api.v1.endpoints._rss_cache import _rss_cache_key_generic
from src.agent.orchestrator_v2.intents import (
    FinancialFeedExportIntent,
    FinancialFeedReadIntent,
)
from src.agent.task_workflows import (
    StandardTaskKind,
    TaskResource,
    workflow_for,
)
from src.services.text_document_service import (
    _detected_mime,
    _extract,
    _validate_ooxml_archive,
    TextDocumentError,
    delete_conversation_document_blobs,
    materialize_text_document,
    read_text_resource,
)
from src.storage import DatabaseManager
from src.tools.discover_rss_sources import discover_rss_sources
from src.tools.base import tool_execution_context
from src.tools.read_text_document import read_text_document
from src.tools.registry import ToolRegistry
from src.tools._rss_agent import ensure_rss_route
from src.tools.rss_source_resolver import resolve_rss_source_specs


def _request_with_owner() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/rss/feeds/item/preview",
            "headers": [],
        }
    )
    request.state.tenant_id = "tenant-test"
    request.state.owner_id = "owner-test"
    return request


def test_dynamic_catalog_uses_existing_filtered_finance_scope() -> None:
    catalog = {
        "routes": [
            {
                "route_path": "/new/research/:keyword",
                "namespace": "new",
                "name": "新上线行业研究",
                "description": "行业研报与产业趋势",
                "params": [{"name": "keyword", "required": True}],
                "categories": ["finance"],
                "features": {},
                "readiness": "available",
                "auto_recommended": True,
            },
            {
                "route_path": "/existing/news",
                "namespace": "existing",
                "name": "已筛选财经来源",
                "description": "财经新闻",
                "params": [],
                "categories": ["finance"],
                "features": {},
                "readiness": "available",
                "auto_recommended": True,
            },
        ],
        "count": 2,
        "_fetched_at": "2026-07-31T00:00:00+08:00",
    }
    with patch(
        "api.v1.endpoints._rss_catalog.get_rss_catalog",
        return_value=catalog,
    ) as get_catalog:
        first = discover_rss_sources(limit=1)
        second = discover_rss_sources(offset=1, limit=1)

    assert get_catalog.call_count == 2
    get_catalog.assert_called_with(force=False, scope="finance")
    assert first["catalog_count"] == 2
    assert first["scope"] == "filtered_finance_routes"
    assert first["items"][0]["route_path"] == "/existing/news"
    assert first["next_offset"] == 1
    assert second["offset"] == 1
    assert second["items"][0]["route_path"] == "/new/research/:keyword"
    assert second["next_offset"] is None


def test_source_discovery_repairs_non_taxonomy_category_as_query() -> None:
    catalog = {
        "routes": [
            {
                "route_path": "/szse/notice",
                "namespace": "szse",
                "name": "上市公司公告",
                "description": "A 股公告披露",
                "categories": ["finance"],
                "readiness": "available",
                "auto_recommended": True,
            },
            {
                "route_path": "/cls/telegraph",
                "namespace": "cls",
                "name": "市场快讯",
                "description": "实时财经电报",
                "categories": ["finance"],
                "readiness": "available",
                "auto_recommended": True,
            },
            {
                "route_path": "/demo/entertainment",
                "namespace": "demo",
                "name": "娱乐资讯",
                "description": "无关内容",
                "categories": ["finance"],
                "readiness": "available",
                "auto_recommended": False,
            },
        ],
        "count": 3,
        "_fetched_at": "2026-07-31T00:00:00+08:00",
    }
    with patch(
        "api.v1.endpoints._rss_catalog.get_rss_catalog",
        return_value=catalog,
    ):
        result = discover_rss_sources(
            query="A股公告",
            category="A股公告",
        )

    assert [item["route_path"] for item in result["items"]] == [
        "/szse/notice"
    ]
    assert result["filters"]["category"] == ""
    assert result["filters"]["requested_category"] == "A股公告"
    assert "已忽略不是 RSSHub 分类" in result["warnings"][0]


def test_source_discovery_marks_genuine_zero_match_as_incomplete() -> None:
    catalog = {
        "routes": [
            {
                "route_path": "/szse/notice",
                "namespace": "szse",
                "name": "上市公司公告",
                "description": "A 股公告披露",
                "categories": ["finance"],
                "readiness": "available",
                "auto_recommended": True,
            }
        ],
        "count": 1,
        "_fetched_at": "2026-07-31T00:00:00+08:00",
    }
    with patch(
        "api.v1.endpoints._rss_catalog.get_rss_catalog",
        return_value=catalog,
    ):
        result = discover_rss_sources(query="完全无关的来源用途")

    assert result["success"] is False
    assert result["items"] == []
    assert result["coverage"]["failures"] == result["errors"]
    assert "没有匹配项" in result["errors"][0]


def test_settings_preview_materializes_documents_in_hidden_session(
    tmp_path: Path,
) -> None:
    database = DatabaseManager(
        db_url=f"sqlite:///{tmp_path / 'preview.db'}"
    )
    body = rss.FeedItemPreviewRequest(
        route_path="/szse/disclosure/listed/notice/:query?",
        params={},
        options={},
        namespace="szse",
        item_id="szse-item",
        title="上市公司公告",
        link="https://www.szse.cn/disclosure/item",
        attachments=[
            {
                "url": "https://disc.static.szse.cn/report.pdf",
                "mime_type": "application/pdf",
            }
        ],
        preview_session_id="preview_session_test",
    )
    detail = normalize_text_item(
        {
            "id": "szse-item",
            "title": "上市公司公告",
            "link": "https://www.szse.cn/disclosure/item",
            "content_html": "<p>公告正文</p>",
            "attachments": body.attachments,
        }
    )
    resource = {
        "resource_id": "textdoc_preview",
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 100,
        "content_hash": "a" * 64,
        "preview_url": (
            "/api/v1/agent/resources/textdoc_preview/content"
            "?disposition=inline"
        ),
        "download_url": (
            "/api/v1/agent/resources/textdoc_preview/content"
            "?disposition=attachment"
        ),
        "extraction_status": "extracted",
        "text_length": 12,
        "chunk_count": 1,
    }
    with (
        patch.object(
            rss,
            "get_rss_feed_item_detail",
            return_value=detail,
        ),
        patch(
            "src.services.text_document_service."
            "materialize_text_documents",
            return_value=([resource], []),
        ) as materialize,
    ):
        result = rss.preview_rss_feed_item(
            body,
            _request_with_owner(),
            database,
        )

    assert result["resources"] == [resource]
    assert result["document_errors"] == []
    assert result["item_ref"]["item_id"] == "szse-item"
    assert len(result["item_ref"]["content_hash"]) == 64
    materialize.assert_called_once()
    call = materialize.call_args.kwargs
    assert call["attachments"][0]["url"].endswith("report.pdf")
    assert call["source_item_ref"] == result["item_ref"]
    preview_conversation = database.get_chat_conversation(
        call["conversation_id"],
        tenant_id="tenant-test",
        owner_id="owner-test",
    )
    assert preview_conversation is not None
    assert preview_conversation.title_source == "system"
    visible, total = database.list_chat_conversations(
        tenant_id="tenant-test",
        owner_id="owner-test",
    )
    assert visible == []
    assert total == 0

    cleanup = rss.delete_rss_preview_session(
        "preview_session_test",
        _request_with_owner(),
        database,
    )
    assert cleanup == {"success": True, "deleted": True}


def test_route_validation_rejects_sources_outside_filtered_catalog() -> None:
    catalog = {
        "routes": [{"route_path": "/allowed/feed"}],
        "count": 1,
    }
    with patch(
        "api.v1.endpoints._rss_catalog.get_rss_catalog",
        return_value=catalog,
    ) as get_catalog:
        assert ensure_rss_route("/allowed/feed") == "/allowed/feed"
        with pytest.raises(ValueError, match="已筛选来源目录"):
            ensure_rss_route("/outside/feed")

    assert get_catalog.call_count == 2
    get_catalog.assert_called_with(force=False, scope="finance")


def test_semantic_resolver_uses_new_route_without_code_release() -> None:
    specs = resolve_rss_source_specs(
        [
            {
                "route_path": "/future/source/:keyword",
                "name": "未来新增产业研究",
                "description": "行业研报与产业趋势",
                "params": [{"name": "keyword", "required": True}],
                "readiness": "available",
                "auto_recommended": True,
            }
        ],
        information_need="industry",
        query="半导体景气",
        subjects=["半导体"],
    )

    assert specs == [
        (
            "/future/source/:keyword",
            {"keyword": "半导体"},
            "未来新增产业研究",
        )
    ]


def test_text_normalization_removes_all_media_but_keeps_alt_text() -> None:
    item = normalize_text_item(
        {
            "id": "one",
            "title": "测试",
            "link": "https://example.test/article",
            "content_html": (
                '<p>正文</p><img src="https://example.test/a.png" '
                'alt="图表说明"><video src="https://example.test/a.mp4"></video>'
                '<a href="https://example.test/embedded.docx">附件正文</a>'
            ),
            "attachments": [
                {
                    "url": "https://example.test/report.pdf",
                    "mime_type": "application/pdf",
                    "title": "报告.pdf",
                },
                {
                    "url": "https://example.test/audio.mp3",
                    "mime_type": "audio/mpeg",
                },
                {
                    "url": "https://example.test/image.png",
                    "mime_type": "image/png",
                },
            ],
        }
    )

    assert "正文" in item["content_html"]
    assert "图表说明" in item["content_html"]
    assert "src=" not in item["content_html"]
    assert [value["url"] for value in item["attachments"]] == [
        "https://example.test/report.pdf",
        "https://example.test/embedded.docx",
    ]
    assert item["_discarded_non_text"] == 2


def test_text_normalization_does_not_rewrite_upstream_text_by_phrase() -> None:
    item = normalize_text_item(
        {
            "id": "notice",
            "title": "上市公司公告",
            "link": "https://example.test/notice",
            "content_html": (
                "<div><p>位置：信息披露/上市公司信息/公告正文</p></div>"
                "<div><p>请先安装浏览器PDF插件在线浏览公告或直接下载公告。</p></div>"
            ),
            "attachments": [
                {
                    "url": "https://example.test/notice.pdf",
                    "mime_type": "application/pdf",
                    "title": "公告.pdf",
                }
            ],
        }
    )

    assert "位置：信息披露" in item["content_html"]
    assert "PDF插件" in item["content_html"]
    assert "直接下载公告" in item["summary"]
    assert len(item["attachments"]) == 1


def test_text_normalization_keeps_real_pdf_article_text() -> None:
    item = normalize_text_item(
        {
            "id": "report",
            "title": "年度报告说明",
            "link": "https://example.test/report",
            "content_html": "<p>公司提供PDF格式年度报告下载，报告共120页。</p>",
            "attachments": [
                {
                    "url": "https://example.test/report.pdf",
                    "mime_type": "application/pdf",
                }
            ],
        }
    )

    assert "PDF格式年度报告下载" in item["content_html"]


def test_json_export_removes_media_and_keeps_text_documents() -> None:
    raw = json.dumps(
        {
            "items": [
                {
                    "title": "报告",
                    "image": "https://example.test/image.png",
                    "attachments": [
                        {
                            "url": "https://example.test/report.pdf",
                            "mime_type": "application/pdf",
                        },
                        {
                            "url": "https://example.test/movie.mp4",
                            "mime_type": "video/mp4",
                        },
                    ],
                }
            ]
        }
    ).encode()

    cleaned = json.loads(filter_raw_feed_bytes(raw, "json"))

    assert "image" not in cleaned["items"][0]
    assert len(cleaned["items"][0]["attachments"]) == 1
    assert cleaned["items"][0]["attachments"][0]["url"].endswith(
        "report.pdf"
    )


def test_blank_pdf_reports_no_text_layer(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 200 200] /Contents 4 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for value in objects:
        offsets.append(len(payload))
        payload.extend(value)
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(payload))

    status, method, chunks, error = _extract(
        path.read_bytes(),
        mime="application/pdf",
        filename="blank.pdf",
        source_url="https://example.test/blank.pdf",
    )

    assert status == "empty_text_layer"
    assert method == "pdfminer"
    assert chunks == []
    assert error is None


def test_file_header_overrides_untrusted_media_mime() -> None:
    assert _detected_mime(
        b"\x89PNG\r\n\x1a\n" + b"not really text",
        "text/plain",
    ) == "image/unknown"
    assert _detected_mime(
        b"%PDF-1.7\n",
        "application/octet-stream",
    ) == "application/pdf"


def test_modern_office_files_extract_text_without_visual_parsing() -> None:
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="urn:test"><w:body><w:p>'
                "<w:r><w:t>Word 原始正文</w:t></w:r>"
                "</w:p></w:body></w:document>"
            ),
        )
    status, method, chunks, error = _extract(
        docx.getvalue(),
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename="report.docx",
        source_url="https://example.test/report.docx",
    )
    assert (status, method, error) == (
        "extracted",
        "ooxml-docx",
        None,
    )
    assert "Word 原始正文" in chunks[0]["text"]

    pptx = io.BytesIO()
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            (
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld>'
                "<a:t>第一张幻灯片正文</a:t></p:cSld></p:sld>"
            ),
        )
    status, method, chunks, error = _extract(
        pptx.getvalue(),
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        filename="slides.pptx",
        source_url="https://example.test/slides.pptx",
    )
    assert (status, method, error) == (
        "extracted",
        "ooxml-pptx",
        None,
    )
    assert chunks[0]["page"] == 1
    assert "第一张幻灯片正文" in chunks[0]["text"]

    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "财务数据"
    worksheet.append(["公司", "收入"])
    worksheet.append(["示例公司", 100])
    xlsx = io.BytesIO()
    workbook.save(xlsx)
    workbook.close()
    status, method, chunks, error = _extract(
        xlsx.getvalue(),
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        filename="table.xlsx",
        source_url="https://example.test/table.xlsx",
    )
    assert (status, method, error) == (
        "extracted",
        "openpyxl",
        None,
    )
    assert chunks[0]["section"] == "工作表：财务数据"
    assert "示例公司\t100" in chunks[0]["text"]


def test_original_bytes_hash_and_read_text_share_one_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    DatabaseManager.reset_instance()
    database = DatabaseManager(
        db_url=f"sqlite:///{tmp_path / 'resource.db'}"
    )
    database.create_chat_conversation("conversation")
    body = "公司,收入\n示例公司,100\n".encode()
    monkeypatch.setenv(
        "AGENT_TEXT_RESOURCE_DIR",
        str(tmp_path / "resources"),
    )
    with patch(
        "src.services.text_document_service._download",
        return_value=(
            body,
            "text/csv",
            "report.csv",
            "https://example.test/report.csv",
        ),
    ):
        resource = materialize_text_document(
            db=database,
            conversation_id="conversation",
            run_id="run",
            url="https://example.test/report.csv",
        )

    stored, chunks = read_text_resource(
        db=database,
        resource_id=resource["resource_id"],
        conversation_id="conversation",
    )
    record = database.get_text_document(resource["resource_id"])
    assert record is not None
    assert Path(record["blob_path"]).read_bytes() == body
    assert resource["content_hash"] == sha256(body).hexdigest()
    assert stored["content_hash"] == resource["content_hash"]
    assert "示例公司" in "\n".join(chunk["text"] for chunk in chunks)
    with tool_execution_context(
        conversation_id="conversation",
        run_id="run",
    ):
        complete = read_text_document(
            resource["resource_id"],
            reading_mode="complete",
        )
    assert complete["coverage_digest"]["coverage_complete"] is True
    assert len(complete["evidence_collection"]["records"]) == len(chunks)
    assert (
        complete["evidence_collection"]["records"][0]["content_hash"]
        == chunks[0]["content_hash"]
    )
    assert (
        database.get_text_document(
            resource["resource_id"],
            tenant_id="another-tenant",
            owner_id="admin",
        )
        is None
    )
    with pytest.raises(Exception, match="不属于当前会话"):
        read_text_resource(
            db=database,
            resource_id=resource["resource_id"],
            conversation_id="another",
        )
    blob_path = Path(str(record["blob_path"]))
    delete_conversation_document_blobs(database, "conversation")
    assert database.get_text_document(resource["resource_id"]) is None
    assert not blob_path.exists()
    DatabaseManager.reset_instance()


def test_office_archive_expansion_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * 256)

    monkeypatch.setattr(
        "src.services.text_document_service._MAX_ARCHIVE_UNCOMPRESSED_BYTES",
        128,
    )
    with pytest.raises(TextDocumentError, match="解压后超过安全上限"):
        _validate_ooxml_archive(payload.getvalue())


def test_planner_exposes_only_generic_tools_and_export_schema_matches() -> None:
    registry = ToolRegistry()
    assert workflow_for(
        StandardTaskKind.FINANCIAL_SOURCE_DISCOVERY
    ).tool_whitelist == {
        "discover_rss_sources",
        "inspect_rss_source",
    }
    assert workflow_for(
        StandardTaskKind.FINANCIAL_ARTICLE_READ
    ).output_resources >= {
        TaskResource.TEXT_DOCUMENT_COLLECTION,
        TaskResource.EVIDENCE_COLLECTION,
    }
    export_schema = registry.get_tool("export_rss_feed").parameters
    assert export_schema["properties"]["format"]["enum"] == [
        "rss",
        "atom",
        "json",
        "rss3",
    ]
    assert "force" not in export_schema["properties"]
    assert export_schema["properties"]["limit"]["maximum"] == 100
    planned_read = FinancialFeedReadIntent.model_validate(
        {
            "route_path": "/new/source",
            "options": {
                "filter_title": "机器人",
                "filterout": "广告",
                "filter_time": 86_400,
                "sorted": True,
                "mode": "fulltext",
                "opencc": "t2s",
                "brief": 300,
                "format": "json",
            },
        }
    )
    assert planned_read.options is not None
    assert planned_read.options.mode == "fulltext"
    with pytest.raises(ValidationError):
        FinancialFeedExportIntent.model_validate(
            {
                "route_path": "/new/source",
                "format": "csv",
                "limit": 101,
            }
        )


def test_privileged_rss_options_are_server_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "RSSHUB_AGENT_PRIVILEGED_OPTIONS",
        raising=False,
    )
    with pytest.raises(PermissionError):
        _normalize_options({"scihub": "server"})
    monkeypatch.setenv(
        "RSSHUB_AGENT_PRIVILEGED_OPTIONS",
        json.dumps({"scihub": "managed-secret"}),
    )
    assert _normalize_options({"scihub": "server"}) == {
        "scihub": "managed-secret"
    }
    first = _rss_cache_key_generic(
        "/example",
        options={"scihub": "server"},
    )
    monkeypatch.setenv(
        "RSSHUB_AGENT_PRIVILEGED_OPTIONS",
        json.dumps({"scihub": "rotated-secret"}),
    )
    second = _rss_cache_key_generic(
        "/example",
        options={"scihub": "server"},
    )
    assert first != second
