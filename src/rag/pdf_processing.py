"""PDF validation, Docling extraction and structure-aware chunking."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Iterable


MAX_PDF_PAGES = 500
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHUNKING_VERSION = "pdf-structure-v1-1400-180"
PDF_PARSER_NAME = "docling"
PDF_OCR_PROFILE = "ch-page-aware-v3"
CHUNK_TARGET_CHARS = 1_400
CHUNK_OVERLAP_CHARS = 180
PDF_OCR_MIN_NATIVE_TEXT_CHARS = 300
PDF_OCR_IMAGE_AREA_RATIO = 0.15
# Bound layout/table/OCR work so dense financial-report pages do not hit the
# per-conversion timeout on CPU-only workers.
PDF_PARSE_BATCH_PAGES = 8
PDF_PARSE_BATCH_TIMEOUT_SECONDS = 300
PDF_LAYOUT_BATCH_SIZE = 8
PDF_TABLE_BATCH_SIZE = 8
PDF_MAX_ACCELERATOR_THREADS = 8
logger = logging.getLogger(__name__)
_DOCLING_IMAGE_PLACEHOLDER = re.compile(
    r"<!--\s*🖼️❌\s*Image not available\.[\s\S]*?-->",
    re.IGNORECASE,
)


class PdfProcessingError(RuntimeError):
    """A safe, user-presentable PDF extraction failure."""

    def __init__(self, message: str, *, code: str, unsupported: bool = False):
        super().__init__(message)
        self.code = code
        self.unsupported = unsupported


@dataclass(frozen=True)
class PdfPage:
    page_number: int
    text: str
    structure: list[dict[str, Any]]


@dataclass(frozen=True)
class PdfChunk:
    chunk_index: int
    page_start: int
    page_end: int
    section: str
    text: str
    content_hash: str
    char_start: int
    char_end: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ParsedPdf:
    pages: list[PdfPage]
    chunks: list[PdfChunk]
    page_count: int
    text_length: int
    parser_version: str


def count_pdf_pages(path: str | Path, *, max_pages: int = MAX_PDF_PAGES) -> int:
    """Count pages using pdfminer without extracting or retaining page text."""
    try:
        from pdfminer.pdfpage import PDFPage

        count = 0
        with Path(path).open("rb") as stream:
            for _ in PDFPage.get_pages(
                stream,
                maxpages=max_pages + 1,
                check_extractable=False,
            ):
                count += 1
                if count > max_pages:
                    raise PdfProcessingError(
                        f"PDF 页数超过首版上限（{max_pages} 页）。",
                        code="page_limit_exceeded",
                        unsupported=True,
                    )
        if count < 1:
            raise PdfProcessingError("PDF 没有可读取的页面。", code="invalid_pdf")
        return count
    except PdfProcessingError:
        raise
    except Exception as exc:
        raise PdfProcessingError("PDF 文件损坏、加密或无法解析。", code="invalid_pdf") from exc


def pdf_parser_version() -> str:
    """Identify both the parser and OCR profile used for a reusable parse."""
    try:
        return f"{version('docling')}+rapidocr-{version('rapidocr')}-{PDF_OCR_PROFILE}"
    except PackageNotFoundError as exc:
        raise PdfProcessingError(
            "RAG PDF 解析依赖 Docling 或 RapidOCR 未安装/不完整；请修复 worker 依赖后重试。",
            code="parser_dependency_missing",
        ) from exc


def _pdf_pages_requiring_ocr(path: str | Path, page_count: int) -> set[int]:
    """Identify image/sparse-text pages; Docling remains the only content parser."""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTImage, LTTextContainer

        def nested_images(node: Any) -> Iterable[Any]:
            for child in getattr(node, "_objs", ()):
                if isinstance(child, LTImage):
                    yield child
                else:
                    yield from nested_images(child)

        profiles: dict[int, tuple[int, float]] = {}
        for page_number, layout in enumerate(extract_pages(str(path)), start=1):
            native_text_chars = sum(
                sum(not character.isspace() for character in element.get_text())
                for element in layout
                if isinstance(element, LTTextContainer)
            )
            page_area = max(1.0, float(layout.width) * float(layout.height))
            image_area = 0.0
            for image in nested_images(layout):
                width = max(0.0, min(float(layout.x1), float(image.x1)) - max(float(layout.x0), float(image.x0)))
                height = max(0.0, min(float(layout.y1), float(image.y1)) - max(float(layout.y0), float(image.y0)))
                image_area += width * height
            profiles[page_number] = (native_text_chars, min(1.0, image_area / page_area))

        if set(profiles) != set(range(1, page_count + 1)):
            raise ValueError("PDF page preflight did not return every page")
        return {
            page_number
            for page_number, (native_text_chars, image_area_ratio) in profiles.items()
            if _page_requires_ocr(native_text_chars, image_area_ratio)
        }
    except Exception:
        # Conservative: if preflight cannot classify a page, let Docling try OCR.
        logger.warning("PDF page preflight failed; enabling Docling OCR for all pages")
        return set(range(1, page_count + 1))


def _page_requires_ocr(native_text_chars: int, image_area_ratio: float) -> bool:
    return (
        native_text_chars < PDF_OCR_MIN_NATIVE_TEXT_CHARS
        or image_area_ratio >= PDF_OCR_IMAGE_AREA_RATIO
    )


def _pdf_processing_batches(
    page_count: int,
    ocr_pages: set[int],
) -> Iterable[tuple[int, int, bool]]:
    """Yield bounded contiguous page ranges with a consistent OCR profile."""
    batch_start = 1
    while batch_start <= page_count:
        use_ocr = batch_start in ocr_pages
        batch_end = batch_start
        max_end = min(page_count, batch_start + PDF_PARSE_BATCH_PAGES - 1)
        while batch_end < max_end and ((batch_end + 1 in ocr_pages) == use_ocr):
            batch_end += 1
        yield batch_start, batch_end, use_ocr
        batch_start = batch_end + 1


def _docling_thread_count() -> int:
    """Use available logical CPUs, capped at the empirically tested 8."""
    return max(1, min(PDF_MAX_ACCELERATOR_THREADS, int(os.cpu_count() or 4)))


def parse_pdf(
    path: str | Path,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ParsedPdf:
    """Parse native text and scanned PDF pages with Chinese OCR and page provenance."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorOptions,
            PdfPipelineOptions,
            RapidOcrOptions,
            TableFormerMode,
        )
        from docling.datamodel.settings import settings
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise PdfProcessingError(
            "RAG PDF 解析依赖 Docling/RapidOCR 不可用；请按当前平台锁文件重新安装 worker 依赖。",
            code="parser_dependency_missing",
        ) from exc
    parser_version = pdf_parser_version()
    settings.debug.profile_pipeline_timings = True
    page_count = count_pdf_pages(path)

    page_map: dict[int, list[str]] = {}
    structure_map: dict[int, list[dict[str, Any]]] = {}
    blocks: list[dict[str, Any]] = []
    current_section = ""
    reported_page = 0
    source_path = Path(path)
    ocr_pages = _pdf_pages_requiring_ocr(source_path, page_count)
    converters: dict[bool, Any] = {}

    def get_converter(use_ocr: bool) -> Any:
        if use_ocr not in converters:
            options = PdfPipelineOptions(
                do_ocr=use_ocr,
                do_table_structure=True,
                layout_batch_size=PDF_LAYOUT_BATCH_SIZE,
                table_batch_size=PDF_TABLE_BATCH_SIZE,
                accelerator_options=AcceleratorOptions(num_threads=_docling_thread_count()),
                **({"ocr_options": RapidOcrOptions(lang=["ch"])} if use_ocr else {}),
            )
            # Keep the financial-table quality that the current index was built
            # with; FAST and disabling cell matching both lost row/cell fidelity
            # in the measured filing.
            options.table_structure_options.mode = TableFormerMode.ACCURATE
            options.table_structure_options.do_cell_matching = True
            options.document_timeout = PDF_PARSE_BATCH_TIMEOUT_SECONDS
            options.generate_page_images = False
            options.generate_picture_images = False
            converters[use_ocr] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return converters[use_ocr]

    for batch_start, batch_end, use_ocr in _pdf_processing_batches(page_count, ocr_pages):
        batch_started_at = time.perf_counter()
        logger.info(
            "RAG PDF parse batch started pages=%s-%s ocr=%s table_mode=accurate "
            "layout_batch_size=%s table_batch_size=%s threads=%s",
            batch_start,
            batch_end,
            use_ocr,
            PDF_LAYOUT_BATCH_SIZE,
            PDF_TABLE_BATCH_SIZE,
            _docling_thread_count(),
        )
        try:
            conversion = get_converter(use_ocr).convert(source_path, page_range=(batch_start, batch_end))
        except Exception as exc:
            logger.warning(
                "RAG PDF parse batch failed pages=%s-%s elapsed_seconds=%.2f error_type=%s",
                batch_start,
                batch_end,
                time.perf_counter() - batch_started_at,
                type(exc).__name__,
            )
            raise PdfProcessingError(
                f"PDF 第 {batch_start}-{batch_end} 页结构解析失败；可重试该文档。",
                code="parse_failed",
            ) from exc

        converted_pages = {
            int(getattr(page, "page_no", 0) or 0)
            for page in (getattr(conversion, "pages", None) or [])
            if int(getattr(page, "page_no", 0) or 0) > 0
        }
        expected_pages = set(range(batch_start, batch_end + 1))
        if converted_pages != expected_pages:
            missing_pages = sorted(expected_pages - converted_pages)
            raise PdfProcessingError(
                f"PDF 第 {batch_start}-{batch_end} 页解析不完整，缺少页面：{missing_pages[:8]}。",
                code="parse_incomplete",
            )

        document = getattr(conversion, "document", None)
        if document is None:
            raise PdfProcessingError(
                f"PDF 第 {batch_start}-{batch_end} 页没有生成结构化解析结果。",
                code="parse_incomplete",
            )
        batch_block_count = 0
        batch_table_count = 0
        for item, level in document.iterate_items():
            label_value = getattr(getattr(item, "label", None), "value", None)
            label = str(label_value or getattr(item, "label", "text")).lower()
            raw_text = _item_text(item, document, label)
            if not raw_text:
                continue
            batch_block_count += 1
            batch_table_count += int(label == "table")
            provenance = list(getattr(item, "prov", None) or [])
            if not provenance:
                # A searchable passage without a reliable page cannot yield a
                # verifiable citation, so fail closed rather than invent a page.
                raise PdfProcessingError(
                    "PDF 解析结果缺少页面定位信息，无法建立可核验的引用。",
                    code="page_provenance_missing",
                    unsupported=True,
                )
            page_start = int(getattr(provenance[0], "page_no", 0) or 0)
            page_end = max(int(getattr(entry, "page_no", page_start) or page_start) for entry in provenance)
            if page_start < batch_start or page_end > batch_end or page_end < page_start:
                raise PdfProcessingError(
                    "PDF 解析结果的页码超出当前处理范围，无法建立可核验的引用。",
                    code="page_provenance_invalid",
                    unsupported=True,
                )
            if page_start < 1:
                raise PdfProcessingError(
                    "PDF 解析结果的页码无效，无法建立可核验的引用。",
                    code="page_provenance_invalid",
                    unsupported=True,
                )
            if progress_callback is not None and page_start > reported_page:
                reported_page = min(page_start, page_count)
                progress_callback(reported_page, page_count)
            if label in {"section_header", "title"}:
                current_section = raw_text.strip()[:500]
            block = {
                "page_start": page_start,
                "page_end": page_end,
                "kind": label,
                "level": int(level or 0),
                "section": current_section,
                "text": raw_text,
            }
            blocks.append(block)
            # Multi-page items are associated with their leading page for preview;
            # the chunk locator retains the full provenance range.
            page_map.setdefault(page_start, []).append(raw_text)
            structure_map.setdefault(page_start, []).append(
                {
                    "kind": label,
                    "section": current_section,
                    "level": int(level or 0),
                    "page_start": page_start,
                    "page_end": page_end,
                    "text": raw_text[:1_000],
                }
            )
        if progress_callback is not None and batch_end > reported_page:
            reported_page = batch_end
            progress_callback(reported_page, page_count)
        timings = getattr(conversion, "timings", {}) or {}

        def timing_total(stage: str) -> float:
            metric = timings.get(stage)
            total = getattr(metric, "total", None)
            return float(total()) if callable(total) else 0.0

        logger.info(
            "RAG PDF parse batch completed pages=%s-%s ocr=%s elapsed_seconds=%.2f "
            "tables=%s blocks=%s pipeline_seconds=%.2f layout_seconds=%.2f "
            "table_structure_seconds=%.2f",
            batch_start,
            batch_end,
            use_ocr,
            time.perf_counter() - batch_started_at,
            batch_table_count,
            batch_block_count,
            timing_total("pipeline_total"),
            timing_total("layout"),
            timing_total("table_structure"),
        )

    pages = [
        PdfPage(
            page_number=number,
            text="\n\n".join(page_map.get(number, [])).strip(),
            structure=structure_map.get(number, []),
        )
        for number in range(1, page_count + 1)
    ]
    text_length = sum(len(page.text) for page in pages)
    nonempty_pages = sum(len(page.text.strip()) >= 8 for page in pages)
    max_empty = max(1, math.ceil(page_count * 0.2))
    if text_length < max(80, page_count * 6) or page_count - nonempty_pages > max_empty:
        raise PdfProcessingError(
            "PDF 解析后仍未能从足够多的页面提取文本；文件可能分辨率过低、文字模糊，或主要由无可识别文字的图像组成。",
            code="extraction_quality_low",
            unsupported=True,
        )
    chunks = chunk_blocks(blocks)
    if not chunks:
        raise PdfProcessingError(
            "PDF 解析后仍未提取到可检索文本；文件可能主要由无可识别文字的图像组成。",
            code="extraction_quality_low",
            unsupported=True,
        )
    if progress_callback is not None:
        progress_callback(page_count, page_count)
    return ParsedPdf(pages, chunks, page_count, text_length, parser_version)


def _item_text(item: Any, document: Any, label: str) -> str:
    if label == "table":
        exporter = getattr(item, "export_to_markdown", None)
        if callable(exporter):
            try:
                return _clean_docling_text(exporter(doc=document))
            except TypeError:
                return _clean_docling_text(exporter())
    value = getattr(item, "text", None)
    if isinstance(value, str):
        return _clean_docling_text(value)
    exporter = getattr(item, "export_to_markdown", None)
    if callable(exporter):
        return _clean_docling_text(exporter(doc=document))
    return ""


def _clean_docling_text(value: Any) -> str:
    """Remove serializer diagnostics so they never become PDF content."""
    text = str(value or "")
    return _DOCLING_IMAGE_PLACEHOLDER.sub("", text).strip()


def chunk_blocks(
    blocks: Iterable[dict[str, Any]],
    *,
    target_chars: int = CHUNK_TARGET_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[PdfChunk]:
    """Group prose by page/heading while keeping tables as separate units."""
    normalized: list[PdfChunk] = []
    prose_buffer: list[dict[str, Any]] = []
    buffer_chars = 0
    buffer_section = ""
    buffer_page = 0
    page_cursor: dict[int, int] = {}

    def flush() -> None:
        nonlocal prose_buffer, buffer_chars, buffer_section, buffer_page
        if not prose_buffer:
            return
        body = "\n\n".join(str(item["text"]).strip() for item in prose_buffer if str(item["text"]).strip())
        if body:
            start = min(int(item["page_start"]) for item in prose_buffer)
            end = max(int(item["page_end"]) for item in prose_buffer)
            offset = page_cursor.get(start, 0)
            normalized.extend(
                _make_text_chunks(
                    body,
                    page_start=start,
                    page_end=end,
                    section=buffer_section,
                    target_chars=target_chars,
                    overlap_chars=overlap_chars,
                    start_offset=offset,
                    kind="prose",
                )
            )
            page_cursor[start] = offset + len(body)
        prose_buffer = []
        buffer_chars = 0
        buffer_section = ""
        buffer_page = 0

    for raw in blocks:
        block = dict(raw)
        text = str(block.get("text") or "").strip()
        kind = str(block.get("kind") or "text").lower()
        section = str(block.get("section") or "").strip()
        page = int(block.get("page_start") or 0)
        if not text or page < 1:
            continue
        if kind == "table":
            flush()
            offset = page_cursor.get(page, 0)
            normalized.extend(
                _make_table_chunks(
                    text,
                    page_start=page,
                    page_end=int(block.get("page_end") or page),
                    section=section,
                    target_chars=target_chars,
                    start_offset=offset,
                )
            )
            page_cursor[page] = offset + len(text)
            continue
        if kind in {"section_header", "title"}:
            flush()
            buffer_section = text[:500]
            continue
        if prose_buffer and (page != buffer_page or (section and section != buffer_section)):
            flush()
        if not prose_buffer:
            buffer_page = page
            buffer_section = section or buffer_section
        if buffer_chars and buffer_chars + len(text) > target_chars:
            flush()
            buffer_page = page
            buffer_section = section
        if len(text) > target_chars:
            flush()
            offset = page_cursor.get(page, 0)
            normalized.extend(
                _make_text_chunks(
                    text,
                    page_start=page,
                    page_end=int(block.get("page_end") or page),
                    section=section,
                    target_chars=target_chars,
                    overlap_chars=overlap_chars,
                    start_offset=offset,
                    kind="prose",
                )
            )
            page_cursor[page] = offset + len(text)
            continue
        prose_buffer.append(block)
        buffer_chars += len(text)
    flush()
    return [
        PdfChunk(
            chunk_index=index,
            page_start=item.page_start,
            page_end=item.page_end,
            section=item.section,
            text=item.text,
            content_hash=item.content_hash,
            char_start=item.char_start,
            char_end=item.char_end,
            metadata=item.metadata,
        )
        for index, item in enumerate(normalized)
    ]


def _make_text_chunks(
    text: str,
    *,
    page_start: int,
    page_end: int,
    section: str,
    target_chars: int,
    overlap_chars: int,
    start_offset: int,
    kind: str,
) -> list[PdfChunk]:
    pieces = _split_text(text, target_chars, overlap_chars)
    return [
        _chunk(
            piece,
            page_start=page_start,
            page_end=page_end,
            section=section,
            start=start_offset + start,
            end=start_offset + end,
            kind=kind,
        )
        for piece, start, end in pieces
    ]


def _make_table_chunks(
    text: str,
    *,
    page_start: int,
    page_end: int,
    section: str,
    target_chars: int,
    start_offset: int,
) -> list[PdfChunk]:
    if len(text) <= target_chars:
        return [_chunk(text, page_start, page_end, section, start_offset, start_offset + len(text), "table")]
    rows = text.splitlines()
    header = rows[:2] if len(rows) > 1 and rows[1].strip().startswith("|") else rows[:1]
    data_rows = rows[len(header):]
    output: list[PdfChunk] = []
    current = list(header)
    char_offset = start_offset
    for row in data_rows:
        candidate = "\n".join([*current, row])
        if len(candidate) > target_chars and len(current) > len(header):
            rendered = "\n".join(current)
            output.append(_chunk(rendered, page_start, page_end, section, char_offset, char_offset + len(rendered), "table_rows"))
            char_offset += len(rendered)
            current = list(header)
        current.append(row)
    if len(current) > len(header):
        rendered = "\n".join(current)
        output.append(_chunk(rendered, page_start, page_end, section, char_offset, char_offset + len(rendered), "table_rows"))
    return output or [_chunk(text, page_start, page_end, section, start_offset, start_offset + len(text), "table")]


def _split_text(text: str, target_chars: int, overlap_chars: int) -> list[tuple[str, int, int]]:
    if len(text) <= target_chars:
        return [(text, 0, len(text))]
    pieces: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + target_chars)
        end = hard_end
        if hard_end < len(text):
            candidates = [
                text.rfind(separator, start + target_chars // 2, hard_end)
                for separator in ("\n", "。", "！", "？", ". ", "；", "; ")
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + (1 if text[boundary] in "\n。！？；" else 2)
        piece = text[start:end].strip()
        if piece:
            leading = len(text[start:end]) - len(text[start:end].lstrip())
            trailing = len(text[start:end]) - len(text[start:end].rstrip())
            actual_start = start + leading
            actual_end = end - trailing
            pieces.append((piece, actual_start, actual_end))
        if end >= len(text):
            break
        next_start = max(start + 1, end - max(0, overlap_chars))
        start = next_start
    return pieces


def _chunk(
    text: str,
    page_start: int,
    page_end: int,
    section: str,
    start: int,
    end: int,
    kind: str,
) -> PdfChunk:
    body = text.strip()
    return PdfChunk(
        chunk_index=-1,
        page_start=page_start,
        page_end=page_end,
        section=section,
        text=body,
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        char_start=max(0, start),
        char_end=max(start, end),
        metadata={"kind": kind},
    )


__all__ = [
    "CHUNKING_VERSION",
    "MAX_PDF_PAGES",
    "MAX_UPLOAD_BYTES",
    "PDF_LAYOUT_BATCH_SIZE",
    "PDF_OCR_PROFILE",
    "PDF_PARSE_BATCH_PAGES",
    "PDF_PARSE_BATCH_TIMEOUT_SECONDS",
    "PDF_TABLE_BATCH_SIZE",
    "PDF_PARSER_NAME",
    "ParsedPdf",
    "PdfChunk",
    "PdfPage",
    "PdfProcessingError",
    "chunk_blocks",
    "count_pdf_pages",
    "parse_pdf",
    "pdf_parser_version",
]
