"""Shared test fixtures.

The environment variables are set *before* the application is imported: the
settings are read once when ``app.config`` is imported, so a test run must
never touch the database or the uploads of a deployment - even when the
container exports ``DOC_COMPARE_DATABASE_URL`` and ``DOC_COMPARE_DATA_DIR``.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: the throw-away directory of this test run
TEST_ROOT = tempfile.mkdtemp(prefix="doc-compare-tests-")
os.environ["DOC_COMPARE_DATABASE_URL"] = f"sqlite:///{TEST_ROOT}/test.db"
os.environ["DOC_COMPARE_DATA_DIR"] = TEST_ROOT

from tests.fixtures import build_doc, build_docx, build_pdf, build_xlsx  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook
    """Remove the database and the uploads of this test run."""
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


@pytest.fixture()
def tmp_files(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def docx_factory(tmp_path: Path):
    def factory(name: str, paragraphs: list[str], tables=None) -> Path:
        return build_docx(tmp_path / name, paragraphs, tables)

    return factory


@pytest.fixture()
def xlsx_factory(tmp_path: Path):
    def factory(name: str, sheets: dict, formulas: dict | None = None) -> Path:
        return build_xlsx(tmp_path / name, sheets, formulas)

    return factory


@pytest.fixture()
def pdf_factory(tmp_path: Path):
    def factory(name: str, pages: list[list[str]]) -> Path:
        return build_pdf(tmp_path / name, pages)

    return factory


@pytest.fixture()
def doc_factory(tmp_path: Path):
    def factory(name: str, paragraphs: list[str]) -> Path:
        return build_doc(tmp_path / name, paragraphs)

    return factory


@pytest.fixture()
def xls_available() -> bool:
    try:
        import xlrd  # noqa: F401
    except ImportError:
        return False
    return True
