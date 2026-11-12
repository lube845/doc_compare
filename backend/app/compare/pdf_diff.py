"""PDF 比对 · 差异计算（V2 · 第八、九、十阶段）.

The comparison works on the token sequences of the two documents:

1. the global alignment (:mod:`app.compare.pdf_anchor`) splits the two
   documents into the regions that correspond to each other and into the
   regions that only exist on one side,
2. every region that is not known to be identical is compared token by
   token (V2 §8.3),
3. the differences are grouped, refined, turned into rectangles on the
   pages (V2 §9) and finally into the changes the reader sees (V2 §10).

The rules that turn the hunks into changes are the ones of the plan:

* two changes that are separated by at most ``merge_gap_chars`` characters
  and that belong to the same paragraph belong to the same change,
* a text that only differs in its spaces is a change of the layout and not
  of the content (V2 §5.2) - a PDF that writes the latin letters in a
  wide font must not look like a document that was rewritten,
* a text that was only moved is reported as a move, not as a deletion and
  an addition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..parsers.pdf_layout import join_lines
from ..schemas import CompareConfig
from .pdf_anchor import align_tokens
from .pdf_normalize import NormStream, squeeze
from .pdf_tokens import TokenStream, tokenize

#: below this confidence a change has to be checked by a human
#: (the other thresholds live in :class:`app.schemas.CompareConfig`)
LOW_CONF_THRESHOLD = 0.6
#: the maximum number of rectangles of a single change
MAX_RECTS = 50
#: the characters that end a sentence (used to cut the context)
_SENTENCE_END = "。！？；!?;"
#: the characters a change is extended over when it lies inside a number
#: or inside a date: a change of "2" in "1200000.00" concerns the whole
#: number, not the single digit
_EXTEND_CHARS = set("0123456789.,:%％年月日元件万亿¥￥$€£")

#: the patterns that make a change worth a tag
_TAG_RULES: tuple[tuple[str, str], ...] = (
    ("amount", r"[¥￥$€£]\s?[\d,.]+|\d[\d,.]*\s*(?:万元|亿元|元|万美元|欧元)"),
    ("date", r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}"),
    ("ratio", r"\d+(?:\.\d+)?\s*[%％]"),
    ("number", r"\d[\d,.]*"),
)


@dataclass(slots=True)
class PdfDiffItem:
    """One difference between two PDFs."""

    kind: str  # insert | delete | replace | move
    old_text: str = ""
    new_text: str = ""
    old_rects: list[list[float]] = field(default_factory=list)
    new_rects: list[list[float]] = field(default_factory=list)
    #: the page every rectangle of ``old_rects`` / ``new_rects`` belongs to.
    #: A difference that runs over a page break has its rectangles on two
    #: pages, and the reader has to know which one is which (V2 §9.3).
    old_rect_pages: list[int] = field(default_factory=list)
    new_rect_pages: list[int] = field(default_factory=list)
    old_page: int | None = None
    new_page: int | None = None
    old_block: str | None = None
    new_block: str | None = None
    old_line: int | None = None
    new_line: int | None = None
    #: the index of the paragraph the change belongs to
    old_paragraph: int | None = None
    new_paragraph: int | None = None
    fragments: list[dict] = field(default_factory=list)
    confidence: float = 1.0
    context_before: str = ""
    context_after: str = ""
    tags: list[str] = field(default_factory=list)
    #: ``paragraph`` / ``line`` / ``text`` - how much of the paragraph the
    #: change covers (V2 §8.2)
    scope: str = "text"
    order: int = 0


@dataclass(slots=True)
class _Hunk:
    """A part of the difference between two texts."""

    tag: str
    a_start: int
    a_end: int
    b_start: int
    b_end: int


@dataclass(slots=True)
class _Span:
    """A range of characters of the PDF."""

    first: int
    last: int


def diff_streams(
    stream_a: NormStream,
    stream_b: NormStream,
    quality_a: dict[int, str] | None = None,
    quality_b: dict[int, str] | None = None,
    config: CompareConfig | None = None,
) -> tuple[list[PdfDiffItem], TokenStream, TokenStream, list]:
    """Compare two normalised documents.

    The token sequences and the aligned segments of the two documents are
    returned as well: the paragraph layer needs the tokens to say which
    paragraph of the other document belongs to a difference, and the page
    map of the viewer is built from the segments.
    """
    config = config or CompareConfig()

    tokens_a = tokenize(stream_a, keep_whitespace=not config.ignore_whitespace)
    tokens_b = tokenize(stream_b, keep_whitespace=not config.ignore_whitespace)

    segments = align_tokens(tokens_a, tokens_b, config.pdf_anchor_shingle)

    items: list[PdfDiffItem] = []
    order = 0
    for segment in segments:
        if segment.identical:
            # The two parts are known to be the same.
            continue
        for item in _diff_segment(stream_a, stream_b, tokens_a, tokens_b, segment, config):
            item.order = order
            order += 1
            items.append(item)

    _detect_moves(items, config)
    items = [item for item in items if item.kind != "consumed"]
    _apply_quality(items, quality_a or {}, quality_b or {})
    return items, tokens_a, tokens_b, segments


# --------------------------------------------------------------------------
# One region
# --------------------------------------------------------------------------


def _diff_segment(
    stream_a: NormStream,
    stream_b: NormStream,
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    segment,
    config: CompareConfig,
) -> list[PdfDiffItem]:
    """Turn one region of the alignment into the changes it contains."""
    texts_a = tokens_a.texts[segment.a_start : segment.a_end]
    texts_b = tokens_b.texts[segment.b_start : segment.b_end]

    # Two regions that carry the same text are the same, whatever the
    # tokeniser made of the spaces between the words.
    if _region_text(stream_a, tokens_a, segment.a_start, segment.a_end) == _region_text(
        stream_b, tokens_b, segment.b_start, segment.b_end
    ):
        return []

    matcher = SequenceMatcher(None, texts_a, texts_b, autojunk=False)
    hunks: list[_Hunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append(
            _Hunk(
                tag=tag,
                a_start=segment.a_start + i1,
                a_end=segment.a_start + i2,
                b_start=segment.b_start + j1,
                b_end=segment.b_start + j2,
            )
        )

    # A change of "6" in "2024年6月30日" concerns the whole date, not the
    # single digit: the reader wants to see which day changed.
    hunks = [
        _extend_hunk(hunk, stream_a, tokens_a, stream_b, tokens_b)
        for hunk in hunks
    ]

    groups = _group_hunks(hunks, tokens_a, tokens_b, config)

    items: list[PdfDiffItem] = []
    for group in groups:
        old_span, new_span = _spans_of(group, tokens_a, tokens_b)
        for refined_old, refined_new in _refine(
            stream_a, stream_b, old_span, new_span, config
        ):
            item = PdfDiffItem(kind="replace")
            _fill(
                item,
                stream_a,
                stream_b,
                tokens_a,
                tokens_b,
                group,
                refined_old,
                refined_new,
            )
            if _is_layout_only(item):
                # The two texts only differ in their spaces.
                continue
            items.append(item)
    return items


def _region_text(
    stream: NormStream,
    tokens: TokenStream,
    start: int,
    end: int,
) -> str:
    """The normalised text of a range of tokens."""
    if start >= end or start >= len(tokens.tokens):
        return ""
    last = min(end, len(tokens.tokens)) - 1
    return stream.text[tokens.tokens[start].start : tokens.tokens[last].end]


def _is_layout_only(item: PdfDiffItem) -> bool:
    """True when the two texts only differ in their whitespace.

    A change of the spaces is a change of the layout, not of the content
    (V2 §5.2).  It happens for instance when the same text is written in a
    wide font: the extractor then puts a space between every letter.
    """
    if not item.old_text and not item.new_text:
        return True
    return squeeze(item.old_text) == squeeze(item.new_text)


def _extend_hunk(
    hunk: _Hunk,
    stream_a: NormStream,
    tokens_a: TokenStream,
    stream_b: NormStream,
    tokens_b: TokenStream,
) -> _Hunk:
    """Extend a change over the number or the date it belongs to.

    The extension is made on the normalised text, where the spaces are
    gone: a number that the PDF wrote as "1 2 0 0 0 0 . 0 0" is one
    number there.  Each side is extended in its own document: the two
    documents have two different token sequences.
    """
    a_start, a_end = _extend_side(stream_a, tokens_a, hunk.a_start, hunk.a_end)
    b_start, b_end = _extend_side(stream_b, tokens_b, hunk.b_start, hunk.b_end)
    return _Hunk(tag=hunk.tag, a_start=a_start, a_end=a_end, b_start=b_start, b_end=b_end)


def _extend_side(
    stream: NormStream,
    tokens: TokenStream,
    start: int,
    end: int,
) -> tuple[int, int]:
    """The range of tokens that holds the number or the date of a change."""
    if start >= end or start >= len(tokens.tokens):
        return start, end

    text = stream.text
    first = tokens.tokens[start].start
    last = tokens.tokens[min(end, len(tokens.tokens)) - 1].end
    new_first, new_last = _extend_range(text, first, last)
    if (new_first, new_last) == (first, last) or new_last <= new_first:
        return start, end

    new_start = _token_index(tokens, new_first)
    new_end = _token_index(tokens, new_last - 1) + 1

    # The extension must not leave the paragraph of the change: the period
    # at the end of the previous paragraph is not part of the number that
    # starts the next one.
    if tokens.tokens[new_start].block_id != tokens.tokens[start].block_id:
        new_start = start
    if tokens.tokens[new_end - 1].block_id != tokens.tokens[end - 1].block_id:
        new_end = end
    return new_start, new_end


def _token_index(tokens: TokenStream, position: int) -> int:
    """The token that holds a position of the normalised text."""
    low, high = 0, len(tokens.tokens) - 1
    while low < high:
        middle = (low + high) // 2
        if tokens.tokens[middle].end <= position:
            low = middle + 1
        else:
            high = middle
    return low


def _group_hunks(
    hunks: list[_Hunk],
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    config: CompareConfig,
) -> list[list[_Hunk]]:
    """Group the hunks that belong to the same change.

    Two changes that are separated by only a few characters belong to the
    same change - a reader would not see them as two either.  The changes
    of two different paragraphs are never grouped (V2 §10.2).
    """
    if not hunks:
        return []

    groups: list[list[_Hunk]] = []
    current: list[_Hunk] = [hunks[0]]

    for hunk in hunks[1:]:
        previous = current[-1]
        if (
            _same_paragraph(tokens_a, tokens_b, previous, hunk)
            and _gap(tokens_a, tokens_b, previous, hunk) <= config.merge_gap_chars
        ):
            current.append(hunk)
        else:
            groups.append(current)
            current = [hunk]

    groups.append(current)
    return groups


def _same_paragraph(
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    previous: _Hunk,
    hunk: _Hunk,
) -> bool:
    """True when the two hunks belong to the same paragraph.

    The block ids of the two documents live in two different namespaces
    (``b_3`` of A says nothing about ``b_3`` of B), so every document is
    checked on its own: the hunks are merged only when they fall into
    the same paragraph of A *and* into the same paragraph of B.
    """
    on_a = _block_of(tokens_a, previous.a_start, previous.a_end) == _block_of(
        tokens_a, hunk.a_start, hunk.a_end
    )
    on_b = _block_of(tokens_b, previous.b_start, previous.b_end) == _block_of(
        tokens_b, hunk.b_start, hunk.b_end
    )
    return on_a and on_b


def _block_of(tokens: TokenStream, start: int, end: int) -> str | None:
    """The logical block a range of tokens belongs to."""
    if start >= end or start >= len(tokens.tokens):
        return None
    return tokens.tokens[start].block_id


def _gap(
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    previous: _Hunk,
    hunk: _Hunk,
) -> int:
    """The number of characters between two hunks."""
    if previous.a_end >= hunk.a_start:
        return 0
    if previous.a_end < hunk.a_start and hunk.a_start < len(tokens_a.tokens):
        first = tokens_a.tokens[previous.a_end]
        second = tokens_a.tokens[hunk.a_start]
        return max(0, second.char_first - first.char_last - 1)
    if previous.b_end < hunk.b_start and hunk.b_start < len(tokens_b.tokens):
        first = tokens_b.tokens[previous.b_end]
        second = tokens_b.tokens[hunk.b_start]
        return max(0, second.char_first - first.char_last - 1)
    return 0


# --------------------------------------------------------------------------
# The change itself
# --------------------------------------------------------------------------


def _spans_of(
    group: list[_Hunk],
    tokens_a: TokenStream,
    tokens_b: TokenStream,
) -> tuple[_Span | None, _Span | None]:
    """The characters of the PDF that a group of hunks covers."""
    a_start = group[0].a_start
    a_end = group[-1].a_end
    b_start = group[0].b_start
    b_end = group[-1].b_end
    return (
        _span_of(tokens_a, a_start, a_end),
        _span_of(tokens_b, b_start, b_end),
    )


def _span_of(tokens: TokenStream, start: int, end: int) -> _Span | None:
    """The characters a range of tokens covers."""
    if start >= end or start >= len(tokens.tokens):
        return None
    last = min(end, len(tokens.tokens)) - 1
    return _Span(tokens.tokens[start].char_first, tokens.tokens[last].char_last)


def _refine(
    stream_a: NormStream,
    stream_b: NormStream,
    old_span: _Span | None,
    new_span: _Span | None,
    config: CompareConfig,
) -> list[tuple[_Span | None, _Span | None]]:
    """The precise difference of a group of hunks.

    The token diff compares the words of the two documents.  When the two
    documents write the same words with another spacing, the words are not
    the same any more and the difference is bigger than it really is.  The
    group is then compared again, character by character, which finds the
    part that really changed.
    """
    old_chars = _chars_of(stream_a, old_span)
    new_chars = _chars_of(stream_b, new_span)

    old_spaces = sum(1 for char in old_chars if char.ch.isspace())
    new_spaces = sum(1 for char in new_chars if char.ch.isspace())
    if old_spaces == new_spaces:
        # The spacing is the same on both sides: the token diff is exact.
        return [(old_span, new_span)]

    old_kept = [
        (offset, char)
        for offset, char in enumerate(old_chars)
        if not char.ch.isspace()
    ]
    new_kept = [
        (offset, char)
        for offset, char in enumerate(new_chars)
        if not char.ch.isspace()
    ]
    text_a = "".join(char.ch for _, char in old_kept)
    text_b = "".join(char.ch for _, char in new_kept)
    if text_a == text_b:
        # Only the layout is different.
        return []
    if not text_a or not text_b:
        return [(old_span, new_span)]

    matcher = SequenceMatcher(None, text_a, text_b, autojunk=False)
    hunks: list[tuple[int, int, int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append((i1, i2, j1, j2))
    if not hunks:
        return []

    changed = sum(i2 - i1 for i1, i2, _, _ in hunks) + sum(
        j2 - j1 for _, _, j1, j2 in hunks
    )
    if changed * 4 >= len(text_a) + len(text_b):
        # The character diff is not more precise than the token diff.
        return [(old_span, new_span)]

    # Two changes that are close to each other (the month and the year of
    # the same date) belong to the same change - otherwise the two of
    # them would both be extended over the whole date and the reader
    # would see the same date twice.
    merged: list[tuple[int, int, int, int]] = [hunks[0]]
    for hunk in hunks[1:]:
        previous = merged[-1]
        if (
            hunk[0] - previous[1] <= config.merge_gap_chars
            and hunk[2] - previous[3] <= config.merge_gap_chars
        ):
            merged[-1] = (previous[0], hunk[1], previous[2], hunk[3])
        else:
            merged.append(hunk)

    result: list[tuple[_Span | None, _Span | None]] = []
    for i1, i2, j1, j2 in merged:
        i1, i2 = _extend_range(text_a, i1, i2)
        j1, j2 = _extend_range(text_b, j1, j2)
        result.append(
            (
                _span_of_chars(old_span, old_kept, i1, i2),
                _span_of_chars(new_span, new_kept, j1, j2),
            )
        )
    return result


def _chars_of(stream: NormStream, span: _Span | None) -> list:
    """The characters a span covers."""
    if span is None:
        return []
    return list(stream.chars[span.first : span.last + 1])


def _span_of_chars(
    span: _Span | None,
    kept: list,
    first: int,
    last: int,
) -> _Span | None:
    """The span of the characters a range of the kept text covers."""
    if span is None or first >= last or first >= len(kept):
        return None
    last = min(last, len(kept)) - 1
    return _Span(span.first + kept[first][0], span.first + kept[last][0])


def _extend_range(text: str, start: int, end: int) -> tuple[int, int]:
    """The range of the number or of the date the range belongs to."""
    left = start
    while left > 0 and text[left - 1] in _EXTEND_CHARS:
        left -= 1
    right = end
    while right < len(text) and text[right] in _EXTEND_CHARS:
        right += 1
    # The range must not start or end in the middle of a number.
    while left < start and text[left] in ".,":
        left += 1
    while right > end and text[right - 1] in ".,":
        right -= 1
    return left, right


def _fill(
    item: PdfDiffItem,
    stream_a: NormStream,
    stream_b: NormStream,
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    group: list[_Hunk],
    old_span: _Span | None,
    new_span: _Span | None,
) -> None:
    """Fill the texts, the rectangles and the fragments of a change."""
    item.old_text = _text_of(stream_a, old_span)
    item.new_text = _text_of(stream_b, new_span)

    item.old_rects, item.old_rect_pages, item.old_page, item.old_block = _rects(
        stream_a, old_span
    )
    item.new_rects, item.new_rect_pages, item.new_page, item.new_block = _rects(
        stream_b, new_span
    )

    if old_span and new_span:
        item.kind = "replace"
    elif old_span:
        item.kind = "delete"
    else:
        item.kind = "insert"

    a_start = group[0].a_start
    a_end = group[-1].a_end
    b_start = group[0].b_start
    b_end = group[-1].b_end

    if a_start < a_end:
        item.old_line = tokens_a.tokens[a_start].line_id
        paragraph = tokens_a.paragraph_of(a_start)
        item.old_paragraph = paragraph.index if paragraph else None
    if b_start < b_end:
        item.new_line = tokens_b.tokens[b_start].line_id
        paragraph = tokens_b.paragraph_of(b_start)
        item.new_paragraph = paragraph.index if paragraph else None

    item.scope = _scope(tokens_a, tokens_b, a_start, a_end, b_start, b_end)

    item.fragments = [
        {"type": "delete", "a": item.old_text, "b": ""},
        {"type": "insert", "a": "", "b": item.new_text},
    ]
    item.tags = _tag(item.old_text, item.new_text)

    if a_start < a_end:
        item.context_before = _context_before(
            stream_a.text, _text_position(tokens_a, a_start)
        )
    if b_start < b_end:
        item.context_after = _context_after(
            stream_b.text, _text_position(tokens_b, b_end)
        )


def _text_position(tokens: TokenStream, position: int) -> int:
    """The position of a token in the normalised text."""
    if position >= len(tokens.tokens):
        return 0
    return tokens.tokens[position].start


def _text_of(stream: NormStream, span: _Span | None) -> str:
    """The text of the PDF that a span of characters covers.

    The lines of the span are joined the way the parser joins the lines of
    a paragraph: the change then reads exactly as the paragraph the reader
    sees, and the frontend can find the changed text inside the paragraph
    (V2 §11).
    """
    if span is None:
        return ""
    chars = stream.chars[span.first : span.last + 1]
    if not chars:
        return ""

    parts: list[str] = []
    line_id = chars[0].line_id
    start = 0
    for offset in range(1, len(chars) + 1):
        if offset < len(chars) and chars[offset].line_id == line_id:
            continue
        parts.append("".join(char.ch for char in chars[start:offset]))
        if offset < len(chars):
            line_id = chars[offset].line_id
            start = offset

    text = ""
    for part in parts:
        text = join_lines(text, part)
    return text.strip()


def _rects(
    stream: NormStream,
    span: _Span | None,
) -> tuple[list[list[float]], list[int], int | None, str | None]:
    """The rectangles on the pages that hold a span of characters.

    Every rectangle is given together with the page it stands on: a
    difference that runs over a page break has its boxes on two pages.
    """
    if span is None or span.last < span.first:
        return [], [], None, None

    chars = stream.chars[span.first : span.last + 1]
    if not chars:
        return [], [], None, None

    rects: list[list[float]] = []
    pages: list[int] = []
    current_line = None
    rect: list[float] | None = None

    for char in chars:
        if char.line_id != current_line:
            if rect is not None:
                rects.append(rect)
            current_line = char.line_id
            rect = [char.x0, char.y0, char.x1, char.y1]
            pages.append(char.page)
        else:
            rect[0] = min(rect[0], char.x0)
            rect[1] = min(rect[1], char.y0)
            rect[2] = max(rect[2], char.x1)
            rect[3] = max(rect[3], char.y1)
    if rect is not None:
        rects.append(rect)

    if len(rects) > MAX_RECTS:
        # Too many boxes to be useful: keep one box per page.
        rects, pages = _merge_per_page(rects, chars, pages)

    return rects, pages, chars[0].page, chars[0].block_id


def _merge_per_page(
    rects: list[list[float]],
    chars: list,
    pages: list[int],
) -> tuple[list[list[float]], list[int]]:
    """One box per page when a change covers too many lines."""
    merged: list[list[float]] = []
    merged_pages: list[int] = []
    for char in chars:
        if not merged or merged_pages[-1] != char.page:
            merged.append([char.x0, char.y0, char.x1, char.y1])
            merged_pages.append(char.page)
        else:
            merged[-1][0] = min(merged[-1][0], char.x0)
            merged[-1][1] = min(merged[-1][1], char.y0)
            merged[-1][2] = max(merged[-1][2], char.x1)
            merged[-1][3] = max(merged[-1][3], char.y1)
    return merged, merged_pages


# --------------------------------------------------------------------------
# How much of the text the change covers
# --------------------------------------------------------------------------


def _scope(
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    a_start: int,
    a_end: int,
    b_start: int,
    b_end: int,
) -> str:
    """``paragraph`` / ``line`` / ``text`` (V2 §8.2)."""
    if a_start < a_end and _covers_paragraph(tokens_a, a_start, a_end):
        return "paragraph"
    if b_start < b_end and _covers_paragraph(tokens_b, b_start, b_end):
        return "paragraph"
    if a_start < a_end and _covers_line(tokens_a, a_start, a_end):
        return "line"
    if b_start < b_end and _covers_line(tokens_b, b_start, b_end):
        return "line"
    return "text"


def _covers_paragraph(tokens: TokenStream, start: int, end: int) -> bool:
    paragraph = tokens.paragraph_of(start)
    return bool(paragraph and paragraph.first == start and paragraph.last == end - 1)


def _covers_line(tokens: TokenStream, start: int, end: int) -> bool:
    first, last = _line_range(tokens, start)
    return first == start and last == end - 1


def _line_range(tokens: TokenStream, position: int) -> tuple[int, int]:
    """The range of the tokens that stand on the same rendered line."""
    line_id = tokens.tokens[position].line_id
    start = position
    while start > 0 and tokens.tokens[start - 1].line_id == line_id:
        start -= 1
    last = position
    while last < len(tokens.tokens) - 1 and tokens.tokens[last + 1].line_id == line_id:
        last += 1
    return start, last


# --------------------------------------------------------------------------
# The context
# --------------------------------------------------------------------------


def _context_before(text: str, position: int, limit: int = 40) -> str:
    """The text that comes before the change."""
    if position <= 0:
        return ""
    chunk = text[max(0, position - limit) : position]
    for index, char in enumerate(chunk):
        if char in _SENTENCE_END:
            return chunk[index + 1 :]
    return chunk


def _context_after(text: str, position: int, limit: int = 40) -> str:
    """The text that comes after the change."""
    if position <= 0 or position >= len(text):
        return ""
    chunk = text[position : position + limit]
    for index, char in enumerate(chunk):
        if char in _SENTENCE_END:
            return chunk[: index + 1]
    return chunk


# --------------------------------------------------------------------------
# Moves
# --------------------------------------------------------------------------


def _detect_moves(items: list[PdfDiffItem], config: CompareConfig) -> None:
    """Turn a deletion and an addition of the same text into a move."""
    deletions = [item for item in items if item.kind == "delete" and item.old_text]
    additions = [item for item in items if item.kind == "insert" and item.new_text]
    if not deletions or not additions:
        return

    from .pdf_align import text_ratio

    used: set[int] = set()
    for deletion in deletions:
        best: tuple[float, int] | None = None
        for index, addition in enumerate(additions):
            if index in used:
                continue
            if abs(len(addition.new_text) - len(deletion.old_text)) > max(
                20, len(deletion.old_text) // 4
            ):
                continue
            score = text_ratio(deletion.old_text, addition.new_text)
            if score < config.move_min_similarity:
                continue
            if len(deletion.old_text) < config.move_min_length and score < 1.0:
                # A short text is only a move when it is exactly the same
                # text - otherwise two different short sentences could be
                # mistaken for one.
                continue
            if best is None or score > best[0]:
                best = (score, index)
        if best is None:
            continue
        used.add(best[1])
        addition = additions[best[1]]
        deletion.kind = "move"
        deletion.new_text = addition.new_text
        deletion.new_rects = addition.new_rects
        deletion.new_rect_pages = addition.new_rect_pages
        deletion.new_page = addition.new_page
        deletion.new_block = addition.new_block
        deletion.new_line = addition.new_line
        deletion.new_paragraph = addition.new_paragraph
        deletion.confidence = min(deletion.confidence, addition.confidence) * best[0]
        deletion.fragments = [
            {"type": "move", "a": deletion.old_text, "b": addition.new_text}
        ]
        addition.kind = "consumed"


# --------------------------------------------------------------------------
# Confidence and tags
# --------------------------------------------------------------------------


def _apply_quality(
    items: list[PdfDiffItem],
    quality_a: dict[int, str],
    quality_b: dict[int, str],
) -> None:
    """Lower the confidence of the changes on a page of poor quality."""
    for item in items:
        factor = 1.0
        for quality in (quality_a.get(item.old_page or -1), quality_b.get(item.new_page or -1)):
            if quality == "warn":
                factor = min(factor, 0.7)
            elif quality == "bad":
                factor = min(factor, 0.4)
        item.confidence = round(item.confidence * factor, 4)
        if item.confidence < LOW_CONF_THRESHOLD:
            item.tags.append("needs_review")


def _tag(text_a: str, text_b: str) -> list[str]:
    """Label a change with the kind of information it carries."""
    tags: list[str] = []
    plain_a = squeeze(text_a)
    plain_b = squeeze(text_b)
    for name, pattern in _TAG_RULES:
        regex = re.compile(pattern)
        if regex.search(plain_a) or regex.search(plain_b):
            tags.append(name)
    return tags
