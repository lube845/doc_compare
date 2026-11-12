"""Text normalisation and tokenisation (PRD chapters 5, 6.3, 7.6).

``normalise`` turns raw text into the canonical form used for equality and
similarity checks.  Which transformations apply is driven by the comparison
configuration (chapter 5.1), so the same code serves both "exact" and
"ignore formatting" comparisons.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

from ..schemas import CompareConfig

_WHITESPACE_RE = re.compile(r"\s+")
_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
#: whitespace touching CJK text.  PDF extraction and the East-Asian
#: typography rules of Word/LibreOffice insert spaces between a Chinese word
#: and an adjacent number ("100 万元"); the DOCX of the same document has no
#: such space.  Those spaces are layout artefacts, not content differences.
_CJK_SPACE_RE = re.compile(
    r"(?<=[一-鿿㐀-䶿豈-﫿])[^\S\r\n]+|[^\S\r\n]+(?=[一-鿿㐀-䶿豈-﫿])"
)
_PUNCT_RE = re.compile(
    r"[!-/:-@\[-`{-~ -⁯　-〿！-／：-＠［-｀｛-･]"
)
# Paragraph / list separators used by the DOC binary extractor.
_SPLIT_RE = re.compile(r"[\r\n]+")


def normalise(text: str | None, config: CompareConfig | None = None) -> str:
    """Normalise ``text`` according to the comparison configuration."""
    if not text:
        return ""
    config = config or CompareConfig()

    # NFKC folds full-width characters; when the user wants full-width and
    # half-width to stay different, use the non-compatibility form instead.
    form = "NFKC" if config.ignore_full_width else "NFC"
    value = unicodedata.normalize(form, text)
    # NBSP and friends are layout artefacts.
    value = value.replace(" ", " ").replace("　", " ")

    if config.ignore_whitespace:
        value = _CJK_SPACE_RE.sub("", value)
        value = _WHITESPACE_RE.sub(" ", value).strip()
    elif config.ignore_newlines:
        # Keep the whitespace as typed, but treat a line break as a space.
        value = _NEWLINE_RE.sub(" ", value)

    if config.ignore_punctuation:
        value = _PUNCT_RE.sub("", value)

    if config.ignore_case:
        value = value.lower()

    if config.ignore_full_width:
        value = value.translate(_FULLWIDTH_MAP)

    if config.ignore_numbering:
        value = _LEADING_NUMBER_RE.sub("", value)

    if config.ignore_whitespace:
        return _WHITESPACE_RE.sub(" ", value).strip()
    return value


_FULLWIDTH_MAP = {ord("（"): "(", ord("）"): ")"}
_LEADING_NUMBER_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百千]+\s*[章节条款项]|[0-9]+(?:\.[0-9]+)*[.、)]?)\s*"
)


def text_hash(text: str) -> str:
    """SHA256 of a normalised string - used for the level 1 exact match."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def split_paragraphs(text: str) -> list[str]:
    """Split a blob of text into non-empty paragraph candidates."""
    return [part.strip() for part in _SPLIT_RE.split(text) if part.strip()]


# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[.\-_/][A-Za-z0-9]+)*|\s+|.", re.S)

try:  # pragma: no cover - optional dependency, jieba is the Chinese default
    import jieba

    jieba.setLogLevel(60)
    _HAS_JIEBA = True
except Exception:  # pragma: no cover
    _HAS_JIEBA = False


def tokenize(text: str) -> list[str]:
    """Tokenise text for word-level diffing.

    Chinese content is segmented with jieba when available; everything else is
    split on word boundaries.  The result is a flat list of comparable
    tokens (whitespace is dropped).
    """
    return [token for token, _start, _end in tokenize_offsets(text)]


def tokenize_offsets(text: str) -> list[tuple[str, int, int]]:
    """Like :func:`tokenize` but keeps the character offsets of each token.

    The offsets are what allows the UI to highlight exactly the changed
    fragment instead of the whole paragraph (PRD 6.3 / 8.4).
    """
    if not text:
        return []
    tokens: list[tuple[str, int, int]] = []
    for match in _TOKEN_RE.finditer(text):
        raw = match.group(0)
        if raw.isspace():
            continue
        start, end = match.start(), match.end()
        if _CJK_RE.search(raw):
            if _HAS_JIEBA:
                for word, word_start, word_end in jieba.tokenize(raw):
                    if word.strip():
                        tokens.append((word, start + word_start, start + word_end))
            else:
                for index, char in enumerate(raw):
                    tokens.append((char, start + index, start + index + 1))
        else:
            tokens.append((raw, start, end))
    return tokens


def char_tokens(text: str) -> list[str]:
    """Character level fallback tokenisation."""
    return [ch for ch in text if not ch.isspace()]


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def summarise(text: str, limit: int = 60) -> str:
    """Short single-line preview of a text block."""
    flat = _WHITESPACE_RE.sub(" ", text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"
