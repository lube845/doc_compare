"""Sample file builders used by the test suite.

The builders create real files (DOCX / XLSX / PDF / OLE2-DOC) so the parsers
are exercised end to end rather than against mocks.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

# --------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------


def build_docx(path: Path, paragraphs: list[str], tables: list[list[list[str]]] | None = None):
    import docx

    document = docx.Document()
    for paragraph in paragraphs:
        if paragraph.startswith("# "):
            document.add_heading(paragraph[2:], level=1)
        elif paragraph.startswith("## "):
            document.add_heading(paragraph[3:], level=2)
        else:
            document.add_paragraph(paragraph)
    for table in tables or []:
        rows = len(table)
        cols = len(table[0]) if rows else 0
        created = document.add_table(rows=rows, cols=cols)
        for row_index, row in enumerate(table):
            for column_index, value in enumerate(row):
                created.cell(row_index, column_index).text = value
    document.save(str(path))
    return path


def build_docx_merged(path: Path):
    """A DOCX with a horizontally and a vertically merged cell.

    Layout (3x3 grid)::

        +------------+--------+
        | 横向合并   |   C1   |
        +------+-----+--------+
        | 纵向 | D1  |   C2   |
        | 合并 |     |        |
        +------+-----+--------+
    """
    import docx

    document = docx.Document()
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "横向合并"
    table.cell(0, 2).text = "C1"
    table.cell(1, 0).merge(table.cell(2, 0))
    table.cell(1, 0).text = "纵向合并"
    table.cell(1, 1).text = "D1"
    table.cell(1, 2).text = "C2"
    table.cell(2, 1).text = "D2"
    table.cell(2, 2).text = "C3"
    document.save(str(path))
    return path



# --------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------


def _png(rgb: tuple[int, int, int]) -> bytes:
    """A valid 1x1 PNG with the given colour (no Pillow dependency)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"\x00" + bytes(rgb)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PNG_RED = _png((255, 0, 0))
PNG_BLUE = _png((0, 0, 255))


def build_docx_with_image(
    path: Path,
    image: bytes,
    *,
    in_table: bool = False,
) -> Path:
    """A document containing one picture (optionally inside a table cell)."""
    import io

    import docx

    document = docx.Document()
    document.add_paragraph("图片前的一段文字")
    if in_table:
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = ""
        table.cell(0, 0).paragraphs[0].add_run().add_picture(
            io.BytesIO(image), width=docx.shared.Inches(1)
        )
        table.cell(0, 1).text = "右格"
    else:
        document.add_picture(io.BytesIO(image), width=docx.shared.Inches(1))
    document.add_paragraph("图片后的一段文字")
    document.save(str(path))
    return path


# --------------------------------------------------------------------------
# XLSX
# --------------------------------------------------------------------------


def build_xlsx(
    path: Path,
    sheets: dict[str, list[list[object]]],
    formulas: dict[tuple[str, str], str] | None = None,
):
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(title=name)
        for row in rows:
            worksheet.append(row)
    for (sheet_name, coordinate), formula in (formulas or {}).items():
        workbook[sheet_name][coordinate] = formula
    workbook.save(str(path))
    return path


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


def build_pdf(
    path: Path,
    pages: list[list[str]],
    header: str | None = None,
    footer: str | None = None,
):
    """Write a minimal but valid PDF.

    ``pages`` is a list of pages, every page a list of paragraphs.  A
    paragraph is either a single string or a list of lines that are wrapped
    over several rendered lines - exactly like a text paragraph in a word
    processor.  The paragraphs are separated by a visible gap so that the
    parser can tell them apart, the lines of a paragraph are not.

    ``header`` and ``footer`` are drawn on every page, in the margin.
    """
    streams: list[str] = []
    for paragraphs in pages:
        parts = ["BT", "/F1 12 Tf", "16 TL"]
        if header:
            parts.append(f"1 0 0 1 72 800 Tm ({_escape_pdf(header)}) Tj")
        if footer:
            parts.append(f"1 0 0 1 72 50 Tm ({_escape_pdf(footer)}) Tj")

        y = 780.0
        for paragraph in paragraphs:
            parts.append(f"1 0 0 1 72 {y:.1f} Tm")
            for line in _paragraph_lines(paragraph):
                parts.append(f"({_escape_pdf(line)}) Tj")
                parts.append("T*")
                y -= 16
            # A visible gap between the paragraphs.
            y -= 16
        parts.append("ET")
        streams.append("\n".join(parts))

    _write_pdf(path, streams)
    return path


def build_pdf_table(
    path: Path,
    rows: list[list[str]],
    header: str | None = None,
    column_width: float = 130.0,
):
    """Write a PDF that holds a table without any ruling lines.

    The cells are only aligned by their position, which is what the parser
    has to recognise (PRD 8.3).
    """
    parts = ["BT", "/F1 12 Tf", "16 TL"]
    if header:
        parts.append(f"1 0 0 1 72 800 Tm ({_escape_pdf(header)}) Tj")

    y = 700.0
    for row in rows:
        x = 72.0
        for cell in row:
            parts.append(f"1 0 0 1 {x:.1f} {y:.1f} Tm ({_escape_pdf(cell)}) Tj")
            x += column_width
        y -= 18
    parts.append("ET")

    _write_pdf(path, ["\n".join(parts)])
    return path


def build_pdf_positions(
    path: Path,
    pages: list[list[tuple[float, float, str]]],
    header: str | None = None,
):
    """Write a PDF whose text is placed at absolute positions.

    ``pages`` is a list of pages, every page a list of ``(x, y, text)``.
    """
    streams: list[str] = []
    for items in pages:
        parts = ["BT", "/F1 12 Tf", "16 TL"]
        if header:
            parts.append(f"1 0 0 1 72 800 Tm ({_escape_pdf(header)}) Tj")
        for x, y, text in items:
            parts.append(f"1 0 0 1 {x:.1f} {y:.1f} Tm ({_escape_pdf(text)}) Tj")
        parts.append("ET")
        streams.append("\n".join(parts))

    _write_pdf(path, streams)
    return path


def build_pdf_lines(
    path: Path,
    lines: list[str],
    line_pitch: float = 24.7,
):
    """Write a PDF with uniformly spaced lines and no extra paragraph gap.

    This is what a DOCX -> PDF export looks like: the paragraph spacing is
    small relative to the line height, so a line based parser cannot tell
    paragraphs apart from the geometry alone.
    """
    parts = ["BT", "/F1 12 Tf", "16 TL"]
    y = 780.0
    for line in lines:
        parts.append(f"1 0 0 1 72 {y:.1f} Tm ({_escape_pdf(line)}) Tj")
        y -= line_pitch
    parts.append("ET")
    _write_pdf(path, ["\n".join(parts)])
    return path


def build_pdf_layout(
    path: Path,
    paragraphs: list[str],
    margin: float = 72.0,
    size: float = 10.0,
    leading: float | None = None,
    fontname: str = "china-s",
):
    """Write a PDF with a free choice of the typography.

    The same text can be written with another margin, another font size or
    another line spacing - that is what a PDF comparison has to survive
    (PDF 比对改造实施计划 · 阶段 3 的黄金用例).
    """
    import fitz

    leading = leading if leading is not None else size * 1.45
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    y = margin + size
    for paragraph in paragraphs:
        for chunk in _wrap(paragraph, int((595 - 2 * margin) / size)):
            page.insert_text((margin, y), chunk, fontsize=size, fontname=fontname)
            y += leading
        y += leading * 0.6
        if y > 800:
            page = document.new_page(width=595, height=842)
            y = margin + size
    document.save(str(path))
    document.close()
    return path


def _wrap(text: str, per_line: int) -> list[str]:
    """Cut a text into the lines of a column."""
    per_line = max(1, per_line)
    return [text[start : start + per_line] for start in range(0, len(text), per_line)]


def build_pdf_pages(
    path: Path,
    pages: list[list[str]],
    margin: float = 72.0,
    size: float = 10.0,
    leading: float | None = None,
    fontname: str = "china-s",
    width: float = 595.0,
    height: float = 842.0,
    header: str | None = None,
    footer: str | None = None,
):
    """Write a PDF whose pages are given one by one.

    ``pages`` is a list of pages, every page a list of paragraphs.  The
    paragraphs are wrapped automatically; a paragraph may also be a list of
    lines that are written exactly as they are.

    The builder is used by the acceptance tests of the V2 engine: they need
    documents with a known pagination (a page that was added at the front,
    a page that was removed in the middle, ...).
    """
    import fitz

    leading = leading if leading is not None else size * 1.5
    document = fitz.open()
    for paragraphs in pages:
        page = document.new_page(width=width, height=height)
        y = margin + size
        if header:
            page.insert_text((margin, margin * 0.5), header, fontsize=size, fontname=fontname)
        if footer:
            page.insert_text((margin, height - margin * 0.5), footer, fontsize=size, fontname=fontname)
        for paragraph in paragraphs:
            lines = paragraph if isinstance(paragraph, list) else _wrap(
                paragraph, int((width - 2 * margin) / size)
            )
            for line in lines:
                page.insert_text((margin, y), line, fontsize=size, fontname=fontname)
                y += leading
            y += leading * 0.6
    document.save(str(path))
    document.close()
    return path


def build_pdf_columns(
    path: Path,
    columns: list[list[str]],
    size: float = 10.0,
    leading: float = 14.0,
    fontname: str = "china-s",
    width: float = 595.0,
    height: float = 842.0,
    left: float = 60.0,
    right: float = 310.0,
):
    """Write a PDF whose text stands in two columns.

    The left column is written first, then the right one - the order a
    reader reads a two column page in.
    """
    import fitz

    document = fitz.open()
    page = document.new_page(width=width, height=height)
    for index, column in enumerate(columns):
        x = left if index == 0 else right
        y = 72.0 + size
        for line in column:
            page.insert_text((x, y), line, fontsize=size, fontname=fontname)
            y += leading
    document.save(str(path))
    document.close()
    return path


def build_pdf_font(path: Path, paragraphs: list[str], fontname: str = "china-s", **kwargs):
    """Write a PDF with a free choice of the font."""
    return build_pdf_pages(path, [paragraphs], fontname=fontname, **kwargs)


def _write_pdf(path: Path, streams: list[str]) -> None:
    """Write a PDF with one content stream per page."""
    objects: dict[int, bytes] = {}
    page_ids: list[int] = []
    content_ids: list[int] = []
    next_id = 4  # 1 = catalog, 2 = pages, 3 = font

    for _ in streams:
        page_ids.append(next_id)
        next_id += 1
        content_ids.append(next_id)
        next_id += 1

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")
    )
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for index, content in enumerate(streams):
        page_id = page_ids[index]
        content_id = content_ids[index]
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("latin-1")
        data = content.encode("latin-1", errors="replace")
        objects[content_id] = (
            f"<< /Length {len(data)} >>\nstream\n".encode("latin-1")
            + data
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for obj_id in sorted(objects):
        offsets[obj_id] = len(out)
        out += f"{obj_id} 0 obj\n".encode("latin-1") + objects[obj_id] + b"\nendobj\n"

    xref_offset = len(out)
    count = max(objects) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for obj_id in range(1, count):
        out += b"%010d 00000 n \n" % offsets.get(obj_id, 0)
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (count, xref_offset)
    )

    path.write_bytes(bytes(out))


def _escape_pdf(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _paragraph_lines(paragraph) -> list[str]:
    """The rendered lines of one paragraph."""
    if isinstance(paragraph, str):
        return [paragraph]
    return list(paragraph)


# --------------------------------------------------------------------------
# OLE2 (legacy .doc)
# --------------------------------------------------------------------------

ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF


def build_ole2(path: Path, streams: dict[str, bytes]) -> Path:
    """Write a minimal OLE2 compound file containing ``streams``.

    Only what the reader needs is produced: a single stream per name, stored
    in the mini stream, plus the FAT, directory and mini FAT.
    """
    sector_size = 512
    mini_size = 64

    # -- mini stream ----------------------------------------------------
    mini_stream = bytearray()
    mini_fat: list[int] = []
    stream_start: dict[str, int] = {}
    for name, payload in streams.items():
        if not payload:
            payload = b"\x00"
        first = len(mini_stream) // mini_size
        stream_start[name] = first
        count = max(1, (len(payload) + mini_size - 1) // mini_size)
        padded = payload + b"\x00" * (count * mini_size - len(payload))
        mini_stream += padded
        for index in range(count):
            mini_fat.append(
                (first + index + 1) if index < count - 1 else ENDOFCHAIN
            )

    # -- regular sector layout -----------------------------------------
    # sector 0: FAT, sector 1: directory, sector 2: mini FAT,
    # sector 3..: mini stream container
    mini_sector_count = len(mini_stream) // mini_size
    container_sectors = max(1, (len(mini_stream) + sector_size - 1) // sector_size)

    fat = [FREESECT] * 128
    fat[0] = FATSECT
    fat[1] = ENDOFCHAIN  # directory
    fat[2] = ENDOFCHAIN  # mini FAT
    for index in range(container_sectors):
        sector = 3 + index
        fat[sector] = (
            (sector + 1) if index < container_sectors - 1 else ENDOFCHAIN
        )

    fat_bytes = b"".join(struct.pack("<I", value) for value in fat)

    directory = bytearray()
    directory += _dir_entry("Root Entry", 5, 3, len(mini_stream), 1)
    for index, (name, payload) in enumerate(streams.items()):
        directory += _dir_entry(name, 2, stream_start[name], len(payload), NOSTREAM)
    while len(directory) % sector_size:
        directory += b"\x00" * 128
    # Pad the directory with empty entries.
    while len(directory) < sector_size:
        directory += b"\x00" * 128

    minifat_bytes = b"".join(struct.pack("<I", value) for value in mini_fat)
    minifat_bytes += b"\xff\xff\xff\xff" * (128 - len(minifat_bytes) // 4)

    container = bytes(mini_stream).ljust(container_sectors * sector_size, b"\x00")

    header = bytearray(b"\x00" * sector_size)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 0x18, 0x003E)  # minor version
    struct.pack_into("<H", header, 0x1A, 0x0003)  # major version
    struct.pack_into("<H", header, 0x1C, 0xFFFE)  # byte order
    struct.pack_into("<H", header, 0x1E, 9)  # sector shift -> 512
    struct.pack_into("<H", header, 0x20, 6)  # mini sector shift -> 64
    struct.pack_into("<I", header, 0x2C, 1)  # number of FAT sectors
    struct.pack_into("<I", header, 0x30, 1)  # first directory sector
    struct.pack_into("<I", header, 0x38, 4096)  # mini stream cutoff
    struct.pack_into("<I", header, 0x3C, 2)  # first mini FAT sector
    struct.pack_into("<I", header, 0x40, 1)  # number of mini FAT sectors
    struct.pack_into("<I", header, 0x44, ENDOFCHAIN)  # first DIFAT sector
    struct.pack_into("<I", header, 0x48, 0)  # number of DIFAT sectors
    for index in range(109):
        value = 0 if index == 0 else FREESECT
        struct.pack_into("<I", header, 0x4C + 4 * index, value)

    path.write_bytes(bytes(header) + fat_bytes + bytes(directory) + minifat_bytes + container)
    return path


def _dir_entry(name: str, entry_type: int, start: int, size: int, child: int) -> bytes:
    entry = bytearray(b"\x00" * 128)
    encoded = name.encode("utf-16-le")[:62]
    entry[0 : len(encoded)] = encoded
    struct.pack_into("<H", entry, 0x40, len(encoded) + 2)
    entry[0x42] = entry_type
    entry[0x43] = 1  # black
    struct.pack_into("<I", entry, 0x44, child)
    struct.pack_into("<I", entry, 0x48, NOSTREAM)
    struct.pack_into("<I", entry, 0x4C, NOSTREAM)
    struct.pack_into("<I", entry, 0x74, start)
    struct.pack_into("<I", entry, 0x78, size)
    return bytes(entry)


def build_doc(path: Path, paragraphs: list[str]) -> Path:
    """Build a legacy Word 97-2003 binary file with the given paragraphs."""
    text = "".join(f"{paragraph}\r" for paragraph in paragraphs)
    encoded = text.encode("utf-16-le")

    fib = bytearray(b"\x00" * 0x200)
    struct.pack_into("<H", fib, 0x00, 0xA5EC)  # wIdent
    struct.pack_into("<H", fib, 0x02, 0x00C1)  # nFib (Word 97)
    struct.pack_into("<H", fib, 0x0A, 0x0000)  # flags -> "0Table"
    struct.pack_into("<I", fib, 0x4C, len(text))  # ccpText
    struct.pack_into("<I", fib, 0x01A2, 0)  # fcClx
    struct.pack_into("<I", fib, 0x01A6, 0)  # lcbClx (patched below)

    text_offset = len(fib)
    word_stream = bytes(fib) + encoded

    # PlcPcd with a single piece
    plc_pcd = struct.pack("<I", 0) + struct.pack("<I", len(text))
    plc_pcd += struct.pack("<H", 0) + struct.pack("<I", text_offset) + struct.pack("<H", 0)
    clx = b"\x02" + struct.pack("<I", len(plc_pcd)) + plc_pcd

    struct.pack_into("<I", fib, 0x01A6, len(clx))
    word_stream = bytes(fib) + encoded

    return build_ole2(path, {"WordDocument": word_stream, "0Table": clx})
