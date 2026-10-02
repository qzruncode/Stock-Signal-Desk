"""Official exchange filing discovery and original-PDF import for RAG."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from src.rag.pdf_processing import MAX_UPLOAD_BYTES
from src.services.rag_knowledge_base_service import (
    RagKnowledgeBaseService,
    rag_storage_root,
)
from src.tools.base import report_tool_progress


_CNINFO_ROOT = "https://www.cninfo.com.cn"
_CNINFO_STATIC_ROOT = "https://static.cninfo.com.cn/"
_CNINFO_PERIOD_CATEGORIES = (
    "category_ndbg_szsh;category_bndbg_szsh;"
    "category_yjdbg_szsh;category_sjdbg_szsh;"
)
_CNINFO_HOSTS = frozenset({"static.cninfo.com.cn"})
_EXCHANGE_HOSTS = {
    "sse": frozenset({"static.sse.com.cn"}),
    "szse": frozenset({"disc.static.szse.cn"}),
    "bse": _CNINFO_HOSTS,
}
_EXCHANGE_LABELS = {"sse": "上交所", "szse": "深交所", "bse": "北交所"}
_MARKET_EXCHANGES = {
    "sh": "sse",
    "kcb": "sse",
    "sz": "szse",
    "cyb": "szse",
    "bj": "bse",
}
_REPORT_TYPE_FILTERS = {
    "annual": {"annual"},
    "semiannual": {"semiannual"},
    "quarterly": {"q1", "q3"},
}
_PERIOD_PATTERNS = (
    ("semiannual", 2, re.compile(r"半年度报告|半年报|中期报告"), "半年度"),
    ("annual", 4, re.compile(r"年度报告|年报"), "年度"),
    ("q3", 3, re.compile(r"第三季度报告|三季度报告|三季报"), "第三季度"),
    ("q1", 1, re.compile(r"第一季度报告|一季度报告|一季报"), "第一季度"),
)
_REPORT_YEAR = re.compile(r"(?<!\d)(20\d{2})\s*年?")
_SUMMARY_MARKERS = ("摘要", "英文版", "英文报告", "english version")
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 3
_DOWNLOAD_TIMEOUT = httpx.Timeout(45.0, connect=10.0, read=30.0, write=15.0)


class CompanyReportError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _normalized_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _resolve_company(company: str) -> dict[str, str]:
    query = " ".join(str(company or "").split())
    if not query:
        raise CompanyReportError("请提供上市公司名称或证券代码。", code="company_required")
    try:
        from src.tools.search_stocks import search_stocks

        result = search_stocks(query=query, limit=20)
    except Exception as exc:
        raise CompanyReportError(
            "证券主数据服务暂不可用，无法可靠确认公司身份。",
            code="security_lookup_unavailable",
            retryable=True,
        ) from exc

    rows = [row for row in result.get("items") or [] if isinstance(row, dict)]
    query_code = query if re.fullmatch(r"\d{6}", query) else ""
    if query_code:
        matches = [row for row in rows if str(row.get("code") or "") == query_code]
    else:
        exact_name = _normalized_name(query)
        matches = [row for row in rows if _normalized_name(row.get("name")) == exact_name]

    # Never turn a fuzzy market-search result into a filing for another issuer.
    if len(matches) != 1:
        suggestions = [
            {
                "code": str(row.get("code") or ""),
                "name": str(row.get("name") or ""),
                "market": str(row.get("market") or ""),
            }
            for row in rows[:8]
        ]
        choices = "；候选：" + "、".join(
            f"{item['name']}（{item['code']}）" for item in suggestions if item["name"]
        ) if suggestions else ""
        raise CompanyReportError(
            "无法唯一确认上市公司；请使用证券代码或精确公司名称" + choices + "。",
            code="company_ambiguous" if rows else "company_not_found",
        )

    row = matches[0]
    code = str(row.get("code") or "").strip()
    name = str(row.get("name") or "").strip()
    market = str(row.get("market") or "").strip().lower()
    exchange = _MARKET_EXCHANGES.get(market)
    if not re.fullmatch(r"\d{6}", code) or not name or exchange is None:
        raise CompanyReportError(
            "该证券不在当前支持的沪、深、北交所 A 股范围内。",
            code="unsupported_exchange",
        )
    return {"code": code, "name": name, "market": market, "exchange": exchange}


def _report_period(title: str) -> tuple[str, str, tuple[int, int]] | None:
    normalized = str(title or "")
    lower = normalized.casefold()
    if any(marker in lower for marker in _SUMMARY_MARKERS):
        return None
    year_match = _REPORT_YEAR.search(normalized)
    if year_match is None:
        return None
    year = int(year_match.group(1))
    for kind, quarter_rank, pattern, label in _PERIOD_PATTERNS:
        if pattern.search(normalized):
            return kind, f"{year}年{label}", (year, quarter_rank)
    return None


def _published_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _allowed_pdf_url(url: Any, exchange: str) -> str | None:
    candidate = str(url or "").strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _EXCHANGE_HOSTS.get(exchange, frozenset())
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not parsed.path.lower().endswith(".pdf")
    ):
        return None
    return candidate


def _candidate_id(
    *, exchange: str, company_code: str, announcement_id: str, title: str, pdf_url: str
) -> str:
    payload = "\0".join((exchange, company_code, announcement_id, title, pdf_url))
    return "report_" + sha256(payload.encode("utf-8")).hexdigest()[:32]


def _candidate(
    *,
    company: dict[str, str],
    title: str,
    announcement_id: str,
    published_at: str | None,
    pdf_url: str,
    announcement_url: str,
    provider: str,
) -> dict[str, Any] | None:
    parsed_period = _report_period(title)
    if parsed_period is None:
        return None
    report_type, period_label, period_rank = parsed_period
    return {
        "candidate_id": _candidate_id(
            exchange=company["exchange"],
            company_code=company["code"],
            announcement_id=announcement_id,
            title=title,
            pdf_url=pdf_url,
        ),
        "company_code": company["code"],
        "company_name": company["name"],
        "exchange": company["exchange"],
        "exchange_label": _EXCHANGE_LABELS[company["exchange"]],
        "report_type": report_type,
        "report_period": period_label,
        "period_rank": period_rank,
        "title": title,
        "announcement_id": announcement_id,
        "published_at": published_at,
        "pdf_url": pdf_url,
        "announcement_url": announcement_url,
        "source_provider": provider,
    }


def _rss_candidates(company: dict[str, str]) -> list[dict[str, Any]]:
    from src.tools.source_operations import read_rss_source

    code = company["code"]
    exchange = company["exchange"]
    if exchange == "sse":
        query = (
            f"productId={code}&pageHelp.pageSize=100&pageHelp.pageNo=1&"
            "pageHelp.beginPage=1&pageHelp.endPage=4"
        )
        source_id = "sse_disclosures"
        route_params = {"query": query}
        source_page = f"https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId={code}"
    else:
        source_id = "szse_disclosures"
        route_params = {"query": f"stock={code}"}
        source_page = f"https://www.szse.cn/disclosure/listed/notice/index.html?stock={code}"

    try:
        feed = read_rss_source(
            source_id,
            route_params,
            options={"format": "json"},
            limit=100,
            force=True,
        )
    except Exception as exc:
        raise CompanyReportError(
            f"{_EXCHANGE_LABELS[exchange]}公告源暂不可用，未改用二手数据源。",
            code=f"{exchange}_source_unavailable",
            retryable=True,
        ) from exc
    if feed.get("success") is not True or feed.get("errors"):
        raise CompanyReportError(
            f"{_EXCHANGE_LABELS[exchange]}公告源未能完整返回结果，未改用其他来源。",
            code=f"{exchange}_source_error",
            retryable=True,
        )

    results: list[dict[str, Any]] = []
    for item in feed.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        attachment_urls = [
            str(attachment.get("url") or "")
            for attachment in item.get("attachments") or []
            if isinstance(attachment, dict)
        ]
        urls: Iterable[str] = [*attachment_urls, str(item.get("link") or ""), str(item.get("url") or "")]
        pdf_url = next(
            (safe for raw in urls if (safe := _allowed_pdf_url(raw, exchange))),
            None,
        )
        if not pdf_url:
            continue
        item_id = str(item.get("id") or item.get("guid") or pdf_url)
        candidate = _candidate(
            company=company,
            title=title,
            announcement_id=item_id,
            published_at=_published_date(item.get("published") or item.get("updated")),
            pdf_url=pdf_url,
            announcement_url=str(item.get("link") or source_page),
            provider=f"RSSHub/{_EXCHANGE_LABELS[exchange]}",
        )
        if candidate is not None:
            results.append(candidate)
    return results


def _cninfo_post(path: str, data: dict[str, Any]) -> Any:
    try:
        with httpx.Client(
            timeout=httpx.Timeout(25.0, connect=8.0),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": _CNINFO_ROOT,
                "Referer": f"{_CNINFO_ROOT}/new/disclosure/stock",
                "User-Agent": "Mozilla/5.0 (compatible; DailyStockAnalysis/1.0)",
            },
        ) as client:
            response = client.post(f"{_CNINFO_ROOT}{path}", data=data)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CompanyReportError(
            "巨潮资讯官方公告接口暂不可用。",
            code="cninfo_source_unavailable",
            retryable=True,
        ) from exc


def _cninfo_candidates(company: dict[str, str]) -> list[dict[str, Any]]:
    code = company["code"]
    lookup = _cninfo_post(
        "/new/information/topSearch/query",
        {"keyWord": code, "maxNum": 10},
    )
    matches = [
        item
        for item in lookup if isinstance(item, dict)
        and str(item.get("code") or "") == code
    ] if isinstance(lookup, list) else []
    if len(matches) != 1:
        raise CompanyReportError(
            "巨潮资讯无法唯一确认该北交所公司身份。",
            code="cninfo_company_unresolved",
        )
    org_id = str(matches[0].get("orgId") or "").strip()
    if not org_id:
        raise CompanyReportError(
            "巨潮资讯公司档案缺少公告查询标识。",
            code="cninfo_company_unresolved",
        )

    payload = _cninfo_post(
        "/new/hisAnnouncement/query",
        {
            "stock": f"{code},{org_id}",
            "tabName": "fulltext",
            "pageSize": 100,
            "pageNum": 1,
            "column": "bj",
            "category": _CNINFO_PERIOD_CATEGORIES,
            "plate": "bj;third",
            "seDate": "",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        },
    )
    announcements = payload.get("announcements") if isinstance(payload, dict) else None
    results: list[dict[str, Any]] = []
    company_page = (
        f"{_CNINFO_ROOT}/new/disclosure/stock?stockCode={code}"
        f"&orgId={org_id}"
    )
    for item in announcements or []:
        if not isinstance(item, dict) or str(item.get("adjunctType") or "").upper() != "PDF":
            continue
        title = str(item.get("announcementTitle") or "").strip()
        announcement_id = str(item.get("announcementId") or "").strip()
        adjunct = str(item.get("adjunctUrl") or "").strip()
        if not title or not announcement_id or not adjunct.startswith("finalpage/"):
            continue
        pdf_url = _allowed_pdf_url(urljoin(_CNINFO_STATIC_ROOT, adjunct), "bse")
        if not pdf_url:
            continue
        published_at = _published_date(item.get("announcementTime"))
        candidate = _candidate(
            company=company,
            title=title,
            announcement_id=announcement_id,
            published_at=published_at,
            pdf_url=pdf_url,
            announcement_url=company_page,
            provider="巨潮资讯（北交所定期报告）",
        )
        if candidate is not None:
            results.append(candidate)
    return results


def _report_candidates(company: dict[str, str]) -> list[dict[str, Any]]:
    if company["exchange"] == "bse":
        candidates = _cninfo_candidates(company)
    else:
        candidates = _rss_candidates(company)
    candidates.sort(
        key=lambda item: (
            item["period_rank"],
            item.get("published_at") or "",
            item.get("announcement_id") or "",
        ),
        reverse=True,
    )
    return candidates


def find_company_financial_reports(company_query: str, report_type: str = "latest") -> dict[str, Any]:
    """Return verifiable full-report candidates from an exchange's official source."""
    if report_type not in {"latest", *_REPORT_TYPE_FILTERS}:
        raise CompanyReportError("不支持的财报类型。", code="invalid_report_type")
    company = _resolve_company(company_query)
    candidates = _report_candidates(company)
    if report_type != "latest":
        accepted_types = _REPORT_TYPE_FILTERS[report_type]
        candidates = [item for item in candidates if item["report_type"] in accepted_types]
    public_candidates = [
        {key: value for key, value in item.items() if key != "period_rank"}
        for item in candidates[:10]
    ]
    return {
        "success": True,
        "company": company,
        "report_type": report_type,
        "candidate_count": len(public_candidates),
        "recommended_candidate_id": public_candidates[0]["candidate_id"] if public_candidates else None,
        "candidates": public_candidates,
        "source": {
            "exchange": company["exchange"],
            "provider": public_candidates[0]["source_provider"] if public_candidates else (
                "RSSHub/" + _EXCHANGE_LABELS[company["exchange"]]
                if company["exchange"] != "bse"
                else "巨潮资讯（北交所定期报告）"
            ),
        },
        "errors": [],
        "warnings": [],
    }


def _safe_filename(company: dict[str, str], candidate: dict[str, Any]) -> str:
    source = f"{company['name']}_{company['code']}_{candidate['report_period']}_{candidate['title']}"
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", source)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")[:430]
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned


def _stream_official_pdf(url: str, exchange: str, destination: Path) -> int:
    current = _allowed_pdf_url(url, exchange)
    if not current:
        raise CompanyReportError("公告原件链接不属于该交易所的官方 PDF 域名。", code="untrusted_pdf_url")
    total = 0
    prefix = bytearray()
    try:
        with httpx.Client(
            timeout=_DOWNLOAD_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1"},
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                with client.stream("GET", current) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location or redirect_count >= _MAX_REDIRECTS:
                            raise CompanyReportError("PDF 下载重定向无效或次数超限。", code="invalid_pdf_redirect")
                        current = _allowed_pdf_url(urljoin(current, location), exchange)
                        if not current:
                            raise CompanyReportError("PDF 下载重定向离开了交易所官方域名。", code="untrusted_pdf_redirect")
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
                        raise CompanyReportError("财报 PDF 超过知识库单文件大小限制。", code="file_too_large")
                    with destination.open("wb") as output:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > MAX_UPLOAD_BYTES:
                                raise CompanyReportError("财报 PDF 超过知识库单文件大小限制。", code="file_too_large")
                            if len(prefix) < 1_024:
                                prefix.extend(chunk[: 1_024 - len(prefix)])
                            output.write(chunk)
                    break
            else:
                raise CompanyReportError("PDF 下载重定向无效或次数超限。", code="invalid_pdf_redirect")
    except CompanyReportError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise CompanyReportError(
            "交易所 PDF 原件下载失败；知识库未写入不完整文件。",
            code="pdf_download_failed",
            retryable=True,
        ) from exc
    if total < 16 or b"%PDF-" not in prefix:
        raise CompanyReportError("官方链接返回的内容不是有效 PDF。", code="invalid_pdf_signature")
    return total


def existing_company_financial_report(
    *, company_query: str, candidate_id: str, report_title: str,
    knowledge_base_id: str, tenant_id: str, owner_id: str,
    kb_service: RagKnowledgeBaseService | None = None,
) -> dict[str, Any] | None:
    """Match an owned original by its persisted official filing identity.

    A filename alone is not proof of identity. Reconstruct the same candidate
    key used by disclosure discovery; this is a local read, not a download.
    """
    service = kb_service or RagKnowledgeBaseService()
    for document in service.list_documents(
        knowledge_base_id, tenant_id=tenant_id, owner_id=owner_id,
    ):
        source = document.get("source") or {}
        code = str(source.get("security_code") or "")
        name = str(source.get("security_name") or "")
        if _normalized_name(company_query) not in {_normalized_name(code), _normalized_name(name)}:
            continue
        title = str(source.get("announcement_title") or "")
        exchange = next((key for key, label in _EXCHANGE_LABELS.items()
                         if source.get("exchange") in {key, label}), None)
        if not exchange or not code or not source.get("announcement_id") or not source.get("pdf_url"):
            continue
        identity = _candidate_id(
            exchange=exchange, company_code=code,
            announcement_id=str(source["announcement_id"]),
            title=title, pdf_url=str(source["pdf_url"]),
        )
        if identity == candidate_id and title == report_title:
            return document
    return None


def existing_report_result(document: Mapping[str, Any]) -> dict[str, Any]:
    """Report a read-only reuse; do not imply that PDF contents were read."""
    source = document.get("source") or {}
    searchable = bool(document.get("active_index_version_id"))
    return {
        "success": True,
        "company": {"code": source.get("security_code"), "name": source.get("security_name")},
        "report": {
            "title": source.get("announcement_title"), "report_period": source.get("report_period"),
            "exchange": source.get("exchange"), "published_at": source.get("published_at"),
            "pdf_url": source.get("pdf_url"),
        },
        "document": dict(document), "downloaded_bytes": 0, "duplicate": True,
        "read_only_reuse": True, "searchable": searchable,
        "source_scope": "knowledge_base_document_inventory",
        "message": (
            "同一官方财报已存在且可检索，已复用；没有下载或修改知识库。分析内容请调用 search_knowledge_base。"
            if searchable else
            "同一官方财报已存在，但尚无活动索引；没有重复下载或修改，请根据文档处理状态说明内容取证缺口。"
        ),
        "errors": [], "warnings": [],
    }


def import_company_financial_report(
    *,
    company_query: str,
    candidate_id: str,
    report_title: str,
    knowledge_base_id: str,
    tenant_id: str,
    owner_id: str,
) -> dict[str, Any]:
    """Revalidate a discovered candidate, then import its exact official PDF into RAG."""
    kb_service = RagKnowledgeBaseService()
    existing = existing_company_financial_report(
        company_query=company_query, candidate_id=candidate_id, report_title=report_title,
        knowledge_base_id=knowledge_base_id, tenant_id=tenant_id, owner_id=owner_id,
        kb_service=kb_service,
    )
    if existing is not None:
        return existing_report_result(existing)
    company = _resolve_company(company_query)
    candidates = _report_candidates(company)
    candidate = next(
        (
            item for item in candidates
            if item["candidate_id"] == str(candidate_id or "").strip()
            and item["title"] == str(report_title or "").strip()
        ),
        None,
    )
    if candidate is None:
        raise CompanyReportError(
            "候选报告已过期或与当前公司不匹配；请重新检索后选择。",
            code="report_candidate_stale",
        )

    kb_service.assert_knowledge_base(knowledge_base_id, tenant_id=tenant_id, owner_id=owner_id)
    incoming = rag_storage_root() / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="exchange-report-", suffix=".part", dir=incoming)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        report_tool_progress(
            f"正在从{_EXCHANGE_LABELS[company['exchange']]}下载 {candidate['report_period']} 原始 PDF",
            progress=20,
        )
        downloaded_bytes = _stream_official_pdf(candidate["pdf_url"], company["exchange"], temp_path)
        report_tool_progress("原件已下载，校验 PDF 并提交知识库入库", progress=65)
        document = kb_service.upload_pdf_path(
            knowledge_base_id,
            _safe_filename(company, candidate),
            temp_path,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        # Source metadata persistence returns a fresh document projection and
        # intentionally does not carry upload-only idempotency fields.
        duplicate = bool(document.get("duplicate"))
        source_metadata = {
            "provider": candidate["source_provider"],
            "exchange": candidate["exchange_label"],
            "security_code": company["code"],
            "security_name": company["name"],
            "report_type": candidate["report_type"],
            "report_period": candidate["report_period"],
            "announcement_id": candidate["announcement_id"],
            "announcement_title": candidate["title"],
            "published_at": candidate["published_at"],
            "pdf_url": candidate["pdf_url"],
            "announcement_url": candidate["announcement_url"],
        }
        document = kb_service.set_document_source(
            str(document["id"]),
            source_metadata,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        document["duplicate"] = duplicate
    finally:
        temp_path.unlink(missing_ok=True)
    if duplicate and document.get("status") == "unsupported":
        completion_message = "知识库中已存在同一原始 PDF，但上次识别未通过；原件已保留，可在管理页点击“重新识别”。"
    elif duplicate:
        completion_message = "知识库中已存在同一原始 PDF，已复用现有文档和处理状态。"
    else:
        completion_message = "原始财报已入库，后台开始解析和建立索引。"
    report_tool_progress(completion_message, progress=100)
    return {
        "success": True,
        "company": {"code": company["code"], "name": company["name"]},
        "report": {
            "title": candidate["title"],
            "report_period": candidate["report_period"],
            "exchange": candidate["exchange_label"],
            "published_at": candidate["published_at"],
            "pdf_url": candidate["pdf_url"],
        },
        "document": document,
        "downloaded_bytes": downloaded_bytes,
        "duplicate": duplicate,
        "message": completion_message,
        "errors": [],
        "warnings": [],
    }


__all__ = [
    "CompanyReportError",
    "find_company_financial_reports",
    "import_company_financial_report",
]
