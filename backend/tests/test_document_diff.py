"""Document comparison acceptance tests (PRD 17.2)."""

from __future__ import annotations

from pathlib import Path

from app.domain.models import Block, BlockType, Document
from app.domain.normalize import normalise
from app.engines.document_diff import align_blocks, diff_documents
from app.parsers.registry import parse_file
from app.schemas import CompareConfig


def make_document(paragraphs: list[str]) -> Document:
    blocks = [
        Block(
            block_id=f"b_{index}",
            type=BlockType.PARAGRAPH,
            order=index,
            original_text=text,
            normalized_text=normalise(text),
        )
        for index, text in enumerate(paragraphs)
    ]
    return Document(document_id="doc", blocks=blocks)


def run(paragraphs_a: list[str], paragraphs_b: list[str]) -> list:
    config = CompareConfig()
    document_a = make_document(paragraphs_a)
    document_b = make_document(paragraphs_b)
    changes, _ = diff_documents(document_a, document_b, config)
    return changes


def types_of(changes) -> list[str]:
    return [change.primary_type for change in changes]


# --------------------------------------------------------------------------
# 17.2 - the 13 mandatory cases
# --------------------------------------------------------------------------


def test_insert_paragraph_in_the_middle():
    a = ["第一段内容", "第二段内容", "第三段内容"]
    b = ["第一段内容", "第二段内容", "新增的自然段", "第三段内容"]
    changes = run(a, b)
    assert types_of(changes) == ["added"]
    assert changes[0].document_b.text == "新增的自然段"


def test_insert_paragraph_at_the_end():
    a = ["第一段内容", "第二段内容"]
    b = ["第一段内容", "第二段内容", "结尾新增段"]
    changes = run(a, b)
    assert types_of(changes) == ["added"]


def test_delete_paragraph():
    a = ["第一段内容", "将被删除的段落", "第三段内容"]
    b = ["第一段内容", "第三段内容"]
    changes = run(a, b)
    assert types_of(changes) == ["deleted"]
    assert "将被删除" in changes[0].document_a.text


def test_modify_paragraph():
    a = ["合同金额为100万元"]
    b = ["合同金额为120万元"]
    changes = run(a, b)
    assert types_of(changes) == ["modified"]
    assert changes[0].category == "numeric"
    assert changes[0].document_a.highlight == "100万元"
    assert changes[0].document_b.highlight == "120万元"


def test_inserted_paragraph_does_not_cascade():
    """Acceptance case 5 - the core case of PRD 17.2."""
    a = [f"第{i}段内容" for i in range(1, 101)]
    b = list(a)
    b.insert(20, "插入的新自然段")

    changes = run(a, b)
    assert types_of(changes) == ["added"]
    assert changes[0].document_b.text == "插入的新自然段"


def test_moved_paragraph_is_detected():
    a = ["标题段落", "第一段内容", "第二段内容", "第三段内容"]
    b = ["标题段落", "第二段内容", "第三段内容", "第一段内容"]
    changes = run(a, b)
    assert "moved" in types_of(changes)


def test_reordered_sections_are_detected():
    a = ["# 第一章", "第一章内容", "# 第二章", "第二章内容"]
    b = ["# 第二章", "第二章内容", "# 第一章", "第一章内容"]
    changes = run(a, b)
    assert changes, "章节顺序调整应产生差异"
    assert all(change.primary_type in {"moved", "modified"} for change in changes)


def test_number_date_and_amount_changes():
    changes = run(["金额100元"], ["金额200元"])
    assert changes[0].category == "numeric"

    changes = run(["日期2024年1月1日"], ["日期2024年3月5日"])
    assert changes[0].category == "date"


def test_table_cell_change(docx_factory):
    path_a = docx_factory(
        "a.docx",
        ["正文段落"],
        tables=[[["编号", "金额"], ["001", "100"]]],
    )
    path_b = docx_factory(
        "b.docx",
        ["正文段落"],
        tables=[[["编号", "金额"], ["001", "200"]]],
    )
    config = CompareConfig()
    result_a = parse_file(path_a, "a.docx", config)
    result_b = parse_file(path_b, "b.docx", config)
    changes, _ = diff_documents(result_a.document, result_b.document, config)
    assert any(change.category == "table_cell" for change in changes)


def test_formatting_only_change_is_ignored(docx_factory):
    """Case 10 - a pure formatting change must not produce a difference."""
    import docx

    path_a = docx_factory("a.docx", ["合同金额为100万元"])
    path_b = docx_factory("b.docx", ["合同金额为100万元"])

    document = docx.Document(str(path_b))
    for run in document.paragraphs[0].runs:
        run.bold = True
        run.font.size = docx.shared.Pt(16)
    document.save(str(path_b))

    config = CompareConfig(ignore_formatting=True)
    result_a = parse_file(path_a, "a.docx", config)
    result_b = parse_file(path_b, "b.docx", config)
    changes, _ = diff_documents(result_a.document, result_b.document, config)
    assert changes == []


def test_docx_and_pdf_with_same_content(docx_factory, pdf_factory):
    path_docx = docx_factory(
        "a.docx",
        ["Project name: Alpha", "Contract amount: 100", "Build period: 12 months"],
    )
    path_pdf = pdf_factory(
        "a.pdf",
        [["Project name: Alpha", "Contract amount: 100", "Build period: 12 months"]],
    )
    config = CompareConfig()
    result_docx = parse_file(path_docx, "a.docx", config)
    result_pdf = parse_file(path_pdf, "a.pdf", config)

    from app.engines.dispatcher import compare

    mode, changes, _ = compare(result_docx, result_pdf, config)
    assert mode == "document_structure"
    assert changes == []


def test_doc_and_docx_with_same_content(doc_factory, docx_factory):
    path_doc = doc_factory("a.doc", ["Hello world", "Second paragraph"])
    path_docx = docx_factory("a.docx", ["Hello world", "Second paragraph"])
    config = CompareConfig()
    result_doc = parse_file(path_doc, "a.doc", config)
    result_docx = parse_file(path_docx, "a.docx", config)

    from app.engines.dispatcher import compare

    mode, changes, _ = compare(result_doc, result_docx, config)
    assert mode == "document_structure"
    assert changes == []


def test_wps_and_docx_with_same_content(docx_factory):
    """Case 13 - requires a genuine WPS sample.

    PRD 9.1 explicitly forbids substituting files produced by other software
    with a ``.wps`` extension.  LibreOffice cannot write the WPS binary
    format, so this case is skipped unless a real ``.wps`` sample is supplied
    through ``DOC_COMPARE_WPS_SAMPLE``.
    """
    import os

    import pytest

    from app.engines.dispatcher import compare
    from app.parsers.detect import detect_file_type

    sample = os.environ.get("DOC_COMPARE_WPS_SAMPLE")
    if not sample or not Path(sample).exists():
        pytest.skip(
            "需要真实 WPS 文件：设置 DOC_COMPARE_WPS_SAMPLE 指向 WPS 软件生成的 .wps 样本"
        )

    detection = detect_file_type(Path(sample), Path(sample).name)
    if detection.file_type != "wps":
        pytest.skip(f"样本不是 WPS 格式（识别为 {detection.file_type}）")

    path_docx = docx_factory("a.docx", ["Hello world", "Second paragraph"])
    config = CompareConfig()
    result_docx = parse_file(path_docx, "a.docx", config)
    result_wps = parse_file(Path(sample), Path(sample).name, config)

    mode, _, _ = compare(result_docx, result_wps, config)
    assert mode == "document_structure"


# --------------------------------------------------------------------------
# Alignment details
# --------------------------------------------------------------------------


def test_align_blocks_keeps_order_for_inserted_paragraph():
    a = make_document([f"段落{i}" for i in range(10)]).blocks
    b = make_document(["段落0", "段落1", "新增", *[f"段落{i}" for i in range(2, 10)]]).blocks
    matches = align_blocks(a, b, CompareConfig())
    matched = [m for m in matches if m.a_index is not None and m.b_index is not None]
    assert len(matched) == 10
    assert all(
        m.b_index - m.a_index in (0, 1) for m in matched
    ), "插入后仅应产生一处偏移"


def test_deleted_paragraph_is_reported_once():
    a = ["一", "二", "三"]
    b = ["一", "三"]
    changes = run(a, b)
    assert types_of(changes).count("deleted") == 1
