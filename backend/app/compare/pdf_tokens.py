"""PDF 比对 · Token 化（V2 · 第六阶段）.

The unit of comparison of the V2 engine is a *token*, not a character:

* a Chinese character is one token,
* a run of latin letters is one token (``project``),
* a number is one token (``1200000.00``),
* a punctuation mark is one token.

The tokeniser never uses a Chinese word segmenter (jieba): the result of a
segmenter depends on its dictionary, and two documents that carry the same
text would then produce two different token sequences - the very thing the
comparison has to avoid.  A single Chinese character is stable, needs no
dictionary and is precise enough for a diff (V2 §6.2).

Every token keeps the reference to the characters of the PDF it was built
from, so a difference can always be turned back into a rectangle on the
page (V2 §6.4).  The coordinates are *never* looked up again with a text
search: a text that occurs three times in a document cannot be located by
searching it.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .pdf_normalize import NormStream

#: the code ranges that hold a character which is written on its own
_WIDE_RANGES = (
    (0x1100, 0x115F),  # hangul jamo
    (0x2E80, 0x303E),  # cjk radicals, kangxi, cjk symbols
    (0x3041, 0x33FF),  # kana, hangul compat, cjk compat
    (0x3400, 0x4DBF),  # cjk ext A
    (0x4E00, 0x9FFF),  # cjk unified
    (0xA000, 0xA4CF),  # yi
    (0xAC00, 0xD7A3),  # hangul syllables
    (0xF900, 0xFAFF),  # cjk compat ideographs
    (0x20000, 0x2FA1F),  # cjk ext B..
)


def _is_wide(char: str) -> bool:
    """True for a character that is written on its own (Chinese, kana, ...)."""
    code = ord(char)
    for low, high in _WIDE_RANGES:
        if low <= code <= high:
            return True
    return unicodedata.east_asian_width(char) in ("W", "F")


def _is_latin(char: str) -> bool:
    """True for a letter that belongs to a word (latin, cyrillic, greek)."""
    return char.isalpha() and not _is_wide(char)


@dataclass(slots=True)
class Token:
    """One token of the document and the characters it was built from."""

    #: the text that is compared
    text: str
    #: the position of the token in the normalised text
    start: int
    end: int
    #: the index of the first and of the last character of the PDF
    char_first: int
    char_last: int
    page: int
    #: the rendered line the token belongs to
    line_id: int
    #: the logical block (paragraph) the token belongs to
    block_id: str | None


@dataclass(slots=True)
class Paragraph:
    """A group of tokens that belong to the same logical block.

    A paragraph is only an *auxiliary* structure: it narrows the comparison
    and it gives the context of a difference, but it never decides whether
    two texts are equal (V2 §8.1).
    """

    index: int
    block_id: str | None
    page: int
    #: the range of the tokens of the paragraph
    first: int
    last: int
    text: str


@dataclass(slots=True)
class TokenStream:
    """The tokens of a document."""

    tokens: list[Token] = field(default_factory=list)
    #: the text of every token, ready for the sequence diff
    texts: list[str] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.tokens)

    def paragraph_of(self, position: int) -> Paragraph | None:
        """The paragraph the token at ``position`` belongs to."""
        if not self.paragraphs:
            return None
        low, high = 0, len(self.paragraphs) - 1
        while low <= high:
            middle = (low + high) // 2
            paragraph = self.paragraphs[middle]
            if position < paragraph.first:
                high = middle - 1
            elif position > paragraph.last:
                low = middle + 1
            else:
                return paragraph
        return None


def tokenize(stream: NormStream, keep_whitespace: bool = False) -> TokenStream:
    """Turn the normalised text of a PDF into a sequence of tokens."""
    text = stream.text
    gaps = stream.gaps
    index = stream.index
    chars = stream.chars

    # The gap that separates two words of this document.  A PDF that writes
    # the latin letters in a wide font puts a space between every letter, a
    # PDF that writes them in a narrow font puts none: the *usual* gap of
    # the document is therefore what tells a word from a letter (V2 §6.2).
    threshold = _word_gap(text, gaps)

    tokens: list[Token] = []
    texts: list[str] = []

    position = 0
    length = len(text)
    while position < length:
        char = text[position]
        if char.isspace():
            if not keep_whitespace:
                position += 1
                continue
            end = position + 1
        else:
            end = _scan_token(text, gaps, position, threshold)

        char_first = index[position]
        char_last = index[end - 1]
        source = chars[char_first]
        tokens.append(
            Token(
                text=text[position:end],
                start=position,
                end=end,
                char_first=char_first,
                char_last=char_last,
                page=source.page,
                line_id=source.line_id,
                block_id=source.block_id,
            )
        )
        texts.append(text[position:end])
        position = end

    paragraphs = _build_paragraphs(tokens, texts)
    return TokenStream(tokens=tokens, texts=texts, paragraphs=paragraphs)


def _word_gap(text: str, gaps: list[int]) -> int:
    """The gap that separates two words of the document.

    It is the gap that stands between two letters most of the time.  In a
    document that is written in a narrow font it is zero, in a document
    that is written in a wide (chinese) font it is one - and the word
    itself is the gap that is one step bigger.
    """
    counts: dict[int, int] = {}
    for position in range(len(text) - 1):
        if not (_is_latin(text[position]) and _is_latin(text[position + 1])):
            continue
        value = gaps[position + 1]
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return 0
    return max(counts.items(), key=lambda item: (item[1], -item[0]))[0]


def _scan_token(text: str, gaps: list[int], start: int, threshold: int) -> int:
    """The position just behind the token that starts at ``start``."""
    char = text[start]
    length = len(text)

    # A Chinese character is a token of its own.
    if _is_wide(char):
        return start + 1

    # A number is a token: "1200000.00" is one token, not five.
    if char.isdigit():
        return _scan_number(text, start)

    # A word is a token.  The gap that separates two words ends it.
    if _is_latin(char):
        end = start + 1
        while end < length and _is_latin(text[end]) and gaps[end] <= threshold:
            end += 1
        return end

    # Everything else (punctuation, symbols) is a token of its own.
    return start + 1


def _scan_number(text: str, start: int) -> int:
    """The position just behind the number that starts at ``start``."""
    length = len(text)
    end = start
    while end < length and text[end].isdigit():
        end += 1
    # A thousands separator and a decimal point belong to the number.
    while end < length and text[end] in ".,":
        if end + 1 < length and text[end + 1].isdigit():
            end += 1
            while end < length and text[end].isdigit():
                end += 1
        else:
            break
    return end


def _build_paragraphs(tokens: list[Token], texts: list[str]) -> list[Paragraph]:
    """Group the tokens that belong to the same logical block."""
    paragraphs: list[Paragraph] = []
    if not tokens:
        return paragraphs

    start = 0
    current = tokens[0].block_id
    for position in range(1, len(tokens) + 1):
        if position < len(tokens) and tokens[position].block_id == current:
            continue
        paragraphs.append(
            Paragraph(
                index=len(paragraphs),
                block_id=current,
                page=tokens[start].page,
                first=start,
                last=position - 1,
                text="".join(texts[start:position]),
            )
        )
        if position < len(tokens):
            start = position
            current = tokens[position].block_id
    return paragraphs
