"""Parse result cache.

The parsed structure is the most expensive artefact of the pipeline, so it is
kept in memory.  The relational tables remain the durable copy (PRD 14.5 -
"解析结果可以复用"): when the process restarts the structure is rebuilt from
those tables instead of asking the user to upload again.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db import (
    DocumentBlock,
    DocumentTable,
    FileRecord,
    WorkbookCell,
    WorkbookRecord,
    WorkbookSheet,
)
from ..domain.models import (
    Block,
    BlockType,
    Cell,
    ContentType,
    Document,
    ParseResult,
    Sheet,
    SheetRow,
    SourcePosition,
    TableData,
    TableRow,
    Workbook,
)

_CACHE: dict[str, ParseResult] = {}


def store_parse(file_id: str, result: ParseResult) -> None:
    _CACHE[file_id] = result


def get_cached_parse(file_id: str) -> ParseResult | None:
    return _CACHE.get(file_id)


def drop_parse(file_id: str) -> None:
    _CACHE.pop(file_id, None)


def load_parse(session: Session, file_id: str) -> ParseResult | None:
    """Return the parse result, rebuilding it from the database if needed."""
    cached = _CACHE.get(file_id)
    if cached is not None:
        return cached

    record = session.get(FileRecord, file_id)
    if record is None:
        return None

    document = _load_document(session, record)
    workbook = _load_workbook(session, record)
    if document is None and workbook is None:
        return None

    result = ParseResult(
        file_id=file_id,
        file_type=record.file_type,
        content_type=ContentType(record.content_type)
        if record.content_type in ContentType._value2member_map_
        else ContentType.DOCUMENT,
        parser=record.parser or "",
        parse_status=record.parse_status,
        message=record.message,
        metadata={
            "page_count": record.page_count,
            "sheet_count": record.sheet_count,
            "block_count": record.block_count,
            "restored_from_db": True,
        },
        document=document,
        workbook=workbook,
        uncovered=list(record.uncovered or []),
    )
    _CACHE[file_id] = result
    return result


def _load_document(session: Session, record: FileRecord) -> Document | None:
    rows = (
        session.query(DocumentBlock)
        .filter_by(file_id=record.file_id)
        .order_by(DocumentBlock.order)
        .all()
    )
    if not rows:
        return None

    tables = {
        table.block_id: table
        for table in session.query(DocumentTable)
        .filter_by(file_id=record.file_id)
        .all()
    }

    blocks: list[Block] = []
    for row in rows:
        table_data = None
        stored = tables.get(row.block_id)
        if stored is not None:
            table_data = TableData(
                table_id=stored.table_id,
                rows=[
                    TableRow.model_validate(item)
                    for item in (stored.data or {}).get("rows", [])
                ],
                layout=list((stored.data or {}).get("layout", [])),
                source_position=SourcePosition(**(stored.source_position or {})),
            )
        blocks.append(
            Block(
                block_id=row.block_id,
                type=BlockType(row.type),
                order=row.order,
                section_id=row.section_id,
                section_title=row.section_title,
                level=row.level,
                original_text=row.original_text or "",
                normalized_text=row.normalized_text or "",
                source_position=SourcePosition(**(row.source_position or {})),
                table=table_data,
                metadata=row.metadata_json or {},
            )
        )

    return Document(
        document_id=f"doc_{record.file_id}",
        file_id=record.file_id,
        blocks=blocks,
        metadata={"restored_from_db": True},
    )


def _load_workbook(session: Session, record: FileRecord) -> Workbook | None:
    workbook_row = (
        session.query(WorkbookRecord).filter_by(file_id=record.file_id).one_or_none()
    )
    if workbook_row is None:
        return None

    sheet_rows = (
        session.query(WorkbookSheet)
        .filter_by(file_id=record.file_id)
        .order_by(WorkbookSheet.order)
        .all()
    )
    cells = (
        session.query(WorkbookCell)
        .filter_by(file_id=record.file_id)
        .order_by(WorkbookCell.row_index, WorkbookCell.column_index)
        .all()
    )

    cells_by_sheet: dict[str, dict[int, list[Cell]]] = {}
    for cell in cells:
        cells_by_sheet.setdefault(cell.sheet_id, {}).setdefault(
            cell.row_index, []
        ).append(
            Cell(
                cell_id=cell.cell_id,
                coordinate=cell.coordinate or "",
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

    sheets: list[Sheet] = []
    for sheet_row in sheet_rows:
        grouped = cells_by_sheet.get(sheet_row.sheet_id, {})
        rows = [
            SheetRow(
                row_id=f"{sheet_row.sheet_id}_r{index}",
                index=index,
                cells=sorted(items, key=lambda item: item.column_index),
            )
            for index, items in sorted(grouped.items())
        ]
        sheets.append(
            Sheet(
                sheet_id=sheet_row.sheet_id,
                name=sheet_row.name,
                order=sheet_row.order,
                rows=rows,
                column_count=sheet_row.column_count,
                hidden=sheet_row.hidden,
            )
        )

    return Workbook(
        workbook_id=workbook_row.workbook_id,
        file_id=record.file_id,
        sheets=sheets,
        metadata={"restored_from_db": True},
    )
