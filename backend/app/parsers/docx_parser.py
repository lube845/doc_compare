"""DOCX parser built on ``python-docx`` + direct OOXML access."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

from ..domain.models import (
    Block,
    BlockType,
    ContentType,
    Document,
    ParseResult,
    Section,
    SourcePosition,
    TableCell,
    TableData,
    TableRow,
)
from ..domain.normalize import normalise
from ..schemas import CompareConfig
from .base import BaseParser, ProgressCallback

_HEADING_RE = re.compile(r"^(?:heading|标题)\s*(\d+)$", re.IGNORECASE)
_LIST_RE = re.compile(r"(?:list|列表)", re.IGNORECASE)
_HEADER_RE = re.compile(r"(?:header|footer|页眉|页脚)", re.IGNORECASE)

#: Text of a paragraph that only carries a picture.
IMAGE_PLACEHOLDER = "【图片】"

#: Legacy VML picture data (python-docx's ``qn`` does not know the ``v``
#: prefix, so the namespace is spelled out).
_VML_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _extract_images(part, element) -> list[dict]:
    """The images inside a paragraph or a table cell.

    Only the *hash* of the picture is kept - the bytes stay in the document,
    so nothing has to be written to disk.  The hash is what the comparison
    works with: the same picture has the same hash, a replaced one does not.
    """
    images: list[dict] = []
    seen: set[str] = set()

    rel_ids: list[str] = []
    for blip in element.iter(qn("a:blip")):
        # DrawingML: <a:blip r:embed="rId9"/>
        rel_id = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
        if rel_id:
            rel_ids.append(rel_id)
    for data in element.iter(_VML_IMAGEDATA):
        # Legacy VML: <v:imagedata r:id="rId9"/>
        rel_id = data.get(_R_ID)
        if rel_id:
            rel_ids.append(rel_id)

    for rel_id in rel_ids:
        if rel_id in seen:
            continue
        seen.add(rel_id)
        related = getattr(part, "related_parts", {}).get(rel_id)
        if related is None:
            continue  # a linked (external) picture
        try:
            blob = related.blob
        except Exception:  # noqa: BLE001 - a broken part must not kill the parse
            continue
        images.append(
            {
                "hash": hashlib.sha256(blob).hexdigest()[:16],
                "size": len(blob),
                "type": getattr(related, "content_type", "") or "",
            }
        )
    return images


def iter_block_items(parent):
    """Yield paragraphs and tables in true document order."""
    if isinstance(parent, DocxDocument):
        parent_elm = parent.element.body
    else:  # pragma: no cover - header/footer parts
        parent_elm = parent._element
    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            yield DocxParagraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield DocxTable(child, parent)


class DocxParser(BaseParser):
    file_type = "docx"
    display_name = "Microsoft Word (OOXML)"
    content_type = ContentType.DOCUMENT.value
    known_gaps = (
        "文本框内容",
        "批注",
        "修订记录",
        "图片内容识别（仅比对图片是否相同）",
    )

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        config = options or CompareConfig()
        document = docx.Document(str(file_path))

        blocks: list[Block] = []
        sections: list[Section] = []
        current_section: Section | None = None
        order = 0
        table_index = 0

        items = list(iter_block_items(document))
        for index, item in enumerate(items):
            if progress is not None and index % 20 == 0:
                progress(
                    10 + int(80 * index / max(1, len(items))), "parsing"
                )
            if isinstance(item, DocxParagraph):
                block = self._paragraph_to_block(
                    item, order, current_section, config
                )
                if block is None:
                    continue
                if block.type == BlockType.HEADING:
                    current_section = Section(
                        section_id=f"sec_{order}",
                        title=block.original_text,
                        level=block.level or 1,
                        order=len(sections),
                    )
                    sections.append(current_section)
                    block.section_id = current_section.section_id
                else:
                    block.section_id = current_section.section_id if current_section else None
                    block.section_title = current_section.title if current_section else None
                blocks.append(block)
                order += 1
            elif isinstance(item, DocxTable):
                table = self._table_to_data(item, table_index, order)
                table_index += 1
                blocks.append(
                    Block(
                        block_id=f"b_{order}",
                        type=BlockType.TABLE,
                        order=order,
                        section_id=current_section.section_id if current_section else None,
                        section_title=current_section.title if current_section else None,
                        original_text=_table_text(table),
                        normalized_text=normalise(_table_text(table), config),
                        source_position=SourcePosition(),
                        table=table,
                    )
                )
                order += 1

        if progress is not None:
            progress(92, "normalizing")

        doc_model = Document(
            document_id=f"doc_{file_path.stem}",
            blocks=blocks,
            sections=sections,
            metadata={
                "paragraph_count": len(blocks),
                "table_count": table_index,
            },
        )

        return ParseResult(
            file_type=self.file_type,
            content_type=ContentType.DOCUMENT,
            parser="docx_parser",
            parse_status="success",
            metadata={
                "page_count": _estimate_pages(document),
                "block_count": len(blocks),
            },
            document=doc_model,
            uncovered=list(self.known_gaps),
        )

    # -- helpers -----------------------------------------------------------
    def _paragraph_to_block(
        self,
        paragraph: DocxParagraph,
        order: int,
        section: Section | None,
        config: CompareConfig,
    ) -> Block | None:
        text = paragraph.text or ""
        style_name = ""
        try:
            style_name = paragraph.style.name or ""
        except Exception:  # noqa: BLE001 - broken styles are common in the wild
            style_name = ""

        # Header/footer paragraphs are kept and marked: whether they take
        # part in the comparison is decided by the rules of the comparison,
        # not by the parser.
        is_header_footer = bool(_HEADER_RE.search(style_name))

        images = _extract_images(paragraph.part, paragraph._p)
        stripped = text.strip()
        if not stripped:
            if not images:
                return None
            # A paragraph that only carries an image still needs a block, so
            # the picture can take part in the comparison.
            text = IMAGE_PLACEHOLDER

        level = None
        block_type = BlockType.PARAGRAPH
        if _HEADING_RE.match(style_name.strip()):
            level = int(_HEADING_RE.match(style_name.strip()).group(1))
            block_type = BlockType.HEADING
        elif _LIST_RE.search(style_name):
            block_type = BlockType.LIST

        metadata: dict = {"style": style_name}
        if is_header_footer:
            metadata["header_footer"] = True
        if images:
            metadata["images"] = images
        return Block(
            block_id=f"b_{order}",
            type=block_type,
            order=order,
            level=level,
            original_text=text,
            normalized_text=normalise(text, config),
            source_position=SourcePosition(),
            metadata=metadata,
        )

    def _table_to_data(
        self, table: DocxTable, table_index: int, order: int
    ) -> TableData:
        rows: list[TableRow] = []
        previous_tcs: list[object] | None = None
        for row_index, row in enumerate(table.rows):
            cells: list[TableCell] = []
            row_cells = row.cells
            for column_index, cell in enumerate(row_cells):
                text = cell.text or ""
                # A vertically merged cell spans several rows: the rows below
                # only repeat the cell of the first row.  The flag lets the
                # comparison report such a change once instead of once per
                # spanned row.
                continues = (
                    previous_tcs is not None
                    and column_index < len(previous_tcs)
                    and previous_tcs[column_index] is cell._tc
                )
                images = _extract_images(table.part, cell._tc)
                cells.append(
                    TableCell(
                        cell_id=f"t{table_index}_r{row_index}_c{column_index}",
                        row_index=row_index,
                        column_index=column_index,
                        original_text=text,
                        normalized_text=normalise(text),
                        value=text.strip(),
                        merged=continues,
                        images=images,
                    )
                )
            previous_tcs = [cell._tc for cell in row_cells]
            rows.append(
                TableRow(row_id=f"t{table_index}_r{row_index}", row_index=row_index, cells=cells)
            )
        return TableData(
            table_id=f"t{table_index}",
            rows=rows,
            layout=_table_layout(table),
            source_position=SourcePosition(),
        )


def _cell_text(tc) -> str:
    """The text of a cell element (``w:tc``)."""
    return "\n".join(paragraph.text for paragraph in tc.p_lst)


def _table_layout(table: DocxTable) -> list[list[dict]]:
    """The visual layout of a table: one entry per cell, with its spans.

    ``rows`` addresses the cells by *grid* position - Word's table grid, where
    a merged cell covers several grid columns.  The layout describes what the
    reader actually sees: one entry per cell, with the number of columns
    (``colspan``) and rows (``rowspan``) it covers.  A vertically merged cell
    is described once, in its first row; the rows below only carry the empty
    continuation (``continuation``), which the reader never sees.
    """
    rows: list[list[dict]] = []
    for tr in table._tbl.tr_lst:
        cells: list[dict] = []
        column = 0
        for tc in tr.tc_lst:
            colspan = max(1, tc.grid_span)
            cells.append(
                {
                    "text": _cell_text(tc),
                    "column": column,
                    "colspan": colspan,
                    "rowspan": 1,
                    "continuation": tc.vMerge == "continue",
                    "images": _extract_images(table.part, tc),
                }
            )
            column += colspan
        rows.append(cells)

    # A vertically merged cell spans the rows below it.
    for row_index, cells in enumerate(rows):
        for cell in cells:
            if cell["continuation"]:
                continue
            height = 1
            for next_row in rows[row_index + 1 :]:
                following = next(
                    (
                        item
                        for item in next_row
                        if item["column"] == cell["column"] and item["continuation"]
                    ),
                    None,
                )
                if following is None:
                    break
                height += 1
            cell["rowspan"] = height
    return rows


def _table_text(table: TableData) -> str:
    lines = []
    for row in table.rows:
        lines.append("\t".join(cell.original_text for cell in row.cells))
    return "\n".join(lines)


def _estimate_pages(document: DocxDocument) -> int:
    """Rough page count based on explicit page breaks."""
    count = 1
    xml = document.element.body.xml
    count += xml.count('w:type="page"')
    return count
