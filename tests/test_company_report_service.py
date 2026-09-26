from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services import company_report_service as reports


def _company(exchange: str = "szse") -> dict[str, str]:
    market = {"sse": "sh", "szse": "cyb", "bse": "bj"}[exchange]
    return {"code": "300850" if exchange == "szse" else "600519", "name": "新强联", "market": market, "exchange": exchange}


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("新强联：2026年半年度报告", ("semiannual", "2026年半年度", (2026, 2))),
        ("新强联：2025年年度报告", ("annual", "2025年年度", (2025, 4))),
        ("公司2026年第一季度报告", ("q1", "2026年第一季度", (2026, 1))),
        ("公司2025年第三季度报告", ("q3", "2025年第三季度", (2025, 3))),
        ("公司2026年半年度报告摘要", None),
        ("公司2025年年度报告（英文版）", None),
    ],
)
def test_period_parser_selects_full_report_and_orders_fiscal_periods(title, expected) -> None:
    assert reports._report_period(title) == expected


@pytest.mark.parametrize(
    ("exchange", "source_id", "expected_domain"),
    [
        ("sse", "sse_disclosures", "static.sse.com.cn"),
        ("szse", "szse_disclosures", "disc.static.szse.cn"),
    ],
)
def test_rss_exchange_adapter_returns_only_official_full_report_pdfs(
    exchange, source_id, expected_domain
) -> None:
    company = _company(exchange)
    official_url = f"https://{expected_domain}/reports/2026-half.PDF"
    with patch(
        "src.tools.source_operations.read_rss_source",
        return_value={
            "success": True,
            "errors": [],
            "items": [
                {
                    "id": "announcement-1",
                    "title": "新强联：2026年半年度报告",
                    "published": "2026-08-29T00:00:00+08:00",
                    "link": "https://www.szse.cn/disclosure/item" if exchange == "szse" else official_url,
                    "attachments": [{"url": official_url, "mime_type": "application/PDF"}],
                },
                {
                    "id": "summary",
                    "title": "新强联：2026年半年度报告摘要",
                    "link": official_url,
                    "attachments": [],
                },
                {
                    "id": "untrusted",
                    "title": "新强联：2025年年度报告",
                    "link": "https://attacker.example/annual.pdf",
                    "attachments": [],
                },
            ],
        },
    ) as read_feed:
        result = reports._rss_candidates(company)

    assert len(result) == 1
    assert result[0]["report_period"] == "2026年半年度"
    assert result[0]["pdf_url"] == official_url
    assert read_feed.call_args.args[0] == source_id
    assert read_feed.call_args.kwargs["limit"] == 100
    assert read_feed.call_args.kwargs["force"] is True


def test_sse_adapter_uses_company_filter_and_larger_recent_window() -> None:
    with patch(
        "src.tools.source_operations.read_rss_source",
        return_value={"success": True, "errors": [], "items": []},
    ) as read_feed:
        reports._rss_candidates(_company("sse"))
    params = read_feed.call_args.args[1]
    assert params["query"].startswith("productId=600519&")
    assert "pageHelp.pageSize=100" in params["query"]


def test_bse_adapter_uses_official_cninfo_company_and_periodic_report_api() -> None:
    company = _company("bse")
    company["code"] = "920002"
    responses = [
        [{"code": "920002", "orgId": "gfbj0873843"}],
        {
            "announcements": [
                {
                    "announcementId": "report-1",
                    "announcementTitle": "2026年半年度报告",
                    "announcementTime": 1785690000000,
                    "adjunctUrl": "finalpage/2026-08-03/report-1.PDF",
                    "adjunctType": "PDF",
                },
                {
                    "announcementId": "summary-1",
                    "announcementTitle": "2026年半年度报告摘要",
                    "announcementTime": 1785690000000,
                    "adjunctUrl": "finalpage/2026-08-03/summary-1.PDF",
                    "adjunctType": "PDF",
                },
                {
                    "announcementId": "not-pdf",
                    "announcementTitle": "2025年年度报告",
                    "announcementTime": 1785690000000,
                    "adjunctUrl": "finalpage/2026-08-03/report.docx",
                    "adjunctType": "DOCX",
                },
            ]
        },
    ]
    with patch.object(reports, "_cninfo_post", side_effect=responses) as post:
        result = reports._cninfo_candidates(company)

    assert len(result) == 1
    assert result[0]["source_provider"].startswith("巨潮资讯")
    assert result[0]["pdf_url"] == "https://static.cninfo.com.cn/finalpage/2026-08-03/report-1.PDF"
    query = post.call_args_list[1].args[1]
    assert query["column"] == "bj"
    assert query["plate"] == "bj;third"
    assert query["category"] == reports._CNINFO_PERIOD_CATEGORIES


def test_latest_orders_by_fiscal_period_and_quarter_filter_includes_q1_and_q3() -> None:
    company = _company()
    candidates = [
        {"candidate_id": "half", "report_type": "semiannual", "period_rank": (2026, 2), "source_provider": "RSSHub/深交所"},
        {"candidate_id": "annual", "report_type": "annual", "period_rank": (2025, 4), "source_provider": "RSSHub/深交所"},
        {"candidate_id": "q1", "report_type": "q1", "period_rank": (2026, 1), "source_provider": "RSSHub/深交所"},
        {"candidate_id": "q3", "report_type": "q3", "period_rank": (2025, 3), "source_provider": "RSSHub/深交所"},
    ]
    with (
        patch.object(reports, "_resolve_company", return_value=company),
        patch.object(reports, "_report_candidates", return_value=candidates),
    ):
        latest = reports.find_company_financial_reports("新强联")
        quarterly = reports.find_company_financial_reports("新强联", "quarterly")

    assert latest["recommended_candidate_id"] == "half"
    assert [item["candidate_id"] for item in quarterly["candidates"]] == ["q1", "q3"]
    assert "period_rank" not in latest["candidates"][0]


def test_report_candidates_sort_by_reporting_period_before_release_date() -> None:
    company = _company()
    candidates = [
        {"candidate_id": "annual", "period_rank": (2025, 4), "published_at": "2026-04-01"},
        {"candidate_id": "half", "period_rank": (2026, 2), "published_at": "2026-08-01"},
    ]
    with patch.object(reports, "_rss_candidates", return_value=candidates):
        sorted_candidates = reports._report_candidates(company)
    assert [item["candidate_id"] for item in sorted_candidates] == ["half", "annual"]


def test_pdf_downloader_accepts_official_https_pdf_and_rejects_cross_domain_redirect(tmp_path) -> None:
    content = b"%PDF-1.7\n" + b"x" * 32
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=content, request=request)

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    with patch.object(reports.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
        target = tmp_path / "report.part"
        size = reports._stream_official_pdf(
            "https://disc.static.szse.cn/reports/report.PDF", "szse", target
        )
    assert size == len(content)
    assert target.read_bytes() == content
    assert requested == ["https://disc.static.szse.cn/reports/report.PDF"]

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://attacker.example/steal.pdf"}, request=request)

    redirect_transport = httpx.MockTransport(redirect_handler)
    with patch.object(reports.httpx, "Client", side_effect=lambda **kwargs: real_client(transport=redirect_transport, **kwargs)):
        with pytest.raises(reports.CompanyReportError) as error:
            reports._stream_official_pdf(
                "https://disc.static.szse.cn/reports/report.PDF", "szse", tmp_path / "redirect.part"
            )
    assert error.value.code == "untrusted_pdf_redirect"


@pytest.mark.parametrize("duplicate", [False, True])
def test_import_revalidates_candidate_and_persists_source_metadata(tmp_path, monkeypatch, duplicate) -> None:
    company = _company()
    candidate = {
        "candidate_id": "report_" + "a" * 32,
        "company_code": company["code"],
        "company_name": company["name"],
        "exchange": company["exchange"],
        "exchange_label": "深交所",
        "report_type": "semiannual",
        "report_period": "2026年半年度",
        "period_rank": (2026, 2),
        "title": "新强联：2026年半年度报告",
        "announcement_id": "szse-1",
        "published_at": "2026-08-29",
        "pdf_url": "https://disc.static.szse.cn/reports/report.PDF",
        "announcement_url": "https://www.szse.cn/disclosure/item",
        "source_provider": "RSSHub/深交所",
    }
    class FakeRagService:
        def assert_knowledge_base(self, *_args, **_kwargs):
            return {"id": "kb-1", "name": "研究资料"}

        def upload_pdf_path(self, _kb, filename, path, **_kwargs):
            assert path.read_bytes().startswith(b"%PDF-")
            return {"id": "doc-1", "filename": filename, "status": "queued", "duplicate": duplicate}

        def set_document_source(self, document_id, metadata, **_kwargs):
            assert document_id == "doc-1"
            assert metadata["pdf_url"] == candidate["pdf_url"]
            return {
                "id": document_id,
                "status": "unsupported" if duplicate else "queued",
                "source": metadata,
            }

    def fake_download(_url, _exchange, path):
        path.write_bytes(b"%PDF-1.7\n" + b"x" * 32)
        return path.stat().st_size

    monkeypatch.setattr(reports, "_resolve_company", lambda _query: company)
    monkeypatch.setattr(reports, "_report_candidates", lambda _company: [candidate])
    monkeypatch.setattr(reports, "RagKnowledgeBaseService", FakeRagService)
    monkeypatch.setattr(reports, "rag_storage_root", lambda: tmp_path / "rag-root")
    monkeypatch.setattr(reports, "_stream_official_pdf", fake_download)

    result = reports.import_company_financial_report(
        company_query="新强联",
        candidate_id=candidate["candidate_id"],
        report_title=candidate["title"],
        knowledge_base_id="kb-1",
        tenant_id="local",
        owner_id="admin",
    )

    assert result["success"] is True
    assert result["document"]["source"]["announcement_id"] == "szse-1"
    assert result["duplicate"] is duplicate
    assert result["document"]["duplicate"] is duplicate
    if duplicate:
        assert "重新识别" in result["message"]
    else:
        assert "后台开始解析" in result["message"]
    assert list((tmp_path / "rag-root" / ".incoming").glob("*")) == []


def test_import_rejects_forged_or_stale_candidate_before_downloading(monkeypatch) -> None:
    company = _company()
    monkeypatch.setattr(reports, "_resolve_company", lambda _query: company)
    monkeypatch.setattr(reports, "_report_candidates", lambda _company: [])
    with pytest.raises(reports.CompanyReportError) as error:
        reports.import_company_financial_report(
            company_query="新强联",
            candidate_id="report_" + "f" * 32,
            report_title="任意标题",
            knowledge_base_id="kb-1",
            tenant_id="local",
            owner_id="admin",
        )
    assert error.value.code == "report_candidate_stale"
