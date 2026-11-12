"""Same-format comparison tests.

Only files of the very same format may be compared (产品决策：仅支持同格式
比对), so every case below pairs two files of one format.
"""

from __future__ import annotations

import pytest

from app.engines.dispatcher import UnsupportedPair, compare, decide_mode
from app.parsers.registry import parse_file
from app.schemas import CompareConfig

DOC_LINES = [
    "项目名称：项目A",
    "合同金额：100万元",
    "建设周期：12个月",
]

DOC_LINES_CHANGED = [
    "项目名称：项目A",
    "合同金额：120万元",
    "建设周期：12个月",
]


def build_docx(path, paragraphs):
    from tests.fixtures import build_docx

    return build_docx(path, paragraphs)


def build_xlsx(path, sheets):
    from tests.fixtures import build_xlsx

    return build_xlsx(path, sheets)


def test_two_docx_files_use_document_structure(tmp_path):
    docx_a = build_docx(tmp_path / "a.docx", DOC_LINES)
    docx_b = build_docx(tmp_path / "b.docx", DOC_LINES_CHANGED)
    config = CompareConfig()
    result_a = parse_file(docx_a, "a.docx", config)
    result_b = parse_file(docx_b, "b.docx", config)

    assert decide_mode(result_a, result_b) == "document_structure"
    mode, changes, _ = compare(result_a, result_b, config)
    assert mode == "document_structure"
    assert any(change.primary_type == "modified" for change in changes)


def test_two_identical_docx_files_have_no_changes(tmp_path):
    docx_a = build_docx(tmp_path / "a.docx", DOC_LINES)
    docx_b = build_docx(tmp_path / "b.docx", DOC_LINES)
    config = CompareConfig()
    result_a = parse_file(docx_a, "a.docx", config)
    result_b = parse_file(docx_b, "b.docx", config)

    mode, changes, _ = compare(result_a, result_b, config)
    assert mode == "document_structure"
    assert changes == []


def test_two_xlsx_files_use_spreadsheet_structure(tmp_path):
    xlsx_a = build_xlsx(
        tmp_path / "a.xlsx",
        {"Sheet1": [["编号", "金额"], ["001", 100]]},
    )
    xlsx_b = build_xlsx(
        tmp_path / "b.xlsx",
        {"Sheet1": [["编号", "金额"], ["001", 200]]},
    )
    config = CompareConfig()
    result_a = parse_file(xlsx_a, "a.xlsx", config)
    result_b = parse_file(xlsx_b, "b.xlsx", config)

    assert decide_mode(result_a, result_b) == "spreadsheet_structure"
    mode, changes, _ = compare(result_a, result_b, config)
    assert mode == "spreadsheet_structure"
    assert changes


def test_a_document_and_a_spreadsheet_cannot_be_compared(tmp_path):
    """The cross-type engine is gone: such a pair is refused."""
    docx = build_docx(tmp_path / "a.docx", DOC_LINES)
    xlsx = build_xlsx(
        tmp_path / "a.xlsx",
        {
            "Sheet1": [
                ["字段", "内容"],
                ["项目名称", "项目A"],
            ]
        },
    )
    config = CompareConfig()
    result_doc = parse_file(docx, "a.docx", config)
    result_sheet = parse_file(xlsx, "a.xlsx", config)

    with pytest.raises(UnsupportedPair):
        decide_mode(result_doc, result_sheet)
    with pytest.raises(UnsupportedPair):
        compare(result_doc, result_sheet, config)


def test_cjk_typography_spaces_are_ignored():
    """Word/LibreOffice insert spaces around numbers in a document."""
    from app.domain.normalize import normalise

    assert normalise("合同金额：（100 万元）") == normalise("合同金额：（100万元）")
    assert normalise("建设周期：12 个月") == normalise("建设周期：12个月")
    # The rule must not touch whitespace between Latin words.
    assert normalise("first line") != normalise("firstline")


@pytest.mark.parametrize(
    "pair",
    [
        ("docx", "docx"),
        ("pdf", "pdf"),
        ("xlsx", "xlsx"),
    ],
)
def test_mode_selection_for_same_formats(tmp_path, pair):
    first, second = pair
    config = CompareConfig()

    def build(fmt, name):
        if fmt == "docx":
            return build_docx(tmp_path / name, ["内容"])
        if fmt == "pdf":
            from tests.fixtures import build_pdf

            return build_pdf(tmp_path / name, [["内容"]])
        return build_xlsx(tmp_path / name, {"Sheet1": [["列"]]})

    result_a = parse_file(build(first, f"a.{first}"), f"a.{first}", config)
    result_b = parse_file(build(second, f"b.{second}"), f"b.{second}", config)

    mode = decide_mode(result_a, result_b)
    assert mode in {"document_structure", "spreadsheet_structure"}
