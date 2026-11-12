"""Diff for tables embedded in documents (PRD 6.4)."""

from __future__ import annotations

from ..domain.models import TableData
from ..domain.normalize import normalise
from ..schemas import ChangeEvent, ChangeLocation, ChangeSide, CompareConfig
from .image_diff import compare_images
from .rowmatch import detect_key_columns, match_rows


def _cell_text(cell, config: CompareConfig) -> str:
    """The text of a cell, normalised with the rules of the comparison."""
    if cell is None:
        return ""
    return normalise(cell.original_text or cell.value or "", config)


def diff_tables(
    table_a: TableData,
    table_b: TableData,
    config: CompareConfig,
    prefix: str = "change",
) -> tuple[list[ChangeEvent], list[str]]:
    """Compare two document tables cell by cell."""
    changes: list[ChangeEvent] = []
    notices: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}_{counter:03d}"

    rows_a = [row.cells for row in table_a.rows]
    rows_b = [row.cells for row in table_b.rows]
    # The parser normalised the cells with the default rules; the rules of
    # this comparison decide what counts as equal.
    texts_a = [[_cell_text(cell, config) for cell in row] for row in rows_a]
    texts_b = [[_cell_text(cell, config) for cell in row] for row in rows_b]

    width_a = max((len(row) for row in rows_a), default=0)
    width_b = max((len(row) for row in rows_b), default=0)

    key = detect_key_columns(texts_a, texts_b)
    matches = match_rows(texts_a, texts_b, key, key, config.row_match_threshold)

    for match in matches:
        if match.a_index is not None and match.b_index is not None:
            row_a = rows_a[match.a_index]
            row_b = rows_b[match.b_index]
            for column in range(max(len(row_a), len(row_b))):
                cell_a = row_a[column] if column < len(row_a) else None
                cell_b = row_b[column] if column < len(row_b) else None
                text_a = texts_a[match.a_index][column] if column < len(texts_a[match.a_index]) else ""
                text_b = texts_b[match.b_index][column] if column < len(texts_b[match.b_index]) else ""
                if cell_a is not None and cell_b is not None and cell_a.merged and cell_b.merged:
                    # Both sides repeat the same vertically merged cell in
                    # this row; the comparison of the origin row above has
                    # already reported the change.
                    continue
                image_change = compare_images(
                    cell_a.images if cell_a else None,
                    cell_b.images if cell_b else None,
                )
                if text_a == text_b:
                    if image_change is not None:
                        changes.append(
                            _image_change(
                                next_id(),
                                image_change,
                                table_a,
                                table_b,
                                match.a_index,
                                match.b_index,
                                column,
                            )
                        )
                    continue
                changes.append(
                    ChangeEvent(
                        change_id=next_id(),
                        primary_type="modified",
                        category="table_cell",
                        document_a=ChangeSide(
                            node_id=cell_a.cell_id if cell_a else None,
                            text=cell_a.original_text if cell_a else "",
                            location=ChangeLocation(
                                block_id=table_a.table_id,
                                row_index=match.a_index,
                                column_index=column,
                                extra={"table_id": table_a.table_id},
                            ),
                        ),
                        document_b=ChangeSide(
                            node_id=cell_b.cell_id if cell_b else None,
                            text=cell_b.original_text if cell_b else "",
                            location=ChangeLocation(
                                block_id=table_b.table_id,
                                row_index=match.b_index,
                                column_index=column,
                                extra={"table_id": table_b.table_id},
                            ),
                        ),
                        summary=(
                            f"表格单元格变化（第 {match.a_index + 1} 行第 {column + 1} 列）："
                            f"{(cell_a.original_text if cell_a else '').strip()} → "
                            f"{(cell_b.original_text if cell_b else '').strip()}"
                        ),
                        details={"table_id_a": table_a.table_id, "table_id_b": table_b.table_id},
                        order=match.b_index or 0,
                    )
                )
                if image_change is not None:
                    changes.append(
                        _image_change(
                            next_id(),
                            image_change,
                            table_a,
                            table_b,
                            match.a_index,
                            match.b_index,
                            column,
                        )
                    )
        elif match.b_index is not None:
            row = rows_b[match.b_index]
            changes.append(
                ChangeEvent(
                    change_id=next_id(),
                    primary_type="added",
                    category="table_row",
                    document_b=ChangeSide(
                        node_id=row[0].cell_id if row else None,
                        text=" | ".join(cell.original_text for cell in row),
                        location=ChangeLocation(
                            block_id=table_b.table_id,
                            row_index=match.b_index,
                            extra={"table_id": table_b.table_id},
                        ),
                    ),
                    summary=f"表格新增第 {match.b_index + 1} 行",
                    order=match.b_index or 0,
                )
            )
        elif match.a_index is not None:
            row = rows_a[match.a_index]
            changes.append(
                ChangeEvent(
                    change_id=next_id(),
                    primary_type="deleted",
                    category="table_row",
                    document_a=ChangeSide(
                        node_id=row[0].cell_id if row else None,
                        text=" | ".join(cell.original_text for cell in row),
                        location=ChangeLocation(
                            block_id=table_a.table_id,
                            row_index=match.a_index,
                            extra={"table_id": table_a.table_id},
                        ),
                    ),
                    summary=f"表格删除第 {match.a_index + 1} 行",
                    order=match.a_index or 0,
                )
            )

    if width_a != width_b:
        notices.append(
            f"表格列数不一致（A {width_a} 列 / B {width_b} 列），列级差异未逐一列出"
        )
    return changes, notices


def _image_change(
    change_id: str,
    image_change: dict,
    table_a: TableData,
    table_b: TableData,
    row_a: int,
    row_b: int,
    column: int,
) -> ChangeEvent:
    """A change event for the pictures inside a matched table cell."""
    cell_a = table_a.rows[row_a].cell(column) if row_a is not None else None
    cell_b = table_b.rows[row_b].cell(column) if row_b is not None else None
    return ChangeEvent(
        change_id=change_id,
        primary_type=image_change["type"],
        category="table_image",
        document_a=ChangeSide(
            node_id=cell_a.cell_id if cell_a else None,
            text=cell_a.original_text if cell_a else "",
            location=ChangeLocation(
                block_id=table_a.table_id,
                row_index=row_a,
                column_index=column,
                extra={"table_id": table_a.table_id},
            ),
        ),
        document_b=ChangeSide(
            node_id=cell_b.cell_id if cell_b else None,
            text=cell_b.original_text if cell_b else "",
            location=ChangeLocation(
                block_id=table_b.table_id,
                row_index=row_b,
                column_index=column,
                extra={"table_id": table_b.table_id},
            ),
        ),
        summary=image_change["summary"],
        details={
            "match_type": "image",
            "table_id_a": table_a.table_id,
            "table_id_b": table_b.table_id,
            **image_change["details"],
        },
        order=row_b or 0,
    )
