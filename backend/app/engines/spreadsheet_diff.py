"""Spreadsheet diff engine (PRD chapter 7).

Pipeline:

    Sheet alignment -> column alignment -> row alignment -> cell diff

Sheets are matched by name first, then by content fingerprint, then by
content similarity - never by position alone (PRD 7.2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..domain.models import Cell, Sheet, Workbook
from ..domain.normalize import normalise
from ..schemas import ChangeEvent, ChangeLocation, ChangeSide, CompareConfig
from . import similarity
from .order import moved_indices
from .rowmatch import KeySpec, detect_key_columns, match_rows


@dataclass
class SheetMatch:
    a_index: int | None = None
    b_index: int | None = None
    match_type: str = "name"  # name | fingerprint | similar | added | deleted
    similarity: float = 0.0
    moved: bool = False
    details: dict = field(default_factory=dict)


@dataclass
class ColumnMatch:
    a_index: int | None = None
    b_index: int | None = None
    match_type: str = "header"  # header | fingerprint | position | added | deleted
    header: str = ""


# --------------------------------------------------------------------------
# Sheet matching
# --------------------------------------------------------------------------


def _cell_text(cell: Cell | None, config: CompareConfig) -> str:
    """The text of a cell, normalised with the rules of the comparison.

    The parser normalises with the default rules; the rules of the current
    comparison decide what counts as equal.
    """
    if cell is None:
        return ""
    return normalise(cell.value or "", config)


def sheet_fingerprint(sheet: Sheet, config: CompareConfig) -> str:
    hasher = hashlib.sha256()
    for row in sheet.rows:
        for cell in row.cells:
            hasher.update(_cell_text(cell, config).encode("utf-8"))
            hasher.update(b"\x00")
    return hasher.hexdigest()


def sheet_text(sheet: Sheet, config: CompareConfig) -> str:
    parts = []
    for row in sheet.rows:
        parts.append(" ".join(_cell_text(cell, config) for cell in row.cells))
    return "\n".join(parts)


def match_sheets(
    workbook_a: Workbook, workbook_b: Workbook, config: CompareConfig
) -> list[SheetMatch]:
    matches: list[SheetMatch] = []
    matched_a: dict[int, int] = {}
    matched_b: dict[int, int] = {}

    sheets_a = workbook_a.sheets
    sheets_b = workbook_b.sheets

    # 1. exact name
    by_name = {sheet.name: index for index, sheet in enumerate(sheets_b)}
    for a_index, sheet in enumerate(sheets_a):
        b_index = by_name.get(sheet.name)
        if b_index is not None and b_index not in matched_b:
            matched_a[a_index] = b_index
            matched_b[b_index] = a_index
            matches.append(SheetMatch(a_index, b_index, "name", 1.0))

    # 2. content fingerprint
    fingerprint_b: dict[str, int] = {}
    for b_index, sheet in enumerate(sheets_b):
        if b_index in matched_b:
            continue
        fingerprint_b.setdefault(sheet_fingerprint(sheet, config), b_index)
    for a_index, sheet in enumerate(sheets_a):
        if a_index in matched_a:
            continue
        b_index = fingerprint_b.get(sheet_fingerprint(sheet, config))
        if b_index is None or b_index in matched_b:
            continue
        matched_a[a_index] = b_index
        matched_b[b_index] = a_index
        matches.append(SheetMatch(a_index, b_index, "fingerprint", 1.0))

    # 3. similarity
    free_a = [i for i in range(len(sheets_a)) if i not in matched_a]
    free_b = [i for i in range(len(sheets_b)) if i not in matched_b]
    scored: list[tuple[float, int, int]] = []
    for a_index in free_a:
        text_a = sheet_text(sheets_a[a_index], config)
        if not text_a.strip():
            continue
        for b_index in free_b:
            text_b = sheet_text(sheets_b[b_index], config)
            if not text_b.strip():
                continue
            score = similarity.combined_similarity(text_a, text_b)
            if score >= config.medium_similarity_threshold:
                scored.append((score, a_index, b_index))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    for score, a_index, b_index in scored:
        if a_index in matched_a or b_index in matched_b:
            continue
        matched_a[a_index] = b_index
        matched_b[b_index] = a_index
        matches.append(SheetMatch(a_index, b_index, "similar", score))

    for a_index in range(len(sheets_a)):
        if a_index not in matched_a:
            matches.append(SheetMatch(a_index, None, "deleted", 0.0))
    for b_index in range(len(sheets_b)):
        if b_index not in matched_b:
            matches.append(SheetMatch(None, b_index, "added", 0.0))

    _detect_sheet_moves(matches, sheets_a, sheets_b)

    matches.sort(key=lambda m: m.b_index if m.b_index is not None else (m.a_index or 0))
    return matches


def _detect_sheet_moves(
    matches: list[SheetMatch], sheets_a: list[Sheet], sheets_b: list[Sheet]
) -> None:
    """Flag sheets that only changed position (PRD 7.2 example).

    The sheets that kept their relative order form the longest increasing
    subsequence of their B positions; the remaining ones moved.
    """
    pairs = [m for m in matches if m.a_index is not None and m.b_index is not None]
    pairs.sort(key=lambda m: m.a_index)  # type: ignore[arg-type]
    if len(pairs) < 2:
        return

    source = [m.a_index for m in pairs]
    target = [m.b_index for m in pairs]
    assert all(position is not None for position in source)
    assert all(position is not None for position in target)

    for index in moved_indices(source, target):
        pairs[index].moved = True


# --------------------------------------------------------------------------
# Column matching
# --------------------------------------------------------------------------


def match_columns(
    sheet_a: Sheet, sheet_b: Sheet, config: CompareConfig
) -> list[ColumnMatch]:
    headers_a = _headers(sheet_a, config)
    headers_b = _headers(sheet_b, config)
    count_a = len(headers_a)
    count_b = len(headers_b)

    matches: list[ColumnMatch] = []
    matched_a: dict[int, int] = {}
    matched_b: dict[int, int] = {}

    # 1. exact header
    by_header = {header: index for index, header in enumerate(headers_b)}
    for a_index, header in enumerate(headers_a):
        b_index = by_header.get(header)
        if b_index is not None and b_index not in matched_b:
            matched_a[a_index] = b_index
            matched_b[b_index] = a_index
            matches.append(ColumnMatch(a_index, b_index, "header", header))

    # 2. normalised header
    normalised_b = {normalise(header): index for index, header in enumerate(headers_b)}
    for a_index, header in enumerate(headers_a):
        if a_index in matched_a:
            continue
        b_index = normalised_b.get(normalise(header))
        if b_index is None or b_index in matched_b:
            continue
        matched_a[a_index] = b_index
        matched_b[b_index] = a_index
        matches.append(ColumnMatch(a_index, b_index, "header", header))

    # 3. content fingerprint
    fingerprint_b = {}
    for b_index in range(count_b):
        if b_index in matched_b:
            continue
        fingerprint_b.setdefault(_column_fingerprint(sheet_b, b_index, config), b_index)
    for a_index in range(count_a):
        if a_index in matched_a:
            continue
        b_index = fingerprint_b.get(_column_fingerprint(sheet_a, a_index, config))
        if b_index is None or b_index in matched_b:
            continue
        matched_a[a_index] = b_index
        matched_b[b_index] = a_index
        matches.append(
            ColumnMatch(a_index, b_index, "fingerprint", headers_a[a_index])
        )

    # 4. remaining columns by position - only when both sides really have
    #    a column at that position.
    for a_index in range(count_a):
        if a_index in matched_a:
            continue
        if a_index >= count_b or a_index in matched_b:
            continue
        matched_a[a_index] = a_index
        matched_b[a_index] = a_index
        matches.append(ColumnMatch(a_index, a_index, "position", headers_a[a_index]))

    for a_index in range(count_a):
        if a_index not in matched_a:
            matches.append(ColumnMatch(a_index, None, "deleted", headers_a[a_index]))
    for b_index in range(count_b):
        if b_index not in matched_b:
            matches.append(ColumnMatch(None, b_index, "added", headers_b[b_index]))

    matches.sort(key=lambda m: m.b_index if m.b_index is not None else (m.a_index or 0))
    return matches


# How many rows to inspect when looking for the header row.  A merged title
# row ("XX 名单") above the real header is a very common layout in Chinese
# reports, so the header is not always row 0.
_HEADER_SCAN_ROWS = 8


def _header_row_index(sheet: Sheet, config: CompareConfig) -> int:
    """Return the row that holds the column headers.

    The first row is only assumed to be the header when no later row in the
    first few rows has more non-empty cells.  A merged title row has a single
    filled cell, while the real header row fills most columns, so this picks
    the header even when it sits below a title.  Ties keep the earlier row, so
    a normal header row at index 0 still wins against equally wide data rows.
    """
    if not sheet.rows:
        return 0
    best_index = 0
    best_count = -1
    for index in range(min(len(sheet.rows), _HEADER_SCAN_ROWS)):
        count = sum(
            1
            for cell in sheet.rows[index].cells
            if _cell_text(cell, config).strip()
        )
        if count > best_count:
            best_count = count
            best_index = index
    return best_index


def _headers(sheet: Sheet, config: CompareConfig) -> list[str]:
    if not sheet.rows:
        return []
    header = sheet.rows[_header_row_index(sheet, config)]
    width = sheet.column_count or len(header.cells)
    headers: list[str] = []
    for column in range(width):
        cell = header.cell(column)
        headers.append(_cell_text(cell, config))
    return headers


def _column_fingerprint(sheet: Sheet, column: int, config: CompareConfig) -> str:
    hasher = hashlib.sha256()
    for row in sheet.rows:
        cell = row.cell(column)
        hasher.update(_cell_text(cell, config).encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


# --------------------------------------------------------------------------
# Cell comparison
# --------------------------------------------------------------------------


def _cell_values_equal(cell_a: Cell | None, cell_b: Cell | None, config: CompareConfig) -> bool:
    value_a = _normalised_cell_value(cell_a, config)
    value_b = _normalised_cell_value(cell_b, config)
    if config.ignore_blank_cells:
        # Blank cells are treated as equal to anything (PRD 5.1).  A cell with
        # a formula or a cached result is not blank.
        if _is_blank_cell(cell_a) or _is_blank_cell(cell_b):
            return True
    if value_a != value_b:
        return False
    if config.compare_formula and not config.ignore_formula:
        formula_a = (cell_a.formula or "") if cell_a else ""
        formula_b = (cell_b.formula or "") if cell_b else ""
        if formula_a.strip() != formula_b.strip():
            return False
    if config.compare_display_value:
        # Display values are only compared when both sides actually carry a
        # cached value.  Some files (XLS, ET fallback) do not cache them, and
        # treating a missing cache as a difference would report a change
        # where there is none.
        display_a = cell_a.display_value if cell_a else None
        display_b = cell_b.display_value if cell_b else None
        if (
            display_a is not None
            and display_b is not None
            and normalise(display_a, config).strip()
            != normalise(display_b, config).strip()
        ):
            return False
    if config.compare_cell_format:
        format_a = (cell_a.number_format or "") if cell_a else ""
        format_b = (cell_b.number_format or "") if cell_b else ""
        if format_a != format_b:
            return False
    return True


def _normalised_cell_value(cell: Cell | None, config: CompareConfig) -> str:
    if cell is None:
        return ""
    return _cell_text(cell, config)


def _is_blank_cell(cell: Cell | None) -> bool:
    """True when the cell carries nothing at all.

    A cell with a formula or with a cached result is *not* blank: it has a
    value the reader sees, so it must take part in the comparison.
    """
    if cell is None:
        return True
    if (cell.value or "").strip():
        return False
    if (cell.formula or "").strip():
        return False
    if (cell.display_value or "").strip():
        return False
    return True


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def diff_workbooks(
    workbook_a: Workbook,
    workbook_b: Workbook,
    config: CompareConfig,
) -> tuple[list[ChangeEvent], list[str]]:
    """Compare two workbooks and return the change events."""
    changes: list[ChangeEvent] = []
    notices: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"change_{counter:04d}"

    sheet_matches = match_sheets(workbook_a, workbook_b, config)

    for sheet_match in sheet_matches:
        sheet_a = (
            workbook_a.sheets[sheet_match.a_index]
            if sheet_match.a_index is not None
            else None
        )
        sheet_b = (
            workbook_b.sheets[sheet_match.b_index]
            if sheet_match.b_index is not None
            else None
        )

        if sheet_a is None and sheet_b is not None:
            changes.append(
                ChangeEvent(
                    change_id=next_id(),
                    primary_type="added",
                    category="sheet",
                    document_b=ChangeSide(
                        text=f"新增工作表：{sheet_b.name}",
                        location=ChangeLocation(
                            sheet_id=sheet_b.sheet_id,
                            sheet_name=sheet_b.name,
                        ),
                    ),
                    summary=f"新增工作表「{sheet_b.name}」（{sheet_b.row_count} 行）",
                    order=sheet_b.order,
                )
            )
            continue
        if sheet_a is not None and sheet_b is None:
            changes.append(
                ChangeEvent(
                    change_id=next_id(),
                    primary_type="deleted",
                    category="sheet",
                    document_a=ChangeSide(
                        text=f"删除工作表：{sheet_a.name}",
                        location=ChangeLocation(
                            sheet_id=sheet_a.sheet_id,
                            sheet_name=sheet_a.name,
                        ),
                    ),
                    summary=f"删除工作表「{sheet_a.name}」（{sheet_a.row_count} 行）",
                    order=sheet_a.order,
                )
            )
            continue
        if sheet_a is None or sheet_b is None:
            continue

        if sheet_match.moved and config.compare_sheet_order:
            changes.append(
                ChangeEvent(
                    change_id=next_id(),
                    primary_type="moved",
                    category="sheet",
                    document_a=ChangeSide(
                        text=sheet_a.name,
                        location=ChangeLocation(
                            sheet_id=sheet_a.sheet_id, sheet_name=sheet_a.name
                        ),
                    ),
                    document_b=ChangeSide(
                        text=sheet_b.name,
                        location=ChangeLocation(
                            sheet_id=sheet_b.sheet_id, sheet_name=sheet_b.name
                        ),
                    ),
                    summary=f"工作表「{sheet_b.name}」位置发生变化",
                    details={
                        "from_order": sheet_a.order + 1,
                        "to_order": sheet_b.order + 1,
                    },
                    order=sheet_b.order,
                )
            )

        changes.extend(
            _diff_sheets(sheet_a, sheet_b, config, next_id, notices)
        )

    if not changes:
        notices.append("两份表格内容完全一致，未发现差异")

    return changes, notices


def _diff_sheets(
    sheet_a: Sheet,
    sheet_b: Sheet,
    config: CompareConfig,
    next_id,
    notices: list[str],
) -> list[ChangeEvent]:
    changes: list[ChangeEvent] = []

    column_matches = match_columns(sheet_a, sheet_b, config)

    added_columns = [m for m in column_matches if m.match_type == "added"]
    deleted_columns = [m for m in column_matches if m.match_type == "deleted"]
    for column in added_columns:
        assert column.b_index is not None
        header = column.header or f"第 {column.b_index + 1} 列"
        changes.append(
            ChangeEvent(
                change_id=next_id(),
                primary_type="added",
                category="column",
                document_b=ChangeSide(
                    text=header,
                    location=ChangeLocation(
                        sheet_id=sheet_b.sheet_id,
                        sheet_name=sheet_b.name,
                        column_index=column.b_index,
                        column_key=header,
                    ),
                ),
                summary=f"工作表「{sheet_b.name}」新增列「{header}」",
                order=sheet_b.order,
            )
        )
    for column in deleted_columns:
        assert column.a_index is not None
        header = column.header or f"第 {column.a_index + 1} 列"
        changes.append(
            ChangeEvent(
                change_id=next_id(),
                primary_type="deleted",
                category="column",
                document_a=ChangeSide(
                    text=header,
                    location=ChangeLocation(
                        sheet_id=sheet_a.sheet_id,
                        sheet_name=sheet_a.name,
                        column_index=column.a_index,
                        column_key=header,
                    ),
                ),
                summary=f"工作表「{sheet_a.name}」删除列「{header}」",
                order=sheet_a.order,
            )
        )

    # Row matching operates on the data rows (the header row is excluded).
    body_a = _body_rows(sheet_a, config)
    body_b = _body_rows(sheet_b, config)
    texts_a = [[_cell_text(cell, config) for cell in row.cells] for row in body_a]
    texts_b = [[_cell_text(cell, config) for cell in row.cells] for row in body_b]

    key_a = key_b = detect_key_columns(texts_a, texts_b)
    if key_a is None and config.primary_key_columns:
        user_keys = _key_from_names(sheet_a, sheet_b, config.primary_key_columns, config)
        if user_keys is not None:
            key_a, key_b = user_keys
    matches = match_rows(texts_a, texts_b, key_a, key_b, config.row_match_threshold)

    if config.compare_row_order:
        changes.extend(_row_order_changes(matches, body_a, body_b, sheet_a, sheet_b, next_id))

    for match in matches:
        if match.a_index is not None and match.b_index is not None:
            row_a = body_a[match.a_index]
            row_b = body_b[match.b_index]
            row_key = _row_key(row_a, row_b)
            for column_match in column_matches:
                if column_match.match_type in {"added", "deleted"}:
                    continue
                a_column = column_match.a_index
                b_column = column_match.b_index
                assert a_column is not None and b_column is not None
                cell_a = row_a.cell(a_column)
                cell_b = row_b.cell(b_column)
                if _cell_values_equal(cell_a, cell_b, config):
                    continue
                changes.extend(
                    _cell_changes(
                        next_id,
                        sheet_a,
                        sheet_b,
                        cell_a,
                        cell_b,
                        row_a.index + 1,
                        row_b.index + 1,
                        a_column,
                        b_column,
                        row_key,
                        config,
                    )
                )
        elif match.b_index is not None:
            row = body_b[match.b_index]
            changes.append(
                ChangeEvent(
                    change_id=next_id(),
                    primary_type="added",
                    category="row",
                    document_b=ChangeSide(
                        node_id=row.cells[0].cell_id if row.cells else None,
                        text=" | ".join((cell.value or "") for cell in row.cells),
                        location=ChangeLocation(
                            sheet_id=sheet_b.sheet_id,
                            sheet_name=sheet_b.name,
                            row_index=row.index + 1,
                            row_key=_row_key_text(row),
                        ),
                    ),
                    summary=f"工作表「{sheet_b.name}」新增第 {row.index + 1} 行",
                    order=sheet_b.order,
                )
            )
        elif match.a_index is not None:
            row = body_a[match.a_index]
            changes.append(
                ChangeEvent(
                    change_id=next_id(),
                    primary_type="deleted",
                    category="row",
                    document_a=ChangeSide(
                        node_id=row.cells[0].cell_id if row.cells else None,
                        text=" | ".join((cell.value or "") for cell in row.cells),
                        location=ChangeLocation(
                            sheet_id=sheet_a.sheet_id,
                            sheet_name=sheet_a.name,
                            row_index=row.index + 1,
                            row_key=_row_key_text(row),
                        ),
                    ),
                    summary=f"工作表「{sheet_a.name}」删除第 {row.index + 1} 行",
                    order=sheet_a.order,
                )
            )

    return changes


def _body_rows(sheet: Sheet, config: CompareConfig) -> list:
    """The rows of a sheet.

    The header row takes part in the comparison as well: it carries content
    the reader sees, and dropping it would hide a sheet whose first row
    changed.  It is still used to name the columns.
    """
    if not sheet.rows:
        return []
    rows = list(sheet.rows)
    if not config.ignore_blank_rows:
        return rows
    return [
        row
        for row in rows
        if any(
            _cell_text(cell, config).strip() or (cell.formula or "").strip()
            for cell in row.cells
        )
    ]


def _row_order_changes(
    matches,
    body_a: list,
    body_b: list,
    sheet_a: Sheet,
    sheet_b: Sheet,
    next_id,
) -> list[ChangeEvent]:
    """Report rows that only changed position (PRD 7.5 - Sheet/row move)."""
    pairs = [m for m in matches if m.a_index is not None and m.b_index is not None]
    if len(pairs) < 2:
        return []
    pairs.sort(key=lambda m: m.a_index)  # type: ignore[arg-type]
    source = [m.a_index for m in pairs]
    target = [m.b_index for m in pairs]

    changes: list[ChangeEvent] = []
    for index in moved_indices(source, target):
        match = pairs[index]
        assert match.a_index is not None and match.b_index is not None
        row_a = body_a[match.a_index]
        row_b = body_b[match.b_index]
        changes.append(
            ChangeEvent(
                change_id=next_id(),
                primary_type="moved",
                category="row",
                document_a=ChangeSide(
                    node_id=row_a.cells[0].cell_id if row_a.cells else None,
                    text=" | ".join((cell.value or "") for cell in row_a.cells),
                    location=ChangeLocation(
                        sheet_id=sheet_a.sheet_id,
                        sheet_name=sheet_a.name,
                        row_index=row_a.index + 1,
                    ),
                ),
                document_b=ChangeSide(
                    node_id=row_b.cells[0].cell_id if row_b.cells else None,
                    text=" | ".join((cell.value or "") for cell in row_b.cells),
                    location=ChangeLocation(
                        sheet_id=sheet_b.sheet_id,
                        sheet_name=sheet_b.name,
                        row_index=row_b.index + 1,
                    ),
                ),
                summary=(
                    f"工作表「{sheet_b.name}」第 {row_b.index + 1} 行位置发生变化"
                    f"（原第 {row_a.index + 1} 行）"
                ),
                order=sheet_b.order,
            )
        )
    return changes


def _cell_changes(
    next_id,
    sheet_a: Sheet,
    sheet_b: Sheet,
    cell_a: Cell | None,
    cell_b: Cell | None,
    row_a_index: int,
    row_b_index: int,
    column_a_index: int,
    column_b_index: int,
    row_key: str | None,
    config: CompareConfig,
) -> list[ChangeEvent]:
    """Build the change events for one unequal cell pair.

    A cell is only "unchanged" when *both* the formula and the evaluated
    result match.  A formula-only change and a result-only change are two
    different findings, so when both changed the pair yields two events
    instead of one combined summary.
    """
    formula_a = (cell_a.formula or "") if cell_a else ""
    formula_b = (cell_b.formula or "") if cell_b else ""
    formula_changed = (
        config.compare_formula
        and not config.ignore_formula
        and formula_a.strip() != formula_b.strip()
    )

    value_a = _normalised_cell_value(cell_a, config)
    value_b = _normalised_cell_value(cell_b, config)

    display_a = cell_a.display_value if cell_a else None
    display_b = cell_b.display_value if cell_b else None
    display_changed = (
        config.compare_display_value
        and display_a is not None
        and display_b is not None
        and normalise(display_a, config).strip() != normalise(display_b, config).strip()
    )

    # What the user actually sees in the grid: the cached result for a formula
    # cell, the literal value otherwise.  Comparing raw values here would turn
    # "literal 3" vs "formula =B+C showing 3" into a redundant 3 → 3 event even
    # though the displayed result is identical.
    result_a = _result_text(cell_a, config)
    result_b = _result_text(cell_b, config)
    result_changed = result_a.strip() != result_b.strip()

    format_a = (cell_a.number_format or "") if cell_a else ""
    format_b = (cell_b.number_format or "") if cell_b else ""
    format_changed = config.compare_cell_format and format_a != format_b

    changes: list[ChangeEvent] = []
    details = {
        "formula_a": formula_a or None,
        "formula_b": formula_b or None,
        "value_a": value_a or None,
        "value_b": value_b or None,
        "display_a": display_a,
        "display_b": display_b,
    }

    if formula_changed:
        changes.append(
            _cell_event(
                next_id(),
                sheet_a,
                sheet_b,
                cell_a,
                cell_b,
                row_a_index,
                row_b_index,
                column_a_index,
                column_b_index,
                row_key,
                category="formula",
                text_a=formula_a,
                text_b=formula_b,
                summary=(
                    f"工作表「{sheet_b.name}」第 {row_b_index} 行公式变化："
                    f"{formula_a or '（无）'} → {formula_b or '（无）'}"
                ),
                details={**details, "change_kind": "formula"},
                order=sheet_b.order,
            )
        )

    if result_changed or format_changed:
        what = "取值结果" if (formula_a or formula_b) else "单元格值"
        if not result_changed:
            what = "单元格格式"
        changes.append(
            _cell_event(
                next_id(),
                sheet_a,
                sheet_b,
                cell_a,
                cell_b,
                row_a_index,
                row_b_index,
                column_a_index,
                column_b_index,
                row_key,
                category="format" if what == "单元格格式" else "value",
                text_a=result_a or "",
                text_b=result_b or "",
                summary=(
                    f"工作表「{sheet_b.name}」第 {row_b_index} 行{what}变化："
                    f"{(result_a or '').strip()} → {(result_b or '').strip()}"
                ),
                details={**details, "change_kind": what},
                order=sheet_b.order,
            )
        )

    return changes


def _cell_event(
    change_id: str,
    sheet_a: Sheet,
    sheet_b: Sheet,
    cell_a: Cell | None,
    cell_b: Cell | None,
    row_a_index: int,
    row_b_index: int,
    column_a_index: int,
    column_b_index: int,
    row_key: str | None,
    category: str,
    text_a: str,
    text_b: str,
    summary: str,
    details: dict,
    order: int,
) -> ChangeEvent:
    return ChangeEvent(
        change_id=change_id,
        primary_type="modified",
        category=category,
        document_a=ChangeSide(
            node_id=cell_a.cell_id if cell_a else None,
            text=text_a,
            location=ChangeLocation(
                sheet_id=sheet_a.sheet_id,
                sheet_name=sheet_a.name,
                row_index=row_a_index,
                column_index=column_a_index,
                coordinate=cell_a.coordinate if cell_a else None,
                row_key=row_key,
            ),
        ),
        document_b=ChangeSide(
            node_id=cell_b.cell_id if cell_b else None,
            text=text_b,
            location=ChangeLocation(
                sheet_id=sheet_b.sheet_id,
                sheet_name=sheet_b.name,
                row_index=row_b_index,
                column_index=column_b_index,
                coordinate=cell_b.coordinate if cell_b else None,
                row_key=row_key,
            ),
        ),
        summary=summary,
        details=details,
        order=order,
    )


def _result_text(cell: Cell | None, config: CompareConfig) -> str:
    """The value a user sees: the cached result for formulas, the cell otherwise."""
    if cell is None:
        return ""
    if cell.formula:
        raw = cell.display_value if cell.display_value is not None else (cell.value or "")
    else:
        raw = cell.value or ""
    return normalise(raw, config)


def _row_key(row_a, row_b) -> str | None:
    for row in (row_a, row_b):
        key = _row_key_text(row)
        if key:
            return key
    return None


def _row_key_text(row) -> str:
    for cell in row.cells:
        value = (cell.value or "").strip()
        if value:
            return value
    return ""


def _key_from_names(
    sheet_a: Sheet, sheet_b: Sheet, names: list[str], config: CompareConfig
) -> tuple[KeySpec, KeySpec] | None:
    """The key columns the user named - one spec per side.

    The two sheets may have their columns in a different order, so the index
    of a key column is looked up on both sides; using the index of one side
    for the other would read the key from the wrong column.
    """
    headers_a = _headers(sheet_a, config)
    headers_b = _headers(sheet_b, config)
    columns_a: list[int] = []
    columns_b: list[int] = []
    labels: list[str] = []
    for name in names:
        index_a = _find_header(headers_a, name)
        index_b = _find_header(headers_b, name)
        if index_a is None or index_b is None:
            return None
        columns_a.append(index_a)
        columns_b.append(index_b)
        labels.append(name)
    if not columns_a:
        return None
    label = "+".join(labels)
    return (
        KeySpec(columns=tuple(columns_a), label=label),
        KeySpec(columns=tuple(columns_b), label=label),
    )


def _find_header(headers: list[str], name: str) -> int | None:
    for index, header in enumerate(headers):
        if header == name:
            return index
    for index, header in enumerate(headers):
        if normalise(header) == normalise(name):
            return index
    return None
