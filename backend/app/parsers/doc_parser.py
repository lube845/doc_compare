"""Legacy Word (``.doc``) parser.

Two strategies are used:

1. **LibreOffice** - convert to DOCX and reuse :class:`DocxParser`.  This is
   the recommended route from PRD 12.2 and yields full structure.
2. **Native binary reader** - when LibreOffice is not installed the Word 97+
   piece table is decoded directly.  Paragraph text is recovered faithfully;
   table structure is recovered from cell marks (0x07).
"""

from __future__ import annotations

import struct
from pathlib import Path

from ..domain.models import (
    Block,
    BlockType,
    ContentType,
    Document,
    ParseResult,
    SourcePosition,
    TableCell,
    TableData,
    TableRow,
)
from ..domain.normalize import normalise
from ..schemas import CompareConfig
from . import libreoffice
from .base import BaseParser, ParseFailed, ProgressCallback
from .cfb import CompoundFile, CFBError

CELL_MARK = 0x07
PARA_MARK = 0x0D
LINE_MARK = 0x0B
PAGE_MARK = 0x0C


def _decode_piece_table(cfb: CompoundFile) -> str | None:
    """Extract the main document text from a Word 97+ binary file."""
    word = cfb.read_stream("WordDocument")
    if not word or len(word) < 0x200:
        return None

    flags = struct.unpack_from("<H", word, 0x0A)[0]
    table_name = "1Table" if flags & 0x0200 else "0Table"
    table = cfb.read_stream(table_name)
    if table is None:
        table = cfb.read_stream("1Table") or cfb.read_stream("0Table")
    if table is None:
        return None

    # Word97 FIB offsets
    try:
        fc_clx = struct.unpack_from("<I", word, 0x01A2)[0]
        lcb_clx = struct.unpack_from("<I", word, 0x01A6)[0]
        ccp_text = struct.unpack_from("<I", word, 0x4C)[0]
    except struct.error:
        return None

    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        return None

    clx = table[fc_clx : fc_clx + lcb_clx]

    # Walk the Clx: 0x01 -> Prc, 0x02 -> Pcdt
    index = 0
    pcdt = None
    while index < len(clx):
        marker = clx[index]
        if marker == 0x01:
            if index + 3 > len(clx):
                break
            (cb_grpprl,) = struct.unpack_from("<h", clx, index + 1)
            index += 3 + cb_grpprl
        elif marker == 0x02:
            if index + 5 > len(clx):
                break
            (lcb,) = struct.unpack_from("<I", clx, index + 1)
            pcdt = clx[index + 5 : index + 5 + lcb]
            break
        else:
            break

    if not pcdt or len(pcdt) < 4:
        return None

    # PlcPcd: (n+1) CPs followed by n 8-byte PCDs
    n_pieces = (len(pcdt) - 4) // 12
    if n_pieces <= 0:
        return None
    if 4 * (n_pieces + 1) + 8 * n_pieces > len(pcdt):
        return None

    cps = list(struct.unpack_from(f"<{n_pieces + 1}I", pcdt, 0))
    pcd_offset = 4 * (n_pieces + 1)

    chunks: list[str] = []
    for i in range(n_pieces):
        base = pcd_offset + 8 * i
        (fc_field,) = struct.unpack_from("<I", pcdt, base + 2)
        compressed = bool(fc_field & 0x40000000)
        fc = fc_field & 0x3FFFFFFF
        cp_start, cp_end = cps[i], cps[i + 1]
        length = cp_end - cp_start
        if length <= 0:
            continue
        if compressed:
            fc //= 2
            raw = word[fc : fc + length]
            text = raw.decode("cp1252", errors="replace")
        else:
            raw = word[fc : fc + length * 2]
            text = raw.decode("utf-16-le", errors="replace")
        chunks.append(text)

    text = "".join(chunks)
    if ccp_text and ccp_text < len(text):
        text = text[:ccp_text]
    return text


def _split_paragraphs(text: str) -> list[list[str]]:
    """Split raw Word text into paragraphs; each paragraph is a list of cells."""
    paragraphs: list[list[str]] = []
    current: list[str] = []
    buffer: list[str] = []

    def flush_cell() -> None:
        if buffer:
            current.append("".join(buffer))
            buffer.clear()

    def flush_paragraph() -> None:
        flush_cell()
        if current:
            paragraphs.append(list(current))
        current.clear()

    for char in text:
        code = ord(char)
        if code == CELL_MARK:
            flush_cell()
            continue
        if code == PARA_MARK:
            flush_paragraph()
            continue
        if code in (LINE_MARK, PAGE_MARK):
            buffer.append("\n")
            continue
        if code in (0x01, 0x02, 0x05, 0x08, 0x13, 0x14, 0x15, 0x07):
            continue
        if code < 0x20 and code not in (0x09, 0x0A):
            continue
        buffer.append(char)
    flush_paragraph()
    return paragraphs


class DocParser(BaseParser):
    file_type = "doc"
    display_name = "Microsoft Word 97-2003"
    content_type = ContentType.DOCUMENT.value
    known_gaps = ("样式层级", "页眉页脚定位", "文本框内容", "图片内容识别（仅比对图片是否相同）")

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        config = options or CompareConfig()

        if libreoffice.is_available():
            converted = libreoffice.convert_to_ooxml(file_path, "doc")
            if converted is not None:
                from .docx_parser import DocxParser

                result = DocxParser().parse(converted, config, progress)
                result.file_type = self.file_type
                result.parser = "doc_parser"
                result.uncovered = list(self.known_gaps)
                return result

        return self._parse_native(file_path, config)

    # -- native reader -----------------------------------------------------
    def _parse_native(
        self, file_path: Path, config: CompareConfig
    ) -> ParseResult:
        try:
            data = file_path.read_bytes()
            cfb = CompoundFile(data)
        except (CFBError, OSError) as exc:
            raise ParseFailed(f"无法读取 DOC 文件：{exc}") from exc

        text = _decode_piece_table(cfb)
        if text is None:
            raise ParseFailed(
                "DOC 文件解析失败：未找到可用的文本流（文件可能已损坏或为受保护文档）"
            )

        raw_paragraphs = _split_paragraphs(text)
        blocks: list[Block] = []
        order = 0
        table_buffer: list[list[str]] = []

        def flush_table() -> None:
            nonlocal order, table_buffer
            if not table_buffer:
                return
            table = _build_table(table_buffer, order)
            blocks.append(
                Block(
                    block_id=f"b_{order}",
                    type=BlockType.TABLE,
                    order=order,
                    original_text=_table_text(table),
                    normalized_text=normalise(_table_text(table), config),
                    table=table,
                )
            )
            order += 1
            table_buffer = []

        for paragraph in raw_paragraphs:
            if len(paragraph) > 1:
                # A cell mark terminated the paragraph -> it is a table row.
                table_buffer.append(paragraph)
                continue
            flush_table()
            content = paragraph[0].strip() if paragraph else ""
            if not content:
                continue
            blocks.append(
                Block(
                    block_id=f"b_{order}",
                    type=BlockType.PARAGRAPH,
                    order=order,
                    original_text=content,
                    normalized_text=normalise(content, config),
                )
            )
            order += 1
        flush_table()

        document = Document(
            document_id=f"doc_{file_path.stem}",
            blocks=blocks,
            metadata={"paragraph_count": len(blocks), "parser": "native-piece-table"},
        )
        return ParseResult(
            file_type=self.file_type,
            content_type=ContentType.DOCUMENT,
            parser="doc_parser",
            parse_status="success",
            metadata={"block_count": len(blocks)},
            document=document,
            uncovered=list(self.known_gaps),
        )


def _build_table(rows: list[list[str]], order: int) -> TableData:
    table_rows: list[TableRow] = []
    for row_index, row in enumerate(rows):
        cells: list[TableCell] = []
        for column_index, value in enumerate(row):
            cells.append(
                TableCell(
                    cell_id=f"t{order}_r{row_index}_c{column_index}",
                    row_index=row_index,
                    column_index=column_index,
                    original_text=value,
                    normalized_text=normalise(value),
                    value=value.strip(),
                )
            )
        table_rows.append(
            TableRow(row_id=f"t{order}_r{row_index}", row_index=row_index, cells=cells)
        )
    return TableData(table_id=f"t{order}", rows=table_rows)


def _table_text(table: TableData) -> str:
    return "\n".join(
        "\t".join(cell.original_text for cell in row.cells) for row in table.rows
    )
