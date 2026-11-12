"""Database layer (PRD chapter 14).

The schema follows the PRD table list.  SQLAlchemy is used so the same code
runs on SQLite (default, zero setup) and PostgreSQL (production) by changing
``DOC_COMPARE_DATABASE_URL``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from .config import settings

logger = logging.getLogger("doc_compare.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class FileRecord(Base):
    """Uploaded file metadata (PRD 14 - ``files``)."""

    __tablename__ = "files"

    file_id = Column(String(64), primary_key=True)
    filename = Column(String(512), nullable=False)
    stored_path = Column(String(1024), nullable=False)
    extension = Column(String(32), nullable=False)
    detected_type = Column(String(32), nullable=True)
    file_type = Column(String(32), nullable=False)
    content_type = Column(String(32), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    checksum = Column(String(64), nullable=True)
    parse_status = Column(String(32), nullable=False, default="uploaded")
    parser = Column(String(64), nullable=True)
    message = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    sheet_count = Column(Integer, nullable=True)
    block_count = Column(Integer, nullable=True)
    format_mismatch = Column(Boolean, nullable=False, default=False)
    uncovered = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=_now)

    parse_task = relationship("ParseTask", back_populates="file", uselist=False)


class ParseTask(Base):
    """Parsing state of a file (PRD 14 - ``parse_tasks``)."""

    __tablename__ = "parse_tasks"

    task_id = Column(String(64), primary_key=True)
    file_id = Column(String(64), ForeignKey("files.file_id"), nullable=False)
    status = Column(String(32), nullable=False, default="uploaded")
    stage = Column(String(32), nullable=True)
    progress = Column(Integer, nullable=False, default=0)
    parser = Column(String(64), nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)

    file = relationship("FileRecord", back_populates="parse_task")


class DocumentBlock(Base):
    """Structured document node (PRD 14 - ``document_blocks``)."""

    __tablename__ = "document_blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), ForeignKey("files.file_id"), nullable=False, index=True)
    block_id = Column(String(64), nullable=False, index=True)
    type = Column(String(32), nullable=False)
    order = Column(Integer, nullable=False, default=0)
    section_id = Column(String(64), nullable=True)
    section_title = Column(Text, nullable=True)
    level = Column(Integer, nullable=True)
    original_text = Column(Text, nullable=False, default="")
    normalized_text = Column(Text, nullable=False, default="")
    text_hash = Column(String(64), nullable=True, index=True)
    page = Column(Integer, nullable=True)
    source_position = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)


class DocumentTable(Base):
    """Table embedded in a document (PRD 14 - ``document_tables``)."""

    __tablename__ = "document_tables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), ForeignKey("files.file_id"), nullable=False, index=True)
    table_id = Column(String(64), nullable=False)
    block_id = Column(String(64), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    column_count = Column(Integer, nullable=False, default=0)
    source_position = Column(JSON, nullable=True)
    data = Column(JSON, nullable=False, default=dict)


class WorkbookRecord(Base):
    """Workbook metadata (PRD 14 - ``workbooks``)."""

    __tablename__ = "workbooks"

    workbook_id = Column(String(64), primary_key=True)
    file_id = Column(String(64), ForeignKey("files.file_id"), nullable=False, index=True)
    filename = Column(String(512), nullable=True)
    sheet_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_now)


class WorkbookSheet(Base):
    """Worksheet (PRD 14 - ``workbook_sheets``)."""

    __tablename__ = "workbook_sheets"

    sheet_id = Column(String(64), primary_key=True)
    workbook_id = Column(String(64), ForeignKey("workbooks.workbook_id"), nullable=False, index=True)
    file_id = Column(String(64), ForeignKey("files.file_id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    order = Column(Integer, nullable=False, default=0)
    row_count = Column(Integer, nullable=False, default=0)
    column_count = Column(Integer, nullable=False, default=0)
    hidden = Column(Boolean, nullable=False, default=False)
    fingerprint = Column(String(64), nullable=True, index=True)


class WorkbookCell(Base):
    """Cell (PRD 14 - ``workbook_cells``).

    Cells are stored row by row rather than in one big JSON blob so that large
    workbooks stay queryable (PRD 14 - last paragraph).
    """

    __tablename__ = "workbook_cells"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), ForeignKey("files.file_id"), nullable=False, index=True)
    sheet_id = Column(String(64), ForeignKey("workbook_sheets.sheet_id"), nullable=False, index=True)
    cell_id = Column(String(64), nullable=False)
    coordinate = Column(String(32), nullable=True)
    row_index = Column(Integer, nullable=False, default=0, index=True)
    column_index = Column(Integer, nullable=False, default=0, index=True)
    value = Column(Text, nullable=True)
    normalized_value = Column(Text, nullable=True)
    formula = Column(Text, nullable=True)
    display_value = Column(Text, nullable=True)
    data_type = Column(String(16), nullable=True)
    number_format = Column(String(128), nullable=True)


class Comparison(Base):
    """Comparison task (PRD 14 - ``comparisons``)."""

    __tablename__ = "comparisons"

    comparison_id = Column(String(64), primary_key=True)
    file_a_id = Column(String(64), ForeignKey("files.file_id"), nullable=False)
    file_b_id = Column(String(64), ForeignKey("files.file_id"), nullable=False)
    mode = Column(String(32), nullable=False, default="document_structure")
    status = Column(String(32), nullable=False, default="uploaded")
    stage = Column(String(32), nullable=True)
    progress = Column(Integer, nullable=False, default=0)
    message = Column(Text, nullable=True)
    config_json = Column(JSON, nullable=False, default=dict)
    stats = Column(JSON, nullable=False, default=dict)
    notices = Column(JSON, nullable=False, default=list)
    #: when a worker took the task; it is refreshed while the task runs
    started_at = Column(DateTime, nullable=True)
    #: page map of the two PDFs and the pages without a text layer
    #: (``{"pairs": [...], "left_no_text_pages": [...], ...}``)
    page_mapping = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)


class ChangeEventRecord(Base):
    """Difference event (PRD 14 - ``change_events``)."""

    __tablename__ = "change_events"

    change_id = Column(String(64), primary_key=True)
    comparison_id = Column(
        String(64), ForeignKey("comparisons.comparison_id"), nullable=False, index=True
    )
    primary_type = Column(String(32), nullable=False, index=True)
    category = Column(String(32), nullable=False, default="text")
    document_a = Column(JSON, nullable=False, default=dict)
    document_b = Column(JSON, nullable=False, default=dict)
    summary = Column(Text, nullable=False, default="")
    details = Column(JSON, nullable=False, default=dict)
    order = Column(Integer, nullable=False, default=0)


class AiAnalysis(Base):
    """AI difference explanation (PRD 14 - ``ai_analysis``)."""

    __tablename__ = "ai_analysis"

    analysis_id = Column(String(64), primary_key=True)
    comparison_id = Column(
        String(64), ForeignKey("comparisons.comparison_id"), nullable=False, index=True
    )
    change_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)


class AppSetting(Base):
    """A setting the user changes in the browser.

    The OCR provider is not written into the code any more: the user enters
    the credentials in the interface and they are stored here.
    """

    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, default=_now)


# --------------------------------------------------------------------------
# Engine / session
# --------------------------------------------------------------------------

settings.ensure_directories()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


#: columns that were added after the first release.  ``create_all`` does not
#: touch an existing table, so they have to be added explicitly.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "comparisons": {
        "stage": "VARCHAR(32)",
        "progress": "INTEGER NOT NULL DEFAULT 0",
        "page_mapping": "JSON",
        "started_at": "TIMESTAMP",
    },
    "parse_tasks": {
        "stage": "VARCHAR(32)",
        "progress": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _migrate() -> None:
    """Add the columns that are missing in an older database.

    Two processes can start at the same time (a rolling deploy for example)
    and try the same ``ALTER TABLE``.  A lost race is harmless: the column
    exists afterwards either way, so the error is logged and ignored.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in existing:
                continue
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    )
            except Exception:  # noqa: BLE001 - another process won the race
                logger.warning("添加列 %s.%s 失败（可能已由其他进程添加）", table, name)


def get_session():
    """FastAPI dependency yielding a database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
