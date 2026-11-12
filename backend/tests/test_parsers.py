"""Parser acceptance tests (PRD 17.1)."""

from __future__ import annotations

from app.domain.models import BlockType
from app.parsers.detect import detect_file_type
from app.parsers.registry import parse_file
from app.schemas import CompareConfig


def test_detect_docx(docx_factory):
    path = docx_factory("a.docx", ["内容"])
    detection = detect_file_type(path, "a.docx")
    assert detection.file_type == "docx"
    assert detection.mismatch is False


def test_detect_xlsx(xlsx_factory):
    path = xlsx_factory("a.xlsx", {"Sheet1": [["列"]]})
    detection = detect_file_type(path, "a.xlsx")
    assert detection.file_type == "xlsx"


def test_detect_pdf(pdf_factory):
    path = pdf_factory("a.pdf", [["content"]])
    detection = detect_file_type(path, "a.pdf")
    assert detection.file_type == "pdf"


def test_detect_doc(doc_factory):
    path = doc_factory("a.doc", ["内容"])
    detection = detect_file_type(path, "a.doc")
    assert detection.file_type == "doc"


def test_extension_mismatch_is_reported(pdf_factory):
    """A PDF named .docx must be detected as a mismatch (PRD 4.2)."""
    path = pdf_factory("a.docx", [["content"]])
    detection = detect_file_type(path, "a.docx")
    assert detection.file_type == "pdf"
    assert detection.mismatch is True
    assert detection.detail


def test_unknown_format_is_detected(tmp_path):
    path = tmp_path / "a.docx"
    path.write_bytes(b"not a real document at all")
    detection = detect_file_type(path, "a.docx")
    assert detection.file_type == "unknown"


def test_docx_structure_is_preserved(docx_factory):
    path = docx_factory(
        "a.docx",
        ["# 标题", "正文段落"],
        tables=[[["编号", "名称"], ["001", "项目"]]],
    )
    result = parse_file(path, "a.docx", CompareConfig())
    assert result.parse_status == "success"
    assert result.document is not None
    kinds = [block.type.value for block in result.document.blocks]
    assert "heading" in kinds
    assert "paragraph" in kinds
    assert "table" in kinds


def test_xlsx_structure_is_preserved(xlsx_factory):
    path = xlsx_factory(
        "a.xlsx", {"Sheet1": [["编号", "名称"], ["001", "项目A"]], "Sheet2": [["x"]]}
    )
    result = parse_file(path, "a.xlsx", CompareConfig())
    assert result.workbook is not None
    assert [sheet.name for sheet in result.workbook.sheets] == ["Sheet1", "Sheet2"]
    assert result.workbook.sheets[0].rows[1].cells[0].value == "001"


def test_doc_text_is_extracted(doc_factory):
    path = doc_factory("a.doc", ["第一段内容", "第二段内容"])
    result = parse_file(path, "a.doc", CompareConfig())
    assert result.parse_status == "success"
    assert result.document is not None
    texts = [block.original_text for block in result.document.blocks]
    assert "第一段内容" in texts
    assert "第二段内容" in texts


def test_pdf_text_is_extracted(pdf_factory):
    path = pdf_factory("a.pdf", [["line one", "line two"]])
    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.parse_status == "success"
    assert result.document is not None
    texts = [block.original_text for block in result.document.blocks]
    assert "line one" in texts


def test_pdf_wrapped_lines_form_one_paragraph(pdf_factory):
    """The lines of a paragraph belong together (PRD 8.2)."""
    path = pdf_factory(
        "a.pdf",
        [[["first line of the paragraph,", "second line of it,", "third line."]]],
    )
    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.document is not None
    blocks = result.document.blocks
    assert len(blocks) == 1
    assert blocks[0].original_text == (
        "first line of the paragraph, second line of it, third line."
    )


def test_pdf_paragraphs_stay_separate(pdf_factory):
    """Two paragraphs are two blocks, even when they look alike."""
    path = pdf_factory(
        "a.pdf",
        [[["first line of a paragraph", "second line of a paragraph"], "another one"]],
    )
    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.document is not None
    texts = [block.original_text for block in result.document.blocks]
    assert texts == [
        "first line of a paragraph second line of a paragraph",
        "another one",
    ]


def test_pdf_running_header_is_removed(tmp_path):
    """A header that repeats on every page is not part of the content."""
    from tests.fixtures import build_pdf

    path = build_pdf(
        tmp_path / "a.pdf",
        [["page one"], ["page two"]],
        header="Company name",
        footer="Page 1",
    )
    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.document is not None
    texts = [block.original_text for block in result.document.blocks]
    assert texts == ["page one", "page two"]


def test_pdf_table_is_recognised(tmp_path):
    """A table without ruling lines is recognised by its columns (PRD 8.3)."""
    from tests.fixtures import build_pdf_table

    path = build_pdf_table(
        tmp_path / "a.pdf",
        [
            ["item", "amount", "change"],
            ["revenue", "12,345", "+8.6%"],
            ["profit", "1,820", "+11.0%"],
        ],
    )
    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.document is not None
    tables = [block for block in result.document.blocks if block.type == BlockType.TABLE]
    assert len(tables) == 1
    table = tables[0].table
    assert table is not None
    assert len(table.rows) == 3
    assert table.column_count == 3
    assert table.cell_text(0, 0) == "item"
    assert table.cell_text(1, 1) == "12,345"

    # The text of the table must not show up a second time as a paragraph.
    texts = [block.original_text for block in result.document.blocks]
    assert "revenue" not in texts


def test_pdf_paragraph_across_a_page_break_is_joined(tmp_path):
    """A paragraph that is cut by a page break stays one paragraph (PRD 8.2)."""
    from tests.fixtures import build_pdf

    path = build_pdf(
        tmp_path / "a.pdf",
        [
            [
                "This paragraph is cut by the page break and continues on the "
                "next page, so both halves have to end up in the same block."
            ],
            ["The second half of the paragraph."],
        ],
    )
    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.document is not None
    blocks = result.document.blocks
    assert len(blocks) == 1
    assert blocks[0].original_text.startswith("This paragraph is cut by the page")
    assert blocks[0].original_text.endswith("The second half of the paragraph.")
    assert blocks[0].source_position.page == 1


def test_pdf_two_columns_are_read_column_by_column(tmp_path):
    """A page in two columns is read column by column, not line by line."""
    from tests.fixtures import build_pdf_positions

    items = []
    for row in range(12):
        y = 760 - row * 20
        items.append((72.0, float(y), f"left column line {row}."))
        items.append((330.0, float(y), f"right column line {row}."))
    path = build_pdf_positions(tmp_path / "a.pdf", [items])

    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.document is not None
    texts = [block.original_text for block in result.document.blocks]
    assert texts[0] == "left column line 0."
    assert texts[11] == "left column line 11."
    assert texts[12] == "right column line 0."
    assert texts[23] == "right column line 11."


def test_empty_pdf_is_reported_as_scanned(pdf_factory):
    path = pdf_factory("a.pdf", [[""]])
    result = parse_file(path, "a.pdf", CompareConfig())
    assert result.parse_status in {"unsupported_ocr", "partial"}
    if result.parse_status == "unsupported_ocr":
        assert result.metadata.get("ocr_enabled") is None
        assert "OCR" in (result.message or "")


def test_cfb_reader_roundtrip(tmp_path):
    """The OLE2 reader must be able to read back what it wrote."""
    from app.parsers.cfb import CompoundFile
    from tests.fixtures import build_ole2

    path = build_ole2(tmp_path / "a.bin", {"Alpha": b"hello world", "Beta": b"x" * 300})
    cfb = CompoundFile(path.read_bytes())
    assert "Alpha" in cfb.stream_names()
    assert cfb.read_stream("Alpha") == b"hello world"
    assert cfb.read_stream("Beta") == b"x" * 300
