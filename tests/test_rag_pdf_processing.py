from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from src.rag import pdf_processing


def _install_fake_docling(monkeypatch: pytest.MonkeyPatch, items, *, pages=None, omit_conversion_pages=()):
    monkeypatch.setattr(pdf_processing, "pdf_parser_version", lambda: "2.129.0")
    docling = types.ModuleType("docling")
    docling.__path__ = []
    datamodel = types.ModuleType("docling.datamodel")
    datamodel.__path__ = []
    settings_module = types.ModuleType("docling.datamodel.settings")
    settings_module.settings = SimpleNamespace(debug=SimpleNamespace(profile_pipeline_timings=False))
    base_models = types.ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = SimpleNamespace(PDF="pdf")
    pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")

    class AcceleratorOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class PdfPipelineOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.table_structure_options = SimpleNamespace(mode=None, do_cell_matching=None)

    class TableFormerMode:
        ACCURATE = "accurate"

    class RapidOcrOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    pipeline_options.AcceleratorOptions = AcceleratorOptions
    pipeline_options.PdfPipelineOptions = PdfPipelineOptions
    pipeline_options.RapidOcrOptions = RapidOcrOptions
    pipeline_options.TableFormerMode = TableFormerMode
    converter_module = types.ModuleType("docling.document_converter")

    class PdfFormatOption:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeDocument:
        def __init__(self, page_range):
            self.page_range = page_range
            first, last = page_range
            self.pages = {number: object() for number in range(first, last + 1)}

        def iterate_items(self):
            first, last = self.page_range
            return iter(
                item for item in items
                if first <= int(getattr(item[0].prov[0], "page_no", 0)) <= last
            )

    converter_instances = []

    class DocumentConverter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.calls = []
            converter_instances.append(self)

        def convert(self, _path, *, page_range):
            options = self.kwargs["format_options"]["pdf"].kwargs["pipeline_options"]
            use_ocr = options.kwargs["do_ocr"]
            self.calls.append((page_range, use_ocr))
            if use_ocr:
                assert options.kwargs["ocr_options"].kwargs["lang"] == ["ch"]
            assert options.kwargs["do_table_structure"] is True
            assert options.document_timeout == pdf_processing.PDF_PARSE_BATCH_TIMEOUT_SECONDS
            first, last = page_range
            conversion_pages = [
                SimpleNamespace(page_no=number)
                for number in range(first, last + 1)
                if number not in omit_conversion_pages
            ]
            return SimpleNamespace(document=FakeDocument(page_range), pages=conversion_pages)

    converter_module.PdfFormatOption = PdfFormatOption
    converter_module.DocumentConverter = DocumentConverter
    for name, module in {
        "docling": docling,
        "docling.datamodel": datamodel,
        "docling.datamodel.base_models": base_models,
        "docling.datamodel.settings": settings_module,
        "docling.datamodel.pipeline_options": pipeline_options,
        "docling.document_converter": converter_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return converter_instances


def _item(label: str, text: str, page: int = 1, *, level: int = 0):
    return (
        SimpleNamespace(
            label=SimpleNamespace(value=label),
            text=text,
            prov=[SimpleNamespace(page_no=page)],
        ),
        level,
    )


def test_parse_pdf_preserves_page_provenance_and_table_markdown(tmp_path, monkeypatch) -> None:
    image_warning = "<!-- 🖼️❌ Image not available. Please use `PdfPipelineOptions(generate_picture_images=True)` -->"
    table = SimpleNamespace(
        label=SimpleNamespace(value="table"),
        prov=[SimpleNamespace(page_no=2)],
        export_to_markdown=lambda **_kwargs: "| 指标 | 数值 |\n|---|---|\n| 收入 | 100 |",
    )
    paragraph = "这是一个用于验证结构化 PDF 解析的段落，必须保留原始页码并且文本内容足够长以通过质量检查。" * 2
    _install_fake_docling(
        monkeypatch,
        [
            _item("section_header", "第二章 财务摘要"),
            _item("picture", f"{image_warning}\n图 1 产品示意图"),
            _item("text", paragraph),
            (table, 0),
        ],
        pages={1: object(), 2: object()},
    )
    monkeypatch.setattr(pdf_processing, "count_pdf_pages", lambda *_args, **_kwargs: 2)
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-fake")
    progress: list[tuple[int, int]] = []

    parsed = pdf_processing.parse_pdf(source, progress_callback=lambda current, total: progress.append((current, total)))

    assert parsed.page_count == 2
    assert progress[-1] == (2, 2)
    assert "第二章 财务摘要" in parsed.pages[0].text
    assert "图 1 产品示意图" in parsed.pages[0].text
    assert "Image not available" not in parsed.pages[0].text
    assert all("Image not available" not in chunk.text for chunk in parsed.chunks)
    assert any(chunk.page_start == 2 and "| 收入 | 100 |" in chunk.text for chunk in parsed.chunks)
    assert any(chunk.section == "第二章 财务摘要" for chunk in parsed.chunks)


def test_parse_pdf_uses_bounded_docling_batches_and_reports_page_progress(tmp_path, monkeypatch) -> None:
    long_text = "用于验证分页结构解析的原生文本内容，必须保留全局页码和章节上下文。" * 2
    items = [
        _item("section_header", "财务报告", page=1),
        _item("text", long_text, page=1),
        _item("text", long_text, page=2),
        _item("text", long_text, page=3),
    ]
    converter_instances = _install_fake_docling(
        monkeypatch,
        items,
        pages={1: object(), 2: object(), 3: object()},
    )
    monkeypatch.setattr(pdf_processing, "count_pdf_pages", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(pdf_processing, "PDF_PARSE_BATCH_PAGES", 2)
    monkeypatch.setattr(pdf_processing.os, "cpu_count", lambda: 8)
    source = tmp_path / "large-report.pdf"
    source.write_bytes(b"%PDF-fake")
    progress: list[tuple[int, int]] = []

    parsed = pdf_processing.parse_pdf(
        source,
        progress_callback=lambda current, total: progress.append((current, total)),
    )

    assert parsed.page_count == 3
    assert converter_instances[0].calls == [((1, 2), True), ((3, 3), True)]
    pipeline = converter_instances[0].kwargs["format_options"]["pdf"].kwargs["pipeline_options"]
    assert pipeline.kwargs["layout_batch_size"] == pdf_processing.PDF_LAYOUT_BATCH_SIZE == 8
    assert pipeline.kwargs["table_batch_size"] == pdf_processing.PDF_TABLE_BATCH_SIZE == 8
    assert pipeline.kwargs["accelerator_options"].kwargs["num_threads"] == 8
    assert pipeline.table_structure_options.mode == "accurate"
    assert pipeline.table_structure_options.do_cell_matching is True
    assert [page.page_number for page in parsed.pages] == [1, 2, 3]
    assert "财务报告" in parsed.chunks[0].section
    assert progress.index((2, 3)) < progress.index((3, 3))


def test_docling_thread_count_is_bounded_by_available_cpus(monkeypatch) -> None:
    monkeypatch.setattr(pdf_processing.os, "cpu_count", lambda: 32)
    assert pdf_processing._docling_thread_count() == 8

    monkeypatch.setattr(pdf_processing.os, "cpu_count", lambda: 2)
    assert pdf_processing._docling_thread_count() == 2

    monkeypatch.setattr(pdf_processing.os, "cpu_count", lambda: None)
    assert pdf_processing._docling_thread_count() == 4


def test_parse_pdf_switches_between_native_text_and_ocr_batches(tmp_path, monkeypatch) -> None:
    long_text = "用于验证混合文本 PDF 按页启用 OCR，同时保留完整页码和检索内容。" * 2
    items = [_item("text", long_text, page=number) for number in range(1, 6)]
    converter_instances = _install_fake_docling(monkeypatch, items)
    monkeypatch.setattr(pdf_processing, "count_pdf_pages", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(pdf_processing, "_pdf_pages_requiring_ocr", lambda *_args: {2, 3})
    monkeypatch.setattr(pdf_processing, "PDF_PARSE_BATCH_PAGES", 3)
    source = tmp_path / "mixed.pdf"
    source.write_bytes(b"%PDF-fake")

    parsed = pdf_processing.parse_pdf(source)

    assert parsed.page_count == 5
    assert sorted(call for converter in converter_instances for call in converter.calls) == [
        ((1, 1), False),
        ((2, 3), True),
        ((4, 5), False),
    ]


def test_parse_pdf_fails_closed_when_a_docling_batch_omits_a_page(tmp_path, monkeypatch) -> None:
    _install_fake_docling(
        monkeypatch,
        [_item("text", "这是足够长的正文段落，测试批次中缺页必须失败。" * 2)],
        omit_conversion_pages={2},
    )
    monkeypatch.setattr(pdf_processing, "count_pdf_pages", lambda *_args, **_kwargs: 2)
    source = tmp_path / "incomplete.pdf"
    source.write_bytes(b"%PDF-fake")

    with pytest.raises(pdf_processing.PdfProcessingError) as error:
        pdf_processing.parse_pdf(source)

    assert error.value.code == "parse_incomplete"


def test_parse_pdf_rejects_document_when_ocr_returns_no_text(tmp_path, monkeypatch) -> None:
    _install_fake_docling(monkeypatch, [], pages={1: object()})
    monkeypatch.setattr(pdf_processing, "count_pdf_pages", lambda *_args, **_kwargs: 1)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-fake")

    with pytest.raises(pdf_processing.PdfProcessingError) as error:
        pdf_processing.parse_pdf(source)

    assert error.value.code == "extraction_quality_low"
    assert error.value.unsupported is True


def test_pdf_parser_version_includes_the_ocr_profile(monkeypatch) -> None:
    versions = {"docling": "2.129.0", "rapidocr": "3.9.2"}
    monkeypatch.setattr(pdf_processing, "version", lambda package: versions[package])

    assert pdf_processing.pdf_parser_version() == "2.129.0+rapidocr-3.9.2-ch-page-aware-v3"


def test_pdf_page_preflight_enables_ocr_for_sparse_or_image_heavy_pages(monkeypatch, tmp_path) -> None:
    class FakeText:
        def __init__(self, text):
            self.text = text

        def get_text(self):
            return self.text

    class FakeImage:
        x0, y0, x1, y1 = 0, 0, 50, 100

    class FakePage:
        x0, y0, x1, y1 = 0, 0, 100, 100
        width, height = 100, 100

        def __init__(self, *items):
            self._objs = list(items)

        def __iter__(self):
            return iter(self._objs)

    high_level = types.ModuleType("pdfminer.high_level")
    high_level.extract_pages = lambda _path: iter(
        [
            FakePage(FakeText("中" * 400)),
            FakePage(FakeText("中" * 600), FakeImage()),
            FakePage(FakeText("短" * 20)),
        ]
    )
    layout = types.ModuleType("pdfminer.layout")
    layout.LTImage = FakeImage
    layout.LTTextContainer = FakeText
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", high_level)
    monkeypatch.setitem(sys.modules, "pdfminer.layout", layout)

    assert pdf_processing._pdf_pages_requiring_ocr(tmp_path / "sample.pdf", 3) == {2, 3}


def test_pdf_batches_keep_ocr_modes_separate_and_bounded(monkeypatch) -> None:
    monkeypatch.setattr(pdf_processing, "PDF_PARSE_BATCH_PAGES", 3)

    assert list(pdf_processing._pdf_processing_batches(9, {2, 3, 7})) == [
        (1, 1, False),
        (2, 3, True),
        (4, 6, False),
        (7, 7, True),
        (8, 9, False),
    ]


def test_parse_pdf_fails_closed_when_docling_is_missing(tmp_path, monkeypatch) -> None:
    def forbidden_fallback(*_args, **_kwargs):
        pytest.fail("PDF parsing must not silently switch to another parser")

    module = types.ModuleType("pdfplumber")
    module.open = forbidden_fallback
    monkeypatch.setitem(sys.modules, "docling", None)
    monkeypatch.setitem(sys.modules, "pdfplumber", module)
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-fake")

    with pytest.raises(pdf_processing.PdfProcessingError) as error:
        pdf_processing.parse_pdf(source)

    assert error.value.code == "parser_dependency_missing"


def test_chunk_blocks_keeps_table_headers_and_page_locations() -> None:
    blocks = [
        {"kind": "text", "page_start": 1, "page_end": 1, "section": "概览", "text": "甲" * 60},
        {
            "kind": "table",
            "page_start": 2,
            "page_end": 2,
            "section": "指标",
            "text": "| 指标 | 数值 |\n|---|---|\n" + "".join(f"| 项目{i} | {i} |\n" for i in range(20)),
        },
    ]

    chunks = pdf_processing.chunk_blocks(blocks, target_chars=70, overlap_chars=10)

    table_chunks = [chunk for chunk in chunks if chunk.metadata["kind"] == "table_rows"]
    assert chunks[0].page_start == 1
    assert table_chunks
    assert all(chunk.page_start == chunk.page_end == 2 for chunk in table_chunks)
    assert all("| 指标 | 数值 |" in chunk.text for chunk in table_chunks)
    assert all("|---|---|" in chunk.text for chunk in table_chunks)
