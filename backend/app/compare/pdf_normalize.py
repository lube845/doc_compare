"""PDF 比对 · 归一化层（改造计划 阶段 3）.

The normalisation turns the character stream of a PDF into a text that can
be compared character by character.  The rule that makes it work: the
original characters are never modified, only *mapped*.  Every character of
the normalised text knows the character of the PDF it came from, so a
difference can always be turned back into a rectangle on the page.

The transformations, in the order they are applied:

1. NFKC, character by character (a character may expand, e.g. ``㎡`` →
   ``m2``; the mapping then points to the same original character twice),
2. the whitespace is removed (it is layout, not content),
3. a hyphen at the end of a line is removed (the word continues on the next
   line),
4. the typographic variants of a character are unified (curly quotes,
   dashes, ellipsis),
5. the optional rules of the configuration (case, punctuation, digits).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..domain.pdf_models import CharRef, ParsedPdf
from ..schemas import CompareConfig

#: characters that end a sentence
_SENTENCE_END = "。！？；…!?;"
#: english abbreviations that end with a dot but do not end a sentence
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
#: a sentence is never longer than this
MAX_SENTENCE_CHARS = 300
#: the characters a sentence may be cut at when it is too long
_SOFT_BREAK = "，,、；;：:"

#: typographic variants that are unified before the comparison
_PUNCTUATION_MAP = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "—": "-",
    "–": "-",
    "―": "-",
    "－": "-",
    "─": "-",
    "…": "...",
    "·": "·",
    "•": "·",
}

_PUNCT_RE = re.compile(
    r"[!-/:-@\[-`{-~ -⁯　-〿！-／：-＠［-｀｛-･]"
)


@dataclass(slots=True)
class Sentence:
    """A sentence of the normalised text."""

    index: int
    start: int
    end: int
    text: str

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(slots=True)
class NormStream:
    """The normalised text of a PDF and the mapping back to the page."""

    text: str
    #: ``index[i]`` is the position of the character of ``ParsedPdf.chars``
    #: that produced ``text[i]``
    index: list[int] = field(default_factory=list)
    #: ``gaps[i]`` is the number of whitespace characters that stood
    #: between ``text[i-1]`` and ``text[i]``.  The whitespace itself is not
    #: part of the text - it is layout - but the *place* where it stood is
    #: what tells the tokeniser that ``the project`` is two words and not
    #: one.
    gaps: list[int] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    chars: list[CharRef] = field(default_factory=list)
    #: the positions in the text where a table starts
    table_breaks: list[int] = field(default_factory=list)

    def char_range(self, start: int, end: int) -> tuple[int, int]:
        """The range of characters of the PDF that produced ``text[start:end]``."""
        if not self.index or start >= end:
            return (0, -1)
        first = self.index[start]
        last = self.index[min(end, len(self.index)) - 1]
        return (first, last)

    def block_at(self, position: int) -> str | None:
        """The logical block the character at ``position`` belongs to."""
        if not self.index or position >= len(self.index):
            return None
        char = self.chars[self.index[position]]
        return char.block_id

    def original_text(self, start: int, end: int) -> str:
        """The text of the PDF that produced ``text[start:end]``."""
        first, last = self.char_range(start, end)
        if last < first:
            return ""
        return "".join(char.ch for char in self.chars[first : last + 1])


def normalise_stream(
    parsed: ParsedPdf,
    config: CompareConfig | None = None,
) -> NormStream:
    """Normalise the character stream of a PDF."""
    config = config or CompareConfig()
    chars = parsed.chars

    text_parts: list[str] = []
    index: list[int] = []
    gaps: list[int] = []
    #: the number of whitespace characters dropped in front of the next one
    pending_gap = 0

    for position, char in enumerate(chars):
        glyph = char.ch
        if not glyph:
            continue

        # A running header or footer is layout, not content: it only takes
        # part in the comparison when the rules say so.
        if char.running and config.ignore_headers_footers:
            continue

        # -- 1. NFKC, character by character -----------------------------
        expanded = unicodedata.normalize(
            "NFKC" if config.ignore_full_width else "NFC", glyph
        )
        if not expanded:
            continue

        # -- 3. a hyphen at the end of a line ----------------------------
        if glyph == "-" and _ends_line(chars, position):
            previous = _previous_letter(chars, position)
            if previous is not None and previous.isascii() and previous.isalpha():
                continue

        # -- 2. the whitespace is layout, not content --------------------
        if config.ignore_whitespace and glyph.isspace():
            pending_gap += 1
            continue

        for piece in expanded:
            # -- 4. unify the typographic variants ------------------------
            piece = _PUNCTUATION_MAP.get(piece, piece)
            if config.ignore_case:
                piece = piece.lower()
            if config.ignore_punctuation and _PUNCT_RE.match(piece):
                continue
            text_parts.append(piece)
            index.append(position)
            gaps.append(pending_gap)
            pending_gap = 0

    text = "".join(text_parts)
    stream = NormStream(text=text, index=index, gaps=gaps, chars=chars)
    stream.sentences = split_sentences(stream)
    stream.table_breaks = [
        _position_of_char(stream, region.char_index)
        for region in parsed.table_regions
    ]
    return stream


def squeeze(text: str) -> str:
    """The text without the spaces.

    Some PDF producers put a space between every character of a number
    ("1 2 0 0 0 0 . 0 0").  The rules that recognise a number or a date
    have to see the text without them.
    """
    return "".join((text or "").split())


def _ends_line(chars: list[CharRef], position: int) -> bool:
    """True when the character is the last one of its rendered line."""
    if position + 1 >= len(chars):
        return True
    return chars[position + 1].line_id != chars[position].line_id


def _previous_letter(chars: list[CharRef], position: int) -> str | None:
    """The last non empty character of the line before ``position``."""
    line_id = chars[position].line_id
    for index in range(position - 1, -1, -1):
        if chars[index].line_id != line_id:
            return None
        if chars[index].ch.strip():
            return chars[index].ch
    return None


def _position_of_char(stream: NormStream, char_index: int) -> int:
    """The position in the normalised text of a character of the PDF."""
    if char_index <= 0:
        return 0
    # The normalised text is built in order, so the index list is
    # non-decreasing: the first position that points at the character or
    # at a later one is the place of the character in the text.
    return _bisect_index(stream.index, char_index)


def _bisect_index(index: list[int], char_index: int) -> int:
    """The first position of ``index`` whose value is at least ``char_index``."""
    low, high = 0, len(index)
    while low < high:
        middle = (low + high) // 2
        if index[middle] < char_index:
            low = middle + 1
        else:
            high = middle
    return low


def split_sentences(stream: NormStream) -> list[Sentence]:
    """Cut the normalised text into sentences.

    A sentence ends at a full stop, a question mark, an exclamation mark
    or a semicolon.  A block boundary always ends a sentence, so that a
    sentence never spans two paragraphs - the paragraphs are what the
    reader sees, the sentences are what the comparison aligns.
    """
    text = stream.text
    if not text:
        return []

    sentences: list[Sentence] = []
    start = 0
    position = 0
    length = len(text)

    def close(end: int) -> None:
        nonlocal start
        if end > start:
            sentences.append(
                Sentence(
                    index=len(sentences),
                    start=start,
                    end=end,
                    text=text[start:end],
                )
            )
            start = end

    while position < length:
        char = text[position]

        if char in _SENTENCE_END:
            close(position + 1)
            position += 1
            start = position
            continue

        if char == ".":
            if _is_dot_terminator(text, position):
                close(position + 1)
                position += 1
                start = position
                continue

        # A paragraph boundary ends the sentence as well.
        if position + 1 < length:
            if stream.block_at(position) != stream.block_at(position + 1):
                close(position + 1)
                position += 1
                start = position
                continue

        # A sentence that grew too long is cut at the last soft break.
        if position - start >= MAX_SENTENCE_CHARS:
            cut = _last_soft_break(text, start, position)
            close(cut if cut > start else position + 1)
            start = cut if cut > start else position + 1
            position = start
            continue

        position += 1

    close(length)
    return sentences


def _is_dot_terminator(text: str, position: int) -> bool:
    """True when the dot at ``position`` ends a sentence."""
    following = text[position + 1] if position + 1 < len(text) else ""
    if following.isdigit() or following.isalpha():
        # A decimal number or a word ("e.g.").
        return False
    word = ""
    index = position - 1
    while index >= 0 and (text[index].isalpha() or text[index] == "."):
        word = text[index] + word
        index -= 1
        if len(word) > 8:
            break
    if word.lower().rstrip(".") in _ABBREVIATIONS:
        return False
    return True


def _last_soft_break(text: str, start: int, position: int) -> int:
    """The last position at which a too long sentence may be cut."""
    for index in range(position, start, -1):
        if text[index] in _SOFT_BREAK:
            return index + 1
    return 0
