"""Regression tests for the P0 fixes.

They cover the parts that were broken:

* the comparison rules are applied when the comparison runs (not when the
  file was parsed),
* the tables do not lose a row and do not lose a formula,
* a background task is only run by one worker,
* the credentials are neither echoed nor stored in plain text,
* the OCR interface cannot be pointed at an internal address.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

from app.config import settings
from app.domain.models import Block, BlockType, Document
from app.domain.normalize import normalise
from app.engines.document_diff import diff_documents
from app.engines.spreadsheet_diff import diff_workbooks
from app.parsers.registry import parse_file
from app.schemas import CompareConfig


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _document(paragraphs: list[str]) -> Document:
    return Document(
        document_id="doc",
        blocks=[
            Block(
                block_id=f"b_{index}",
                type=BlockType.PARAGRAPH,
                order=index,
                original_text=text,
                normalized_text=normalise(text),
            )
            for index, text in enumerate(paragraphs)
        ],
    )


def _diff(a: list[str], b: list[str], **kwargs) -> list:
    config = CompareConfig(**kwargs)
    changes, _ = diff_documents(_document(a), _document(b), config)
    return changes


def _build_workbook(path: Path, sheets: dict, formulas: dict | None = None) -> Path:
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(title=name)
        for row in rows:
            worksheet.append(row)
    for coordinate, formula in (formulas or {}).items():
        sheet_name, cell = coordinate.split("!")
        workbook[sheet_name][cell] = formula
    workbook.save(str(path))
    return path


def _compare_workbooks(tmp_path: Path, a: dict, b: dict, formulas_a=None, formulas_b=None, **kwargs):
    config = CompareConfig(**kwargs)
    path_a = _build_workbook(tmp_path / "a.xlsx", a, formulas_a)
    path_b = _build_workbook(tmp_path / "b.xlsx", b, formulas_b)
    result_a = parse_file(path_a, "a.xlsx", config)
    result_b = parse_file(path_b, "b.xlsx", config)
    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    return changes


# --------------------------------------------------------------------------
# the comparison rules are applied when the comparison runs
# --------------------------------------------------------------------------


def test_ignore_case_is_applied_at_comparison_time():
    assert _diff(["Hello"], ["hello"], ignore_case=True) == []
    assert _diff(["Hello"], ["hello"]) != []


def test_ignore_punctuation_is_applied_at_comparison_time():
    assert _diff(["金额,单位"], ["金额单位"], ignore_punctuation=True) == []
    assert _diff(["金额,单位"], ["金额单位"]) != []


def test_the_full_width_characters_can_be_told_apart():
    assert _diff(["ＡＢＣ"], ["ABC"], ignore_full_width=True) == []
    assert _diff(["ＡＢＣ"], ["ABC"], ignore_full_width=False) != []


def test_the_whitespace_can_be_told_apart():
    assert _diff(["a b"], ["a  b"], ignore_whitespace=True) == []
    assert _diff(["a b"], ["a  b"], ignore_whitespace=False) != []


def test_the_numbering_can_be_ignored_at_comparison_time():
    assert _diff(["第一条 内容"], ["1. 内容"], ignore_numbering=True) == []
    assert _diff(["第一条 内容"], ["1. 内容"]) != []


def test_a_reorder_is_reported_when_the_move_detection_is_off():
    changes = _diff(["第一段", "第二段"], ["第二段", "第一段"], detect_moved_paragraphs=False)
    assert changes, "a reorder must not disappear from the result"
    assert {change.primary_type for change in changes} == {"added", "deleted"}


def test_a_reorder_is_reported_as_a_move_by_default():
    changes = _diff(["第一段", "第二段"], ["第二段", "第一段"])
    assert [change.primary_type for change in changes] == ["moved"]


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


def test_a_formula_is_not_treated_as_an_empty_cell(tmp_path):
    changes = _compare_workbooks(
        tmp_path,
        {"Sheet1": [["编号", "金额"]]},
        {"Sheet1": [["编号", "金额"]]},
        formulas_a={"Sheet1!B2": "=1+1"},
        formulas_b={"Sheet1!B2": "=2+2"},
        ignore_blank_cells=True,
    )
    assert changes, "a changed formula must be reported"


def test_a_sheet_without_a_header_does_not_lose_its_first_row(tmp_path):
    changes = _compare_workbooks(
        tmp_path,
        {"Sheet1": [["10", "20"], ["30", "40"]]},
        {"Sheet1": [["10", "21"], ["30", "40"]]},
    )
    assert changes, "the first row of a sheet without a header must be compared"
    assert changes[0].primary_type == "modified"


def test_the_key_columns_are_found_on_both_sides(tmp_path):
    changes = _compare_workbooks(
        tmp_path,
        {"Sheet1": [["ID", "金额"], ["001", 100]]},
        {"Sheet1": [["金额", "ID"], [100, "001"]]},
        primary_key_columns=["ID"],
    )
    types = [change.primary_type for change in changes]
    assert "deleted" not in types
    assert "added" not in types


def test_the_cells_are_compared_with_the_rules_of_the_comparison(tmp_path):
    changes = _compare_workbooks(
        tmp_path,
        {"Sheet1": [["名称"], ["ABC"]]},
        {"Sheet1": [["名称"], ["abc"]]},
        ignore_case=True,
    )
    assert changes == []


# --------------------------------------------------------------------------
# header and footer
# --------------------------------------------------------------------------


def _docx_with_header(path: Path, with_header: bool) -> Path:
    from docx import Document as DocxDocument

    document = DocxDocument()
    document.add_paragraph("正文内容")
    if with_header:
        try:
            paragraph = document.add_paragraph("页眉文字")
            paragraph.style = document.styles["Header"]
        except KeyError:  # pragma: no cover - the style is part of the template
            pytest.skip("the template has no header style")
    document.save(str(path))
    return path


def test_a_header_paragraph_is_ignored_by_default(tmp_path):
    config = CompareConfig()
    with_header = parse_file(_docx_with_header(tmp_path / "a.docx", True), "a.docx", config)
    without = parse_file(_docx_with_header(tmp_path / "b.docx", False), "b.docx", config)
    texts = [block.original_text for block in with_header.document.blocks]
    assert "页眉文字" in texts, "the header is kept by the parser"

    changes, _ = diff_documents(
        with_header.document, without.document, CompareConfig(ignore_headers_footers=True)
    )
    assert changes == [], "the header is ignored by default"

    changes, _ = diff_documents(
        with_header.document, without.document, CompareConfig(ignore_headers_footers=False)
    )
    assert changes, "the header is compared when the rule says so"


def test_a_pdf_running_header_takes_part_when_the_rule_says_so(tmp_path):
    from tests.fixtures import build_pdf
    from app.compare.pdf_compare import compare_pdfs

    path_a = build_pdf(tmp_path / "a.pdf", [["body one"], ["body two"]], header="Company-A")
    path_b = build_pdf(tmp_path / "b.pdf", [["body one"], ["body two"]], header="Company-B")
    parse_a = parse_file(path_a, "a.pdf", CompareConfig())
    parse_b = parse_file(path_b, "b.pdf", CompareConfig())

    changes, _ = compare_pdfs(
        parse_a, parse_b, CompareConfig(ignore_headers_footers=True)
    )
    assert changes == []

    changes, _ = compare_pdfs(
        parse_a, parse_b, CompareConfig(ignore_headers_footers=False)
    )
    assert changes, "the header must be compared when the rule says so"


def test_a_rotated_page_keeps_the_table_inside_the_table(tmp_path):
    import fitz

    path = tmp_path / "rotated.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    for x in (50, 150, 250):
        page.draw_line((x, 50), (x, 150))
    for y in (50, 100, 150):
        page.draw_line((50, y), (250, y))
    for x, y, text in (
        (55, 70, "alpha"),
        (155, 70, "beta"),
        (55, 120, "1"),
        (155, 120, "2"),
    ):
        page.insert_text((x, y), text)
    page.set_rotation(90)
    document.save(str(path))
    document.close()

    result = parse_file(path, "rotated.pdf", CompareConfig())
    tables = [
        block for block in result.document.blocks if block.type == BlockType.TABLE
    ]
    assert tables, "the table must be recognised"
    assert tables[0].original_text == "alpha\tbeta\n1\t2"
    assert tables[0].source_position.bbox == pytest.approx([50, 50, 250, 150])

    # The text of the table is not repeated in the character stream.
    stream = "".join(
        char.ch for char in result.parsed_pdf.chars if not char.ch.isspace()
    )
    assert "alpha" not in stream


# --------------------------------------------------------------------------
# the background work is only done by one worker
# --------------------------------------------------------------------------


def test_only_one_worker_can_claim_a_comparison():
    from app.db import Comparison, SessionLocal, init_db
    from app.services import leases

    init_db()
    session = SessionLocal()
    try:
        comparison = Comparison(
            comparison_id="cmp_lease_test",
            file_a_id="file_a",
            file_b_id="file_b",
            mode="document_structure",
            status="uploaded",
            config_json={},
            stats={},
            notices=[],
        )
        session.add(comparison)
        session.commit()

        first = leases.claim_comparison(session, "cmp_lease_test", "uploaded")
        second = leases.claim_comparison(session, "cmp_lease_test", "uploaded")
        assert first is not None
        assert second is None, "a task must not be claimed twice"
    finally:
        session.close()


def test_the_cleanup_of_one_thread_does_not_delete_the_files_of_another():
    from app.parsers import libreoffice

    directory = Path(tempfile.mkdtemp())
    mine = directory / "mine.tmp"
    other = directory / "other.tmp"
    mine.write_text("mine")
    other.write_text("other")

    ready = threading.Event()
    done = threading.Event()

    def worker() -> None:
        libreoffice._pending().append(other)
        ready.set()
        done.wait(2)

    thread = threading.Thread(target=worker)
    thread.start()
    ready.wait(2)

    # The conversion of the other thread must survive the cleanup.
    libreoffice._pending().append(mine)
    libreoffice.cleanup_converted()

    assert not mine.exists()
    assert other.exists(), "the file of the other thread must not be deleted"

    done.set()
    thread.join(2)
    other.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# the credentials
# --------------------------------------------------------------------------


def test_the_credentials_are_not_sent_back_to_the_browser():
    from app.api.settings import _sanitised_values
    from app.parsers.ocr_client import get_provider
    from app.services.app_settings import OcrSettings

    custom = get_provider("custom")
    assert custom is not None
    values = _sanitised_values(
        custom,
        OcrSettings(
            provider="custom",
            values={
                "endpoint": "https://example.com/ocr",
                "headers": "Authorization: Bearer very-secret",
            },
        ),
    )
    assert values["headers"] == "", "the request headers are a credential"
    assert values["endpoint"] == "https://example.com/ocr"

    baidu = get_provider("baidu")
    assert baidu is not None
    values = _sanitised_values(
        baidu,
        OcrSettings(provider="baidu", values={"api_key": "the-key", "secret_key": "the-secret"}),
    )
    assert values["api_key"] == ""
    assert values["secret_key"] == ""


def test_the_credentials_are_encrypted_in_the_database():
    from app.db import AppSetting, SessionLocal, init_db
    from app.services import app_settings

    init_db()
    session = SessionLocal()
    try:
        session.query(AppSetting).filter_by(key="ocr").delete()
        session.commit()
        app_settings.save(
            session,
            app_settings.OcrSettings(
                provider="baidu",
                values={
                    "api_key": "the-key",
                    "secret_key": "the-secret",
                    "language_type": "CHN_ENG",
                },
            ),
            {"api_key", "secret_key"},
        )
        row = session.get(AppSetting, "ocr")
        assert row is not None
        stored = row.value["values"]
        assert stored["api_key"].startswith("enc:v1:")
        assert stored["secret_key"].startswith("enc:v1:")
        assert stored["language_type"] == "CHN_ENG"

        app_settings.load(session)
        assert app_settings.get().values["api_key"] == "the-key"
        assert app_settings.get().values["secret_key"] == "the-secret"
    finally:
        session.query(AppSetting).filter_by(key="ocr").delete()
        session.commit()
        session.close()
        app_settings.reset()


def test_an_old_plain_text_configuration_is_still_readable():
    from app.db import AppSetting, SessionLocal, init_db
    from app.services import app_settings

    init_db()
    session = SessionLocal()
    try:
        session.query(AppSetting).filter_by(key="ocr").delete()
        session.commit()
        session.add(
            AppSetting(
                key="ocr",
                value={
                    "provider": "baidu",
                    "values": {"api_key": "old-key", "secret_key": "old-secret"},
                    "dpi": 200,
                    "max_pages": 100,
                },
            )
        )
        session.commit()
        app_settings.load(session)
        assert app_settings.get().values["api_key"] == "old-key"
    finally:
        session.query(AppSetting).filter_by(key="ocr").delete()
        session.commit()
        session.close()
        app_settings.reset()


# --------------------------------------------------------------------------
# the OCR interface
# --------------------------------------------------------------------------


def test_the_endpoint_guard_blocks_the_internal_addresses():
    from app.services.url_guard import BlockedEndpoint, validate_endpoint

    with pytest.raises(BlockedEndpoint):
        validate_endpoint("http://127.0.0.1:8000/api/health")
    with pytest.raises(BlockedEndpoint):
        validate_endpoint("http://169.254.169.254/latest/meta-data")
    with pytest.raises(BlockedEndpoint):
        validate_endpoint("http://10.0.0.5/ocr")
    with pytest.raises(BlockedEndpoint):
        validate_endpoint("file:///etc/passwd")
    with pytest.raises(BlockedEndpoint):
        validate_endpoint("http://192.168.1.10/ocr")

    # A public address is fine.
    validate_endpoint("https://8.8.8.8/ocr")


def test_the_internal_addresses_are_allowed_with_the_switch(monkeypatch):
    from app.services.url_guard import validate_endpoint

    monkeypatch.setattr(settings, "ocr_allow_private_endpoints", True)
    validate_endpoint("http://127.0.0.1:8000/api/health")


# --------------------------------------------------------------------------
# the tests do not touch the deployment
# --------------------------------------------------------------------------


def test_the_tests_run_against_a_throw_away_database():
    assert settings.database_url.startswith("sqlite:///"), (
        "the tests must never use the database of the deployment"
    )
    assert str(settings.project_dir).startswith(tempfile.gettempdir())
