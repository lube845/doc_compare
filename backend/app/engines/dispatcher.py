"""Compare dispatcher (PRD 3.2).

The algorithm is chosen from the *parsed content*:

* both sides carry a workbook            -> spreadsheet engine
* both sides carry a document            -> document engine

Only same-format comparisons are supported, so a document is never compared
against a spreadsheet: such a pair is rejected when the task is created.
"""

from __future__ import annotations

from ..domain.models import ParseResult
from ..progress import ProgressCallback
from ..schemas import ChangeEvent, CompareConfig
from ..compare.pdf_compare import can_stream_compare, compare_pdfs
from .document_diff import diff_documents
from .spreadsheet_diff import diff_workbooks


class UnsupportedPair(ValueError):
    """The two files cannot be compared with each other."""


def decide_mode(parse_a: ParseResult, parse_b: ParseResult) -> str:
    """Choose the comparison mode from the parsed content.

    The two sides always have the same format (the API rejects every other
    combination), so the mode follows from the content type alone.
    """
    sheet_a = parse_a.workbook is not None
    sheet_b = parse_b.workbook is not None
    doc_a = parse_a.document is not None
    doc_b = parse_b.document is not None

    if sheet_a and sheet_b:
        return "spreadsheet_structure"
    if doc_a and doc_b:
        return "document_structure"
    raise UnsupportedPair("两个文件的内容结构不同，无法比对")


def compare(
    parse_a: ParseResult,
    parse_b: ParseResult,
    config: CompareConfig,
    progress: ProgressCallback | None = None,
    layout: dict | None = None,
) -> tuple[str, list[ChangeEvent], list[str]]:
    """Run the appropriate engine and return (mode, changes, notices).

    ``layout`` is filled by the PDF engine with the page map and the pages
    without a text layer; other engines leave it untouched.
    """
    mode = decide_mode(parse_a, parse_b)
    if progress is not None:
        progress(40, "aligning")
    notices: list[str] = []
    changes: list[ChangeEvent] = []

    if mode == "spreadsheet_structure":
        assert parse_a.workbook is not None and parse_b.workbook is not None
        sheet_changes, sheet_notices = diff_workbooks(
            parse_a.workbook, parse_b.workbook, config
        )
        changes.extend(sheet_changes)
        notices.extend(sheet_notices)
        return mode, changes, notices

    assert parse_a.document is not None and parse_b.document is not None
    # Two PDFs are compared through their character stream: the blocks
    # of a PDF are a guess of the typography and two PDFs of the same
    # document rarely guess it the same way (改造计划 阶段 4).
    if config.pdf_stream_mode and can_stream_compare(parse_a, parse_b):
        try:
            pdf_changes, pdf_notices = compare_pdfs(
                parse_a, parse_b, config, progress, layout
            )
        except Exception as exc:  # noqa: BLE001 - never lose the result
            notices.append(f"流式比对失败，已回退到块级比对：{exc}")
        else:
            changes.extend(pdf_changes)
            notices.extend(pdf_notices)
            return mode, changes, notices
    doc_changes, doc_notices = diff_documents(
        parse_a.document, parse_b.document, config, progress
    )
    changes.extend(doc_changes)
    notices.extend(doc_notices)
    return mode, changes, notices
