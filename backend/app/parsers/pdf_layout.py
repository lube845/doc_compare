"""PDF layout analysis - turn raw page fragments into logical paragraphs.

PRD chapter 8.2 is explicit about this: a PDF has no paragraphs, it has
glyphs placed on a page.  Comparing the raw fragments therefore compares
*rendered lines*, not *content*:

* one logical paragraph is torn into one block per rendered line,
* running headers and footers are mixed into the body text,
* text that lives inside a table is duplicated (once as a table, once as
  text),
* a paragraph that spans a page break ends up as two unrelated blocks,
* the reading order of a page is the PDF drawing order, not the visual one.

This module rebuilds the logical structure:

1. :func:`extract_lines` reads the *lines* of a page together with their
   geometry and font information.
2. :func:`detect_running_text` finds the page headers / footers that repeat
   from page to page.
3. :func:`group_lines` groups the remaining lines into paragraphs using the
   typographic cues of the page (font size, indentation, line spacing and
   sentence punctuation).
4. :func:`detect_text_tables` recognises tables that have no ruling lines,
   only aligned text columns (PRD 8.3).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

#: vertical gap (relative to the line height) that separates two paragraphs
LINE_GAP_RATIO = 0.8
#: a paragraph break is a vertical distance that is clearly larger than the
#: line pitch of the page.  The absolute rule (``LINE_GAP_RATIO``) breaks on
#: every second line of a document with a large line spacing, so the pitch
#: of the page is used whenever it can be measured.
PARA_GAP_PITCH_RATIO = 1.5
#: minimum number of line pairs needed before the pitch may be trusted
PITCH_MIN_SAMPLES = 5
#: left indent change (relative to the font size) that separates two paragraphs
INDENT_RATIO = 0.6
#: a line shorter than this share of its column ends a paragraph
SHORT_LINE_RATIO = 0.7
#: font size above the body size that marks a heading
HEADING_SIZE_RATIO = 1.15
#: longest text that may still be a heading when it carries a chapter number
HEADING_MAX_LENGTH = 40
#: top share of the page reserved for running headers
HEADER_ZONE = 0.09
#: bottom share of the page reserved for running footers
FOOTER_ZONE = 0.91
#: share of the pages a running header/footer has to appear on
RECURRING_RATIO = 0.4
#: horizontal distance below which two lines belong to the same column
BAND_TOLERANCE = 12.0

# --------------------------------------------------------------------------
# Character classes
# --------------------------------------------------------------------------

_STRONG_TERMINATORS = "。！？；…!?"

#: a line that starts with ``标签：内容`` - the shape of the field lines found
#: in contracts, forms and cover pages ("合同编号：HT-2024-001").
_FIELD_LABEL_RE = re.compile(r"^\s*([^\s：:]{1,24})[：:]")
_ABBREVIATIONS = {
    "e.g",
    "i.e",
    "etc",
    "vs",
    "no",
    "fig",
    "eq",
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "inc",
    "ltd",
    "co",
    "vol",
    "pp",
    "al",
    "approx",
}

#: a line that starts with one of these markers is a list item.  The negative
#: look ahead keeps decimal numbers ("8.6%") from being read as an item.
_BULLET_RE = re.compile(
    r"^\s*(?:"
    r"[•·○●▪◆◇■□‣⁃⁎]"
    r"|[-*–—]\s"
    r"|\([0-9]{1,3}\)\s*"
    r"|[0-9]{1,3}[.)、](?![0-9])\s*"
    r"|[（(][0-9]{1,3}[)）]\s*"
    r")"
)

#: a line that starts with one of these markers looks like a heading in the
#: Chinese official document style ("第一章", "一、", "（一）", "1.2").
_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9一二三四五六七八九十百千]+\s*[章节篇部条]"
    r"|[一二三四五六七八九十百]{1,3}[、.]"
    r"|[（(][一二三四五六七八九十百]{1,3}[)）]"
    r"|[0-9]{1,2}(?:\.[0-9]{1,2}){1,3}[.、]?"
    r")\s*"
)

#: a line that starts with a number and is followed by something that cannot
#: be a heading text ("8.6%，同比增长..." is a sentence, not a heading)
_NUMERIC_HEADING_RE = re.compile(r"^\s*[0-9]{1,2}(?:\.[0-9]{1,2}){1,3}[.、]?")
#: characters that turn a numeric prefix into a number rather than a heading
_NUMBER_LIKE = "%％$＄¥￥€£"

#: numbering -> heading level
_CHAPTER_RE = re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百千]+\s*[章节篇部条]")
_C_NUMBER_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)*)")
_CN_NUMBER_RE = re.compile(r"^\s*([一二三四五六七八九十百]+)[、.]")
_PAREN_CN_RE = re.compile(r"^\s*[（(]([一二三四五六七八九十百]+)[)）]")
_PAREN_NUM_RE = re.compile(r"^\s*[（(]([0-9]+)[)）]")

#: a number written with spaces between its digits ("1 0" is the page 10 of
#: a PDF that extracts the digits of a wide font one by one)
_SPACED_DIGIT_RE = re.compile(r"\d(?:\s*\d)*")
_WHITESPACE_RE = re.compile(r"\s+")


def is_cjk(char: str) -> bool:
    """True for CJK ideographs and CJK punctuation."""
    if not char:
        return False
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3000 <= code <= 0x303F
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF
    )


def _is_latin(char: str) -> bool:
    return bool(char) and char.isascii() and char.isalpha()


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class PdfLine:
    """A single rendered line of a page."""

    page: int
    text: str
    bbox: tuple[float, float, float, float]
    size: float = 0.0
    bold: bool = False
    font: str = ""
    #: position of the line in the reading order of its page
    index: int = 0
    #: the line repeats on many pages: it is a running header or footer
    running: bool = False
    #: the characters of the line with their own box - the raw material of
    #: the character stream (阶段 2)
    chars: list[tuple[float, float, float, float, str]] = field(
        default_factory=list
    )

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)


@dataclass
class PdfParagraph:
    """A logical paragraph rebuilt from one or more rendered lines."""

    text: str
    pages: list[int] = field(default_factory=list)
    bbox: list[float] = field(default_factory=list)
    kind: str = "paragraph"  # heading | paragraph | list
    level: int | None = None
    size: float = 0.0
    bold: bool = False
    lines: list[PdfLine] = field(default_factory=list)

    @property
    def page(self) -> int:
        return self.pages[0] if self.pages else 1

    @property
    def y0(self) -> float:
        return self.bbox[1] if self.bbox else 0.0


@dataclass
class DetectedTable:
    """A table recognised from the alignment of its text columns."""

    rows: list[list[str]] = field(default_factory=list)
    bbox: list[float] = field(default_factory=list)


# --------------------------------------------------------------------------
# 1. Line extraction
# --------------------------------------------------------------------------


def extract_lines(page, page_number: int) -> list[PdfLine]:
    """Read the rendered lines of ``page`` in visual reading order.

    PyMuPDF does not always return whole lines: a text line can be cut into
    several fragments whenever there is a wide gap inside it (justified text
    for instance).  The fragments are therefore merged back into the visual
    line they belong to, and the text is rebuilt from the characters so that
    no character is lost or moved.
    """
    fragments = _extract_fragments(page)
    lines: list[PdfLine] = []

    for group in _merge_fragments(fragments):
        chars = sorted(group.chars, key=lambda item: item[0])
        text = "".join(char for _x0, _y0, _x1, _y1, char in chars)
        if not text.strip():
            continue
        bbox = (
            min(char[0] for char in chars),
            min(char[1] for char in chars),
            max(char[2] for char in chars),
            max(char[3] for char in chars),
        )
        size, bold, fonts = _dominant_style(group)
        lines.append(
            PdfLine(
                page=page_number,
                text=text,
                bbox=bbox,
                size=size,
                bold=bold,
                font=fonts,
                chars=chars,
            )
        )

    ordered = _order_lines(lines, _page_width(page))
    for position, line in enumerate(ordered):
        line.index = position
    return ordered


def page_text_size(page) -> tuple[float, float]:
    """The size of the page in the coordinate system of its text.

    ``page.rect`` follows the rotation of the page, the text coordinates do
    not: on a page that is rotated by 90 or 270 degrees the two disagree,
    and every comparison of a line with the page width would be wrong.
    """
    try:
        width = float(page.rect.width)
        height = float(page.rect.height)
        rotation = int(getattr(page, "rotation", 0) or 0) % 360
    except Exception:  # noqa: BLE001 - defensive
        return 0.0, 0.0
    if rotation in (90, 270):
        return height, width
    return width, height


def _page_width(page) -> float:
    """Width of the page, 0.0 when it cannot be determined."""
    return page_text_size(page)[0]


#: horizontal distance below which two lines belong to the same column
COLUMN_TOLERANCE = 20.0
#: share of the lines that must sit in a column before the page is treated
#: as a multi column layout
COLUMN_COVERAGE = 0.85
#: a line that covers this share of the page width crosses the columns
FULL_WIDTH_RATIO = 0.7


def _order_lines(lines: list[PdfLine], page_width: float = 0.0) -> list[PdfLine]:
    """Sort the lines of a page into reading order.

    A page with two columns has to be read column by column, not line by
    line across the whole page.  A title that spans both columns breaks the
    column flow into horizontal bands: the title ends a band, and every band
    is read column by column.
    """
    if len(lines) < 6:
        return sorted(lines, key=lambda line: (round(line.y0, 1), line.x0))

    columns = detect_columns(lines, page_width)
    if len(columns) < 2:
        return sorted(lines, key=lambda line: (round(line.y0, 1), line.x0))

    def column_index(line: PdfLine) -> int:
        index = 0
        for position, column in enumerate(columns):
            if line.x0 + COLUMN_TOLERANCE >= column["x0"]:
                index = position
        return index

    def is_full_width(line: PdfLine) -> bool:
        return bool(page_width) and line.width >= page_width * FULL_WIDTH_RATIO

    ordered: list[PdfLine] = []
    band: list[PdfLine] = []

    def flush() -> None:
        band.sort(key=lambda line: (column_index(line), round(line.y0, 1), line.x0))
        ordered.extend(band)
        band.clear()

    for line in sorted(lines, key=lambda line: (round(line.y0, 1), line.x0)):
        if is_full_width(line):
            flush()
            ordered.append(line)
            continue
        band.append(line)
    flush()
    return ordered


#: public alias - the OCR parser has to put its lines in the same reading order
order_lines = _order_lines


def detect_columns(lines: list[PdfLine], page_width: float = 0.0) -> list[dict]:
    """Find the text columns of a page (empty list when there is only one).

    A page that is laid out in two columns must not be mistaken for a table
    with two columns.  Lines that span the whole width (the title of a
    chapter, a full width quote) are excluded from the statistics: they
    would otherwise destroy the two column structure.
    """
    candidates = [
        line
        for line in lines
        if not (
            page_width and line.width >= page_width * FULL_WIDTH_RATIO
        )
    ]
    if len(candidates) < 4:
        candidates = list(lines)
    if not candidates:
        return []

    clusters: list[dict] = []
    for line in sorted(candidates, key=lambda item: item.x0):
        if clusters and abs(line.x0 - clusters[-1]["x0"]) <= COLUMN_TOLERANCE:
            cluster = clusters[-1]
            cluster["lines"].append(line)
            cluster["x1"] = max(cluster["x1"], line.x1)
        else:
            clusters.append(
                {"x0": line.x0, "x1": line.x1, "lines": [line]}
            )
    if len(clusters) < 2:
        return []

    clusters.sort(key=lambda cluster: -len(cluster["lines"]))
    covered = sum(len(cluster["lines"]) for cluster in clusters[:2])
    if covered < len(candidates) * COLUMN_COVERAGE:
        return []

    # The columns have to sit next to each other, not on top of each other.
    first, second = sorted(clusters[:2], key=lambda cluster: cluster["x0"])
    if first["x1"] > second["x0"] - COLUMN_TOLERANCE:
        return []

    return sorted(clusters[:2], key=lambda cluster: cluster["x0"])


@dataclass
class _Fragment:
    """A piece of a rendered line, as PyMuPDF reports it."""

    chars: list[tuple[float, float, float, float, str]] = field(default_factory=list)
    sizes: list[tuple[float, int]] = field(default_factory=list)
    bold: bool = False
    fonts: set[str] = field(default_factory=set)

    @property
    def x0(self) -> float:
        return min(char[0] for char in self.chars)

    @property
    def x1(self) -> float:
        return max(char[2] for char in self.chars)

    @property
    def y0(self) -> float:
        return min(char[1] for char in self.chars)

    @property
    def y1(self) -> float:
        return max(char[3] for char in self.chars)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)


def _extract_fragments(page) -> list[_Fragment]:
    """Read the text fragments of a page, character by character."""
    fragments: list[_Fragment] = []
    try:
        raw = page.get_text("rawdict") or {}
    except Exception:  # noqa: BLE001 - a broken page must not kill the parse
        return fragments

    for block in raw.get("blocks", []) or []:
        if block.get("type") != 0:  # 0 = text, 1 = image
            continue
        for raw_line in block.get("lines", []) or []:
            fragment = _Fragment()
            for span in raw_line.get("spans", []) or []:
                span_chars = span.get("chars") or []
                if not span_chars:
                    # Some producers do not deliver characters at all.
                    text = span.get("text") or ""
                    if not text:
                        continue
                    bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                    fragment.chars.append(
                        (
                            float(bbox[0]),
                            float(bbox[1]),
                            float(bbox[2]),
                            float(bbox[3]),
                            text,
                        )
                    )
                    fragment.sizes.append((float(span.get("size") or 0.0), len(text)))
                    continue
                for char in span_chars:
                    glyph = char.get("c") or ""
                    if not glyph:
                        continue
                    bbox = char.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                    fragment.chars.append(
                        (
                            float(bbox[0]),
                            float(bbox[1]),
                            float(bbox[2]),
                            float(bbox[3]),
                            glyph,
                        )
                    )
                    fragment.sizes.append(
                        (float(span.get("size") or 0.0), 1)
                    )
                flags = int(span.get("flags") or 0)
                if flags & 16:  # PyMuPDF: bit 4 == bold
                    fragment.bold = True
                font = span.get("font") or ""
                if font:
                    fragment.fonts.add(font)
                    lowered = font.lower()
                    if "bold" in lowered or "black" in lowered or "heavy" in lowered:
                        fragment.bold = True
            if fragment.chars:
                fragments.append(fragment)
    return fragments


def _merge_fragments(fragments: list[_Fragment]) -> list[_Fragment]:
    """Merge the fragments that belong to the same rendered line."""
    lines: list[_Fragment] = []
    for row in _group_by_baseline(fragments):
        current: _Fragment | None = None
        for fragment in sorted(row, key=lambda item: item.x0):
            if current is not None and _adjacent(current, fragment):
                _absorb(current, fragment)
                continue
            if current is not None:
                lines.append(current)
            current = _copy(fragment)
        if current is not None:
            lines.append(current)
    return lines


def _group_by_baseline(fragments: list[_Fragment]) -> list[list[_Fragment]]:
    """Group the fragments that share a baseline.

    The fragments arrive in the drawing order of the PDF, which is not the
    visual order: two fragments of the same line can be separated by the
    fragments of another line.  The fragments are therefore sorted by the
    vertical centre of their box before they are grouped, and the tolerance
    is derived from the median height of the page.
    """
    if not fragments:
        return []

    heights = sorted(fragment.height for fragment in fragments)
    median_height = heights[len(heights) // 2] or 0.0
    tolerance = max(1.0, median_height * 0.5)

    ordered = sorted(
        fragments,
        key=lambda fragment: (
            (fragment.y0 + fragment.y1) / 2,
            fragment.x0,
        ),
    )

    rows: list[list[_Fragment]] = []
    centers: list[float] = []
    for fragment in ordered:
        center = (fragment.y0 + fragment.y1) / 2
        for index in range(len(rows) - 1, -1, -1):
            if abs(center - centers[index]) > tolerance:
                continue
            if not _same_baseline(rows[index][0], fragment):
                continue
            rows[index].append(fragment)
            centers[index] = sum(
                (item.y0 + item.y1) / 2 for item in rows[index]
            ) / len(rows[index])
            break
        else:
            rows.append([fragment])
            centers.append(center)

    return [sorted(row, key=lambda fragment: fragment.x0) for row in rows]


def _same_baseline(line: _Fragment, fragment: _Fragment) -> bool:
    """True when the fragment sits on the same baseline as the line."""
    overlap = min(line.y1, fragment.y1) - max(line.y0, fragment.y0)
    smallest = min(line.height, fragment.height)
    if overlap <= 0:
        return False
    return smallest <= 0 or overlap >= smallest * 0.5


def _adjacent(line: _Fragment, fragment: _Fragment) -> bool:
    """True when the fragment continues the line horizontally."""
    gap = fragment.x0 - line.x1
    if gap <= 0:
        # The fragment overlaps the line (a wide space inside the text).
        return True
    size = _dominant_size(fragment.sizes)
    return gap <= max(size * 1.5, 4.0)


def _absorb(line: _Fragment, fragment: _Fragment) -> None:
    """Merge a fragment into a line."""
    line.chars.extend(fragment.chars)
    line.sizes.extend(fragment.sizes)
    line.bold = line.bold or fragment.bold
    line.fonts.update(fragment.fonts)


def _copy(fragment: _Fragment) -> _Fragment:
    copied = _Fragment()
    copied.chars.extend(fragment.chars)
    copied.sizes.extend(fragment.sizes)
    copied.bold = fragment.bold
    copied.fonts.update(fragment.fonts)
    return copied


def _dominant_size(sizes: list[tuple[float, int]]) -> float:
    """The font size that covers most of the characters."""
    if not sizes:
        return 0.0
    counter: Counter[float] = Counter()
    for size, count in sizes:
        counter[round(size, 1)] += count
    return counter.most_common(1)[0][0]


def _dominant_style(fragment: _Fragment) -> tuple[float, bool, str]:
    """Return (font size, bold, font name) of a merged line."""
    return (
        _dominant_size(fragment.sizes),
        fragment.bold,
        ",".join(sorted(fragment.fonts)),
    )


# --------------------------------------------------------------------------
# 2. Running headers and footers
# --------------------------------------------------------------------------


def _signature(text: str) -> str:
    """Normalised form of a header/footer candidate (page numbers collapse)."""
    collapsed = _WHITESPACE_RE.sub(" ", (text or "").strip()).lower()
    # "第 1 页" and "第 10 页" (written as "第 1 0 页") are the same footer.
    return _SPACED_DIGIT_RE.sub("#", collapsed)


def detect_running_text(
    pages: list[tuple[int, list[PdfLine], float]],
    ratio: float = RECURRING_RATIO,
) -> set[tuple[int, int]]:
    """Return the ``(page, line index)`` pairs that are running headers/footers.

    ``pages`` is a list of ``(page number, lines, page height)`` tuples.
    """
    if len(pages) < 2:
        return set()

    counts: dict[str, int] = {}
    where: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for page_number, lines, page_height in pages:
        for index, line in enumerate(lines):
            signature = _signature(line.text)
            if not signature:
                continue
            in_header = page_height > 0 and line.y1 <= page_height * HEADER_ZONE
            in_footer = page_height > 0 and line.y0 >= page_height * FOOTER_ZONE
            if not in_header and not in_footer:
                continue
            counts[signature] = counts.get(signature, 0) + 1
            where[signature].append((page_number, index))

    threshold = max(2, math.ceil(len(pages) * ratio))
    dropped: set[tuple[int, int]] = set()
    for signature, count in counts.items():
        if count >= threshold:
            dropped.update(where[signature])
    return dropped


def inside_any_table(line: PdfLine, bboxes: list[list[float]]) -> bool:
    """True when the line sits inside one of the given table boxes."""
    if not bboxes:
        return False
    cx = (line.x0 + line.x1) / 2
    cy = (line.y0 + line.y1) / 2
    for bbox in bboxes:
        if len(bbox) < 4:
            continue
        x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
        if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
            return True
    return False


# --------------------------------------------------------------------------
# 3. Line grouping
# --------------------------------------------------------------------------


def body_font_size(lines: list[PdfLine]) -> float:
    """The most common font size, weighted by the number of characters."""
    weights: Counter[float] = Counter()
    for line in lines:
        weight = len(line.text.strip())
        if weight:
            weights[round(line.size, 1)] += weight
    if not weights:
        return 0.0
    return weights.most_common(1)[0][0]


def group_lines(
    lines: list[PdfLine],
    body_size: float = 0.0,
) -> list[PdfParagraph]:
    """Group the lines of one page (or several) into logical paragraphs."""
    paragraphs: list[PdfParagraph] = []
    current: list[PdfLine] = []
    bands = _column_bands(lines)
    usable = [line for line in lines if line.text.strip()]
    pitch = line_pitch(usable)

    for index, line in enumerate(usable):
        following = usable[index + 1] if index + 1 < len(usable) else None
        if current and _starts_new_paragraph(current[-1], line, bands, following, pitch):
            paragraphs.append(_make_paragraph(current, body_size))
            current = []
        current.append(line)

    if current:
        paragraphs.append(_make_paragraph(current, body_size))
    return paragraphs


def line_pitch(lines: list[PdfLine]) -> float:
    """The typical distance between two consecutive lines of a page.

    The pitch is the distance between the tops of two lines that belong
    together (same font size, same left edge).  It tells how much larger
    than the line spacing a paragraph break is - an absolute threshold
    cannot do that, because a document with a line spacing of two would
    then have a paragraph on every second line.

    The distances of a page are not all line spacings: the distance
    between two paragraphs is larger.  The line spacing is therefore the
    *smallest* cluster of distances and not their average - otherwise a
    document of one line paragraphs would report the paragraph distance
    as its line spacing and never find a paragraph break.
    """
    pitches: list[float] = []
    for previous, current in zip(lines, lines[1:]):
        if abs(current.size - previous.size) > 0.6:
            continue
        if abs(current.x0 - previous.x0) > INDENT_RATIO * max(current.size, 1.0):
            continue
        pitch = current.y0 - previous.y0
        if pitch > 0:
            pitches.append(pitch)
    if len(pitches) < PITCH_MIN_SAMPLES:
        return 0.0

    pitches.sort()
    smallest = pitches[0]
    # The lines of a paragraph are all at (about) the same distance; the
    # paragraph breaks are further apart.
    cluster = [pitch for pitch in pitches if pitch <= smallest * 1.25]
    if len(cluster) < 3:
        cluster = pitches[: max(3, len(pitches) // 4)]
    return cluster[len(cluster) // 2]


def _column_bands(lines: list[PdfLine]) -> list[tuple[float, float]]:
    """The text columns of a page as ``(left edge, right edge)`` pairs."""
    bands: list[list[float]] = []
    for line in sorted(lines, key=lambda item: item.x0):
        if bands and abs(line.x0 - bands[-1][0]) <= BAND_TOLERANCE:
            band = bands[-1]
            band[1] = max(band[1], line.x1)
        else:
            bands.append([line.x0, line.x1])
    return [(band[0], band[1]) for band in bands]


def _column_right_edge(line: PdfLine, bands: list[tuple[float, float]]) -> float:
    """Right edge of the column the line belongs to."""
    edge = line.x1
    for left, right in bands:
        if abs(line.x0 - left) <= BAND_TOLERANCE:
            edge = max(edge, right)
    return edge


def _starts_new_paragraph(
    prev: PdfLine,
    line: PdfLine,
    bands: list[tuple[float, float]],
    following: PdfLine | None = None,
    pitch: float = 0.0,
) -> bool:
    """Decide whether ``line`` starts a new paragraph after ``prev``."""
    # A different font size means a different element (title, heading, ...).
    if abs(line.size - prev.size) > 0.6:
        return True

    # A finished sentence ends the paragraph.
    if _ends_sentence(prev.text, line.text):
        return True

    # A list item always starts a new paragraph.
    if _BULLET_RE.match(line.text):
        return True

    # Two consecutive field lines ("合同编号：...", "合同金额：...") are
    # separate paragraphs even when the PDF uses the same line pitch for
    # every line.  Without this rule a PDF export silently merges a whole
    # block of fields into one paragraph, which then cannot be aligned with
    # the individual fields of the same document.
    if _is_field_line(prev.text) and _is_field_line(line.text):
        return True

    # Extra vertical spacing between the two lines.  The threshold is
    # relative to the line pitch of the page, so that a document with a
    # large line spacing is not split into one paragraph per line.
    if pitch > 0:
        if (line.y0 - prev.y0) > pitch * PARA_GAP_PITCH_RATIO:
            return True
    else:
        gap = line.y0 - prev.y1
        if gap > max(prev.height, line.height, 1.0) * LINE_GAP_RATIO:
            return True

    # A different left indent means a new paragraph (or a new column).
    if abs(line.x0 - prev.x0) > INDENT_RATIO * max(line.size, 1.0):
        return True

    # The previous line ended well before the right edge of the column and
    # the next line starts again at the left margin: the paragraph is over.
    if not _ends_sentence(prev.text):
        right_edge = _column_right_edge(prev, bands)
        column = right_edge - prev.x0
        if column > 0 and prev.width < SHORT_LINE_RATIO * column:
            if abs(line.x0 - prev.x0) <= 0.4 * max(line.size, 1.0):
                if _ends_after_short_line(prev, following, bands):
                    return True

    return False


def _ends_after_short_line(
    prev: PdfLine,
    following: PdfLine | None,
    bands: list[tuple[float, float]],
) -> bool:
    """True when a short line ends the paragraph.

    A short line only ends the paragraph when the line after it fills the
    column again - two short lines in a row belong to the same paragraph.
    """
    if following is None:
        # The paragraph may be cut by the page break, the decision is left
        # to the page break handling.
        return False
    right_edge = _column_right_edge(following, bands)
    column = right_edge - following.x0
    if column <= 0:
        return True
    return following.width >= SHORT_LINE_RATIO * column


def _is_field_line(text: str) -> bool:
    """True when the line looks like ``标签：内容``.

    Only a labelled line counts: the label has to contain a CJK ideograph or
    a letter ("12:30" and "http://..." are values, not field labels).
    """
    match = _FIELD_LABEL_RE.match(text or "")
    if match is None:
        return False
    label = match.group(1)
    if not any(is_cjk(char) or char.isalpha() for char in label):
        return False
    # A URL is not a "label: value" pair.
    return not text[match.end():].startswith("//")


def _ends_sentence(text: str, next_text: str = "") -> bool:
    """True when ``text`` ends with a sentence terminator."""
    stripped = (text or "").rstrip()
    if not stripped:
        return True
    last = stripped[-1]
    if last in _STRONG_TERMINATORS:
        return True
    if last == ".":
        # A number that was cut in the middle of a line ("1200000." followed
        # by "00" on the next line) is not the end of a sentence.  A line
        # that ends with a number and is followed by a word is.
        if re.search(r"[0-9]\s*\.$", stripped) and (next_text or "").strip()[:1].isdigit():
            return False
        # "3." or "e.g." are not sentence ends.
        match = re.search(r"([A-Za-z.]+)\.$", stripped)
        if match and match.group(1).lower() in _ABBREVIATIONS:
            return False
        return True
    return False


def _make_paragraph(lines: list[PdfLine], body_size: float) -> PdfParagraph:
    text = ""
    for line in lines:
        text = _join(text, line.text)
    text = text.strip()

    size = max((line.size for line in lines), default=0.0)
    bold = any(line.bold for line in lines)
    kind = "paragraph"
    level: int | None = None

    if _is_heading(text, size, bold, body_size):
        kind = "heading"
        level = _heading_level(text)
    elif any(_BULLET_RE.match(line.text.strip()) for line in lines):
        kind = "list"

    bbox = [
        min(line.x0 for line in lines),
        min(line.y0 for line in lines),
        max(line.x1 for line in lines),
        max(line.y1 for line in lines),
    ]
    pages: list[int] = []
    for line in lines:
        if not pages or pages[-1] != line.page:
            pages.append(line.page)

    return PdfParagraph(
        text=text,
        pages=pages,
        bbox=bbox,
        kind=kind,
        level=level,
        size=size,
        bold=bold,
        lines=list(lines),
    )


def _is_heading(text: str, size: float, bold: bool, body_size: float) -> bool:
    """Decide whether a paragraph is a heading."""
    if not text:
        return False
    if body_size and size >= body_size * HEADING_SIZE_RATIO:
        return True
    # A chapter number plus a short text is a heading even when it uses the
    # body font size (Chinese official documents do that a lot).
    if (
        len(text) <= HEADING_MAX_LENGTH
        and not _ends_sentence(text)
        and _CHAPTER_HEADING_RE.match(text)
        and _numeric_prefix_is_heading(text)
        and (bold or not body_size or size >= body_size * 0.95)
    ):
        return True
    return False


def _numeric_prefix_is_heading(text: str) -> bool:
    """False when a numeric prefix belongs to a number, not to a heading.

    "1.2 概述" is a heading, "8.6%，同比增长" is a sentence that happens to
    start with a number.
    """
    match = _NUMERIC_HEADING_RE.match(text)
    if match is None:
        return True
    rest = text[match.end():]
    if not rest:
        return True
    first = rest[0]
    if first.isspace() or first in "、":
        return True
    # Without a separator the rest has to be a word, not a number.
    if first.isdigit() or first in _NUMBER_LIKE:
        return False
    return True


def join_lines(left: str, right: str) -> str:
    """Join two lines of a paragraph (public alias of :func:`_join`)."""
    return _join(left, right)


def _join(left: str, right: str) -> str:
    """Join two fragments of the same paragraph."""
    if not left:
        return right
    if not right:
        return left
    if left.endswith("-") and len(left) > 1 and _is_latin(left[-2]):
        # Word hyphenated across two lines.
        return left + right
    if left[-1].isspace() or right[0].isspace():
        # The line already carries the spacing.
        return left + right
    if is_cjk(left[-1]) and is_cjk(right[0]):
        return left + right
    if is_cjk(left[-1]) and right[0].isascii() and right[0].isalnum():
        # CJK text followed by a number or a word keeps the usual space.
        return left + " " + right
    if is_cjk(right[0]) and left[-1].isascii() and left[-1].isalnum():
        return left + " " + right
    return left + " " + right


def _heading_level(text: str) -> int | None:
    """Derive the heading level from the numbering of the heading."""
    if _CHAPTER_RE.match(text):
        return 1
    match = _C_NUMBER_RE.match(text)
    if match:
        return len(match.group(1).split("."))
    if _PAREN_CN_RE.match(text):
        return 2
    if _PAREN_NUM_RE.match(text):
        return 4
    match = _CN_NUMBER_RE.match(text)
    if match:
        return 1
    return None


def assign_heading_levels(paragraphs: list[PdfParagraph]) -> None:
    """Give every heading a level, based on its numbering or its font size.

    Headings that carry a chapter number know their level already.  The
    remaining ones (a document title for example) are ranked by font size,
    the biggest one being level 1.
    """
    headings = [p for p in paragraphs if p.kind == "heading"]
    if not headings:
        return

    numbered = [p for p in headings if p.level is not None]
    if not numbered:
        sizes = sorted({round(p.size, 1) for p in headings}, reverse=True)
        for paragraph in headings:
            try:
                paragraph.level = sizes.index(round(paragraph.size, 1)) + 1
            except ValueError:  # pragma: no cover - defensive
                paragraph.level = 1
        return

    # The size of the headings that know their level tells how the other
    # sizes have to be ranked: bigger than the biggest known one is level 1,
    # smaller than the smallest known one is one level deeper.
    known: dict[float, int] = {}
    for paragraph in numbered:
        size = round(paragraph.size, 1)
        level = paragraph.level or 1
        known[size] = min(known.get(size, level), level)
    biggest = max(known)
    smallest = min(known)
    deepest = max(known.values())

    for paragraph in headings:
        if paragraph.level is not None:
            continue
        size = round(paragraph.size, 1)
        if size > biggest:
            paragraph.level = 1
        elif size < smallest:
            paragraph.level = deepest + 1
        else:
            paragraph.level = deepest


def merge_across_pages(
    paragraphs: list[PdfParagraph],
    body_size: float = 0.0,
    page_extents: dict[int, tuple[float, float]] | None = None,
    blocked_pages: set[int] | None = None,
) -> list[PdfParagraph]:
    """Join a paragraph that was split by a page break (PRD 8.2).

    The last paragraph of a page and the first paragraph of the next page
    belong together when the first one does not end with a sentence, its last
    line reaches the right edge of the column (the paragraph was cut in the
    middle) and the second one continues with the same typography.

    ``blocked_pages`` lists the pages whose last paragraph must *not* be
    joined with the next page - a page that ends with a table for example:
    the text after the table is a new paragraph, not the continuation of the
    text above it.
    """
    extent = page_extents or {}
    blocked = blocked_pages or set()
    merged: list[PdfParagraph] = []
    for paragraph in paragraphs:
        if merged:
            previous = merged[-1]
            if (
                previous.pages
                and previous.pages[-1] not in blocked
                and _continues_across_page(previous, paragraph, body_size, extent)
            ):
                previous.text = _join(previous.text, paragraph.text)
                previous.lines.extend(paragraph.lines)
                previous.bbox = [
                    min(previous.bbox[0], paragraph.bbox[0]),
                    min(previous.bbox[1], paragraph.bbox[1]),
                    max(previous.bbox[2], paragraph.bbox[2]),
                    max(previous.bbox[3], paragraph.bbox[3]),
                ]
                for page in paragraph.pages:
                    if page not in previous.pages:
                        previous.pages.append(page)
                continue
        merged.append(paragraph)
    return merged


def _continues_across_page(
    previous: PdfParagraph,
    paragraph: PdfParagraph,
    body_size: float,
    page_extents: dict[int, tuple[float, float]],
) -> bool:
    """True when ``paragraph`` continues ``previous`` on the next page."""
    if previous.kind != "paragraph" or paragraph.kind != "paragraph":
        return False
    # Only a paragraph that was cut by a page break may be joined.
    if not previous.pages or not paragraph.pages:
        return False
    if previous.pages[-1] == paragraph.pages[0]:
        return False
    if not previous.lines or not paragraph.lines:
        return False
    if _ends_sentence(previous.text):
        return False
    # The last line of the previous paragraph has to fill the column,
    # otherwise the paragraph ended before the page break.
    extent = page_extents.get(previous.pages[-1]) or (0.0, 0.0)
    text_width, page_width = extent
    last_line = previous.lines[-1]
    if text_width:
        column = text_width - min(line.x0 for line in previous.lines)
        if column > 0 and last_line.width < SHORT_LINE_RATIO * column:
            return False
    if page_width and last_line.width < 0.35 * page_width:
        # A short line at the end of a page is the end of a paragraph.
        return False
    if abs(previous.size - paragraph.size) > 0.6:
        return False
    last_line = previous.lines[-1]
    first_line = paragraph.lines[0]
    if abs(last_line.x0 - first_line.x0) > INDENT_RATIO * max(first_line.size, 1.0):
        return False
    if body_size and first_line.size < body_size * 0.9:
        return False
    return True


# --------------------------------------------------------------------------
# 4. Tables without ruling lines (PRD 8.3)
# --------------------------------------------------------------------------


@dataclass
class _Cell:
    """A cell of a table that was recognised through its text alignment."""

    text: str
    x0: float
    x1: float


def detect_text_tables(
    page,
    min_rows: int = 3,
    min_columns: int = 2,
) -> list[DetectedTable]:
    """Recognise tables that consist of aligned text columns only.

    A PDF table that was exported from a spreadsheet often has no ruling
    lines at all - the columns are only visible through the alignment of the
    text.  This function groups the words of the page into rows, splits the
    rows into cells and keeps the runs of rows that share their column
    layout.

    The recognition is deliberately strict: a paragraph whose lines happen
    to start at the same left edge is *not* a table.  Every column has to
    be aligned, the run needs at least three rows and the cells must not
    be as wide as the page.
    """
    try:
        words = page.get_text("words") or []
    except Exception:  # noqa: BLE001 - table detection is best effort
        return []

    page_width, _page_height = page_text_size(page)

    entries: list[tuple[float, float, float, float, str]] = []
    for word in words:
        if len(word) < 5:
            continue
        text = (word[4] or "").strip()
        if not text:
            continue
        entries.append((float(word[0]), float(word[1]), float(word[2]), float(word[3]), text))
    if len(entries) < min_rows * min_columns:
        return []

    entries.sort(key=lambda item: (round(item[1], 1), item[0]))
    heights = sorted(item[3] - item[1] for item in entries)
    median_height = heights[len(heights) // 2] or 10.0
    gap_threshold = max(6.0, median_height * 1.2)

    rows = _group_words_into_rows(entries, median_height)
    cells = [_split_row(row, gap_threshold) for row in rows]

    tables: list[DetectedTable] = []
    index = 0
    while index < len(rows):
        column_count = len(cells[index])
        if column_count < min_columns:
            index += 1
            continue

        run = [index]
        cursor = index + 1
        while cursor < len(rows):
            if len(cells[cursor]) != column_count:
                break
            # Rows of a table sit close to each other.
            previous_bottom = rows[run[-1]][0][3]
            if rows[cursor][0][1] - previous_bottom > max(3 * median_height, 40):
                break
            run.append(cursor)
            cursor += 1

        if (
            len(run) >= min_rows
            and _columns_align(rows, run, cells)
            and not _is_list_run(cells, run)
            and _cells_are_narrow(cells, run, page_width)
        ):
            table_rows = [
                [cell.text for cell in cells[position]] for position in run
            ]
            bbox = [
                min(row[0][0] for row in (rows[position] for position in run)),
                min(row[0][1] for row in (rows[position] for position in run)),
                max(row[-1][2] for row in (rows[position] for position in run)),
                max(row[-1][3] for row in (rows[position] for position in run)),
            ]
            tables.append(DetectedTable(rows=table_rows, bbox=bbox))
            index = cursor
        else:
            index += 1

    return tables


def _group_words_into_rows(
    entries: list[tuple[float, float, float, float, str]],
    median_height: float,
) -> list[list[tuple[float, float, float, float, str]]]:
    """Group the words of a page into visual rows."""
    tolerance = max(2.0, median_height * 0.6)
    rows: list[list[tuple[float, float, float, float, str]]] = []
    current: list[tuple[float, float, float, float, str]] = []
    center: float | None = None

    for entry in entries:
        entry_center = (entry[1] + entry[3]) / 2
        if center is None or abs(entry_center - center) <= tolerance:
            current.append(entry)
            if center is None:
                center = entry_center
            else:
                center = sum((item[1] + item[3]) / 2 for item in current) / len(current)
        else:
            rows.append(current)
            current = [entry]
            center = entry_center
    if current:
        rows.append(current)
    return rows


def _split_row(
    row: list[tuple[float, float, float, float, str]],
    gap_threshold: float,
) -> list[_Cell]:
    """Split a row into cells, using the horizontal gaps between the words."""
    groups: list[list[tuple[float, float, float, float, str]]] = []
    for entry in sorted(row, key=lambda item: item[0]):
        if groups and entry[0] - groups[-1][-1][2] <= gap_threshold:
            groups[-1].append(entry)
        else:
            groups.append([entry])
    return [
        _Cell(
            text=" ".join(item[4] for item in group).strip(),
            x0=group[0][0],
            x1=max(item[2] for item in group),
        )
        for group in groups
    ]


def _columns_align(
    rows: list[list[tuple[float, float, float, float, str]]],
    run: list[int],
    cells: list[list[_Cell]],
) -> bool:
    """True when every column of the rows starts at the same place.

    Checking the first column only was not enough: a justified paragraph
    whose lines start at the same left edge was recognised as a table.  The
    cells of a row must also not overlap - that is what tells a table from
    a text that merely looks like two columns.
    """
    if not run:
        return False
    column_count = len(cells[run[0]])
    for column in range(column_count):
        starts = [cells[position][column].x0 for position in run]
        average_start = sum(starts) / len(starts)
        if any(abs(start - average_start) > 8.0 for start in starts):
            return False

    for position in run:
        row_cells = cells[position]
        for index in range(len(row_cells) - 1):
            if row_cells[index].x1 > row_cells[index + 1].x0 + 2.0:
                return False
    return True


def _is_list_run(cells: list[list[_Cell]], run: list[int]) -> bool:
    """True when the first cell of every row is a bullet or a number.

    A list is not a table, even when its items happen to line up.
    """
    for position in run:
        if not cells[position]:
            continue
        if _BULLET_RE.match(cells[position][0].text):
            return True
    return False


def _cells_are_narrow(
    cells: list[list[_Cell]],
    run: list[int],
    page_width: float,
) -> bool:
    """True when the cells are much narrower than the page.

    A table whose cells fill the whole width of the page is far more likely
    to be a paragraph that was split into "columns" by its spacing.
    """
    if not page_width:
        return True
    widths = [
        cell.x1 - cell.x0
        for position in run
        for cell in cells[position]
    ]
    if not widths:
        return True
    widths.sort()
    median = widths[len(widths) // 2]
    return median < page_width * 0.6
