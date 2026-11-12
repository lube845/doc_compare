"""PDF 比对 · 表格（改造计划 阶段 5）.

The tables of a PDF are not part of the character stream: they are
compared on their own, row by row and cell by cell.

The tables of the two documents are paired first.  A pair is only accepted
when the tables are similar enough, otherwise a table is reported as
completely new or completely removed.
"""

from __future__ import annotations

from ..domain.models import Block, Document, TableData
from ..engines.table_diff import diff_tables
from ..schemas import ChangeEvent, ChangeLocation, ChangeSide, CompareConfig
from .pdf_align import text_ratio

#: the smallest similarity two tables may have to be the same table
TABLE_MIN_SIM = 0.4


def tables_of(document: Document | None) -> list[Block]:
    """The table blocks of a document, in the order of the document."""
    if document is None:
        return []
    return [block for block in document.blocks if block.table is not None]


def table_text(table: TableData) -> str:
    """The whole text of a table."""
    return "\n".join(
        "\t".join(cell.original_text for cell in row.cells) for row in table.rows
    )


def header_text(table: TableData) -> str:
    """The text of the first row of a table."""
    if not table.rows:
        return ""
    return "\t".join(cell.original_text for cell in table.rows[0].cells)


def table_similarity(table_a: TableData, table_b: TableData) -> float:
    """How similar two tables are (the header counts more than the rest)."""
    header = text_ratio(header_text(table_a), header_text(table_b))
    body = text_ratio(table_text(table_a), table_text(table_b))
    return round(0.6 * header + 0.4 * body, 4)


def pair_tables(
    tables_a: list[TableData],
    tables_b: list[TableData],
) -> list[tuple[int, int]]:
    """Pair the tables of the two documents, keeping their order.

    The pairing is a dynamic programming over the two lists: a pair costs
    the difference of the similarity, an unpaired table costs one.  Two
    tables are only paired when they are similar enough.
    """
    count_a = len(tables_a)
    count_b = len(tables_b)
    if not count_a or not count_b:
        return []

    infinity = float("inf")
    cost = [[infinity] * (count_b + 1) for _ in range(count_a + 1)]
    step: list[list[tuple | None]] = [
        [None] * (count_b + 1) for _ in range(count_a + 1)
    ]
    cost[count_a][count_b] = 0.0

    for i in range(count_a, -1, -1):
        for j in range(count_b, -1, -1):
            if i == count_a and j == count_b:
                continue
            best = infinity
            choice: tuple | None = None

            if i < count_a:
                value = cost[i + 1][j] + 1.0
                if value < best:
                    best, choice = value, ("skip_a",)
            if j < count_b:
                value = cost[i][j + 1] + 1.0
                if value < best:
                    best, choice = value, ("skip_b",)
            if i < count_a and j < count_b:
                similarity = table_similarity(tables_a[i], tables_b[j])
                if similarity >= TABLE_MIN_SIM:
                    value = cost[i + 1][j + 1] + (1.0 - similarity)
                    if value < best:
                        best, choice = value, ("pair",)

            cost[i][j] = best
            step[i][j] = choice

    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < count_a or j < count_b:
        choice = step[i][j]
        if choice is None:
            break
        if choice[0] == "pair":
            pairs.append((i, j))
            i += 1
            j += 1
        elif choice[0] == "skip_a":
            i += 1
        else:
            j += 1
    return pairs


def compare_tables(
    document_a: Document | None,
    document_b: Document | None,
    config: CompareConfig,
    prefix: str = "change",
) -> tuple[list[ChangeEvent], list[str]]:
    """Compare the tables of two documents."""
    blocks_a = tables_of(document_a)
    blocks_b = tables_of(document_b)
    if not blocks_a and not blocks_b:
        return [], []

    tables_a = [block.table for block in blocks_a]
    tables_b = [block.table for block in blocks_b]
    pairs = pair_tables(tables_a, tables_b)
    paired_a = {index for index, _ in pairs}
    paired_b = {index for _, index in pairs}

    changes: list[ChangeEvent] = []
    notices: list[str] = []

    for order, (index_a, index_b) in enumerate(pairs):
        block_a = blocks_a[index_a]
        block_b = blocks_b[index_b]
        table_changes, table_notices = diff_tables(
            tables_a[index_a], tables_b[index_b], config, prefix=f"{prefix}_t{order}"
        )
        _locate(table_changes, block_a, block_b)
        changes.extend(table_changes)
        notices.extend(table_notices)

    counter = 0
    for index, block in enumerate(blocks_a):
        if index in paired_a:
            continue
        counter += 1
        changes.append(
            ChangeEvent(
                change_id=f"{prefix}_t_del{counter}",
                primary_type="deleted",
                category="table",
                document_a=ChangeSide(
                    node_id=block.block_id,
                    text=table_text(block.table),
                    location=ChangeLocation(
                        page=block.source_position.page,
                        block_id=block.block_id,
                        extra=_table_extra(block),
                    ),
                ),
                summary="整个表格被删除",
                details={"table_id_a": block.table.table_id},
                order=block.order,
            )
        )
    for index, block in enumerate(blocks_b):
        if index in paired_b:
            continue
        counter += 1
        changes.append(
            ChangeEvent(
                change_id=f"{prefix}_t_add{counter}",
                primary_type="added",
                category="table",
                document_b=ChangeSide(
                    node_id=block.block_id,
                    text=table_text(block.table),
                    location=ChangeLocation(
                        page=block.source_position.page,
                        block_id=block.block_id,
                        extra=_table_extra(block),
                    ),
                ),
                summary="新增整个表格",
                details={"table_id_b": block.table.table_id},
                order=block.order,
            )
        )

    return changes, notices


def _table_extra(block: Block) -> dict:
    """Where the table stands on the page.

    The cells of a table have no coordinates of their own (the table is kept
    out of the character stream), so a difference inside a table is marked
    by the box of the whole table in the PDF viewer.
    """
    bbox = list(block.source_position.bbox or [])
    extra: dict = {"table_id": block.table.table_id}
    if len(bbox) == 4 and block.source_position.page:
        extra["rects"] = [bbox]
        extra["rect_pages"] = [block.source_position.page]
    return extra


def _locate(
    changes: list[ChangeEvent],
    block_a: Block,
    block_b: Block,
) -> None:
    """Give every change of a table pair the box of its table."""
    for change in changes:
        for side, block in (("document_a", block_a), ("document_b", block_b)):
            data = getattr(change, side, None)
            if data is None or block is None:
                continue
            table_id = (data.location.extra or {}).get("table_id")
            if table_id != block.table.table_id:
                continue
            data.location.page = block.source_position.page
            extra = dict(data.location.extra or {})
            bbox = list(block.source_position.bbox or [])
            if len(bbox) == 4:
                extra["rects"] = [bbox]
                extra["rect_pages"] = [block.source_position.page]
            data.location.extra = extra
