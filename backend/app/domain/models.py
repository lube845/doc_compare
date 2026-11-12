"""Unified data model shared by all parsers and diff engines.

The PRD (chapter 12.3) defines two models:

* ``Document``  - headings / paragraphs / lists / tables coming from Word-like formats
* ``Workbook``  - sheets / rows / cells coming from spreadsheet formats

Everything is a plain pydantic model so it can be serialised straight into the
database layer or handed to the engines without further conversion.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ContentType(str, Enum):
    """Content type of a parsed file (chapter 4.3)."""

    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"


class SourcePosition(BaseModel):
    """Where a piece of content lives inside the original file."""

    page: int | None = None
    line: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    bbox: list[float] | None = None
    sheet_name: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    coordinate: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TableCell(BaseModel):
    cell_id: str
    row_index: int
    column_index: int
    original_text: str = ""
    normalized_text: str = ""
    value: str | None = None
    formula: str | None = None
    data_type: str | None = None
    number_format: str | None = None
    #: The cell continues the vertically merged cell above it: the grid
    #: repeats the same physical cell in every row it spans.
    merged: bool = False
    #: The pictures inside the cell (hash, size, type) - they take part in the
    #: comparison like the text of the cell does.
    images: list[dict[str, Any]] = Field(default_factory=list)
    source_position: SourcePosition = Field(default_factory=SourcePosition)


class TableRow(BaseModel):
    row_id: str
    row_index: int
    cells: list[TableCell] = Field(default_factory=list)

    def cell(self, column_index: int) -> TableCell | None:
        for cell in self.cells:
            if cell.column_index == column_index:
                return cell
        return None


class TableData(BaseModel):
    """A table embedded inside a document."""

    table_id: str
    rows: list[TableRow] = Field(default_factory=list)
    #: Visual layout of the table: one list per row, one entry per cell.
    #: ``rows`` addresses the cells by *grid* position (Word's table grid,
    #: where a merged cell covers several columns), the layout describes what
    #: the reader actually sees - one entry per cell with its spans.
    layout: list[list[dict[str, Any]]] = Field(default_factory=list)
    source_position: SourcePosition = Field(default_factory=SourcePosition)
    degraded: bool = False
    note: str | None = None

    @property
    def column_count(self) -> int:
        if not self.rows:
            return 0
        return max(len(row.cells) for row in self.rows)

    def cell_text(self, row_index: int, column_index: int) -> str:
        for row in self.rows:
            if row.row_index == row_index:
                cell = row.cell(column_index)
                if cell is not None:
                    return cell.normalized_text
        return ""


class Block(BaseModel):
    """A single logical block of a document."""

    block_id: str
    type: BlockType
    order: int
    section_id: str | None = None
    section_title: str | None = None
    level: int | None = None
    original_text: str = ""
    normalized_text: str = ""
    list_marker: str | None = None
    source_position: SourcePosition = Field(default_factory=SourcePosition)
    table: TableData | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Section(BaseModel):
    section_id: str
    title: str | None = None
    level: int = 0
    order: int = 0


class Document(BaseModel):
    document_id: str
    file_id: str | None = None
    blocks: list[Block] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_tables(self) -> bool:
        return any(block.type == BlockType.TABLE for block in self.blocks)


class Cell(BaseModel):
    """A spreadsheet cell."""

    cell_id: str
    coordinate: str
    row_index: int
    column_index: int
    value: str | None = None
    normalized_value: str | None = None
    formula: str | None = None
    display_value: str | None = None
    data_type: str | None = None
    number_format: str | None = None
    style_id: str | None = None
    source_position: SourcePosition = Field(default_factory=SourcePosition)


class SheetRow(BaseModel):
    row_id: str
    index: int
    cells: list[Cell] = Field(default_factory=list)

    def cell(self, column_index: int) -> Cell | None:
        for cell in self.cells:
            if cell.column_index == column_index:
                return cell
        return None

    def values(self) -> list[str | None]:
        return [cell.value for cell in self.cells]


class Sheet(BaseModel):
    sheet_id: str
    name: str
    order: int
    rows: list[SheetRow] = Field(default_factory=list)
    column_count: int = 0
    hidden: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)


class Workbook(BaseModel):
    workbook_id: str
    file_id: str | None = None
    sheets: list[Sheet] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def sheet(self, name: str) -> Sheet | None:
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        return None


class ParseResult(BaseModel):
    """Uniform envelope returned by every parser."""

    file_id: str | None = None
    file_type: str = ""
    content_type: ContentType = ContentType.DOCUMENT
    parser: str = ""
    parse_status: str = "success"
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    document: Document | None = None
    workbook: Workbook | None = None
    uncovered: list[str] = Field(default_factory=list)

    #: The character level contract of the PDF parser (阶段 2).  It is kept
    #: out of the serialisation on purpose: the character stream is large
    #: and only the comparison needs it, so the field is an in-memory
    #: companion of the parse result.  ``None`` for every other format.
    parsed_pdf: Any = Field(default=None, exclude=True)
