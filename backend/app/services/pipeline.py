"""Pipeline: parse -> normalise -> align -> diff -> persist.

This module owns the status transitions of PRD chapter 15.1.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..db import (
    ChangeEventRecord,
    Comparison,
    DocumentBlock,
    DocumentTable,
    FileRecord,
    ParseTask,
    WorkbookCell,
    WorkbookRecord,
    WorkbookSheet,
)
from ..domain.models import BlockType, ParseResult
from ..parsers.base import ParseFailed, UnsupportedFormat
from ..progress import ProgressCallback
from ..parsers.registry import parse_file
from ..schemas import ChangeEvent, CompareConfig
from .cache import store_parse
from ..storage import stored_path
from ..engines.dispatcher import compare as compare_parsed
from . import leases


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_and_store(
    session: Session,
    file_record: FileRecord,
    progress: ProgressCallback | None = None,
) -> ParseResult:
    """Parse a stored file and persist the structured result.

    ``progress`` is called with ``(percent, stage)`` while the file is
    parsed.  The values are written to the parse task so that the frontend
    can show the progress of a long running parse.
    """
    # The file says it was never parsed: a finished parse task has to be
    # taken again even though its status is terminal.
    lost_parse = file_record.parse_status == "uploaded"
    file_record.parse_status = "parsing"
    session.commit()

    task = session.query(ParseTask).filter_by(file_id=file_record.file_id).one_or_none()
    if task is None:
        task = ParseTask(task_id=f"pt_{file_record.file_id}", file_id=file_record.file_id)
        session.add(task)
        session.commit()

    # Claim the parse: only one worker may parse a file.  A worker that is
    # alive refreshes its claim, so a file that is parsed somewhere else is
    # never taken over.
    if not leases.claim_parse_task(session, task.task_id, allow_terminal=lost_parse):
        raise leases.AlreadyClaimed(
            f"文件「{file_record.filename}」正在被其它进程解析"
        )

    task.status = "parsing"
    task.stage = "detecting"
    task.progress = 0
    task.started_at = leases.now()
    session.commit()
    heartbeat = leases.start_heartbeat("parse", task.task_id)

    last: tuple[int, str] = (-1, "")

    def report(percent: int, stage: str) -> None:
        nonlocal last
        if progress is None:
            return
        progress(percent, stage)
        value = max(0, min(100, int(percent)))
        # Writing every single step would hammer the database, so only the
        # visible changes are stored.
        if abs(value - last[0]) < 2 and stage == last[1]:
            return
        last = (value, stage)
        task.stage = stage
        task.progress = value
        session.commit()

    path = stored_path(file_record.file_id)
    if path is None:
        heartbeat.set()
        _fail_parse(session, file_record, task, "原始文件不存在", "failed")
        raise ParseFailed("原始文件不存在")

    try:
        result = parse_file(
            path, file_record.filename, CompareConfig(), progress=report
        )
    except UnsupportedFormat as exc:
        heartbeat.set()
        _fail_parse(session, file_record, task, str(exc), "failed")
        raise
    except ParseFailed as exc:
        heartbeat.set()
        _fail_parse(session, file_record, task, exc.message, exc.status)
        raise
    except Exception:
        heartbeat.set()
        raise

    file_record.parse_status = result.parse_status
    file_record.parser = result.parser
    file_record.message = result.message
    file_record.content_type = result.content_type.value
    file_record.detected_type = result.metadata.get("detected_type")
    file_record.page_count = result.metadata.get("page_count")
    file_record.sheet_count = result.metadata.get("sheet_count")
    file_record.block_count = result.metadata.get("block_count")
    file_record.uncovered = result.uncovered
    file_record.format_mismatch = bool(result.metadata.get("format_mismatch"))

    task.status = result.parse_status
    task.parser = result.parser
    task.stage = "completed" if result.parse_status in {"success", "partial"} else result.parse_status
    task.progress = 100
    task.finished_at = _now()
    session.commit()

    _persist_structure(session, file_record, result)
    store_parse(file_record.file_id, result)
    heartbeat.set()
    return result


def _fail_parse(
    session: Session,
    file_record: FileRecord,
    task: ParseTask,
    message: str,
    status: str,
) -> None:
    """Record a parse failure on the file and on its parse task."""
    file_record.parse_status = status
    file_record.message = message
    task.status = status
    task.stage = "failed"
    task.error = message
    task.finished_at = _now()
    session.commit()


def _persist_structure(
    session: Session, file_record: FileRecord, result: ParseResult
) -> None:
    """Store the parsed nodes in the relational tables."""
    document = result.document
    if document is not None:
        session.query(DocumentBlock).filter_by(file_id=file_record.file_id).delete()
        session.query(DocumentTable).filter_by(file_id=file_record.file_id).delete()
        for block in document.blocks:
            session.add(
                DocumentBlock(
                    file_id=file_record.file_id,
                    block_id=block.block_id,
                    type=block.type.value,
                    order=block.order,
                    section_id=block.section_id,
                    section_title=block.section_title,
                    level=block.level,
                    original_text=block.original_text,
                    normalized_text=block.normalized_text,
                    text_hash=hashlib.sha256(
                        block.normalized_text.encode("utf-8")
                    ).hexdigest(),
                    page=block.source_position.page,
                    source_position=block.source_position.model_dump(),
                    metadata_json=block.metadata,
                )
            )
            if block.table is not None:
                session.add(
                    DocumentTable(
                        file_id=file_record.file_id,
                        table_id=block.table.table_id,
                        block_id=block.block_id,
                        row_count=len(block.table.rows),
                        column_count=block.table.column_count,
                        source_position=block.table.source_position.model_dump(),
                        data={
                            "rows": [row.model_dump() for row in block.table.rows],
                            # The visual layout (the spans of merged cells) is
                            # kept so the preview can render merged cells
                            # correctly even after a restart.
                            "layout": block.table.layout,
                        },
                    )
                )

    workbook = result.workbook
    if workbook is not None:
        # The workbook id must be unique per upload - two different files may
        # share the same file name (PRD 4.2 allows duplicate uploads).
        workbook.workbook_id = f"wb_{file_record.file_id}"
        session.query(WorkbookCell).filter_by(file_id=file_record.file_id).delete()
        session.query(WorkbookSheet).filter_by(file_id=file_record.file_id).delete()
        session.query(WorkbookRecord).filter_by(file_id=file_record.file_id).delete()
        session.add(
            WorkbookRecord(
                workbook_id=workbook.workbook_id,
                file_id=file_record.file_id,
                filename=file_record.filename,
                sheet_count=len(workbook.sheets),
            )
        )
        # Flush the parent rows first: the child rows carry foreign keys and
        # the insert order across independent mappers is not guaranteed.
        session.flush()

        for sheet in workbook.sheets:
            session.add(
                WorkbookSheet(
                    sheet_id=f"{file_record.file_id}:{sheet.sheet_id}",
                    workbook_id=workbook.workbook_id,
                    file_id=file_record.file_id,
                    name=sheet.name,
                    order=sheet.order,
                    row_count=sheet.row_count,
                    column_count=sheet.column_count,
                    hidden=sheet.hidden,
                )
            )
        session.flush()

        for sheet in workbook.sheets:
            for row in sheet.rows:
                for cell in row.cells:
                    session.add(
                        WorkbookCell(
                            file_id=file_record.file_id,
                            sheet_id=f"{file_record.file_id}:{sheet.sheet_id}",
                            cell_id=cell.cell_id,
                            coordinate=cell.coordinate,
                            row_index=cell.row_index,
                            column_index=cell.column_index,
                            value=cell.value,
                            normalized_value=cell.normalized_value,
                            formula=cell.formula,
                            display_value=cell.display_value,
                            data_type=cell.data_type,
                            number_format=cell.number_format,
                        )
                    )
    session.commit()


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def compute_stats(changes: list[ChangeEvent]) -> dict[str, int]:
    stats = {"total": len(changes), "added": 0, "deleted": 0, "modified": 0, "moved": 0}
    for change in changes:
        if change.primary_type in stats:
            stats[change.primary_type] += 1
    return stats


def _ensure_parsed_pdf(
    config: CompareConfig,
    file_id: str,
    parse_result: ParseResult,
) -> None:
    """Parse the file again when the character stream is missing.

    The character stream is not stored in the database, so a parse result
    that was rebuilt after a restart of the service does not have it.  The
    PDF is parsed again in that case - the comparison of two PDFs needs it.
    """
    if parse_result.parsed_pdf is not None or parse_result.file_type != "pdf":
        return
    path = stored_path(file_id)
    if path is None or not path.exists():
        return
    try:
        from ..parsers.pdf_parser import PdfParser

        fresh = PdfParser().parse(path, config)
    except Exception:  # noqa: BLE001 - the fallback is the old engine
        return
    parse_result.parsed_pdf = fresh.parsed_pdf


def run_comparison(
    session: Session,
    comparison: Comparison,
    parse_a: ParseResult,
    parse_b: ParseResult,
    progress: ProgressCallback | None = None,
) -> tuple[list[ChangeEvent], list[str], str]:
    """Run the diff and persist the results."""
    config = CompareConfig(**(comparison.config_json or {}))
    comparison.status = "comparing"
    comparison.stage = "aligning"
    comparison.progress = 40
    # A re-run must not show the message of the previous attempt.
    comparison.message = None
    session.commit()

    last: tuple[int, str] = (-1, "")

    def report(percent: int, stage: str) -> None:
        nonlocal last
        if progress is not None:
            progress(percent, stage)
        value = max(0, min(100, int(percent)))
        if abs(value - last[0]) < 2 and stage == last[1]:
            return
        last = (value, stage)
        comparison.stage = stage
        comparison.progress = value
        session.commit()

    if config.pdf_stream_mode:
        _ensure_parsed_pdf(config, comparison.file_a_id, parse_a)
        _ensure_parsed_pdf(config, comparison.file_b_id, parse_b)

    layout: dict = {}
    mode, changes, notices = compare_parsed(parse_a, parse_b, config, report, layout)
    session.query(ChangeEventRecord).filter_by(
        comparison_id=comparison.comparison_id
    ).delete()
    for change in changes:
        session.add(
            ChangeEventRecord(
                change_id=f"{comparison.comparison_id}:{change.change_id}",
                comparison_id=comparison.comparison_id,
                primary_type=change.primary_type,
                category=change.category,
                document_a=change.document_a.model_dump(),
                document_b=change.document_b.model_dump(),
                summary=change.summary,
                details=change.details,
                order=change.order,
            )
        )

    comparison.mode = mode
    comparison.status = "completed"
    comparison.stage = "completed"
    comparison.progress = 100
    comparison.stats = compute_stats(changes)
    comparison.notices = notices
    comparison.page_mapping = layout
    comparison.finished_at = _now()
    session.commit()
    return changes, notices, mode
