"""Structured output of the PDF parser (改造计划 阶段 2).

The generic document model describes *logical blocks*.  That is the right
model for a DOCX, but it is the wrong model for a PDF: the block boundaries
are guessed from the typography, so two PDFs of the same document with a
different margin, font size or pagination produce different blocks and the
generic engine then reports "whole paragraph deleted + whole paragraph
added".

This module adds the contract the streaming comparison needs:

* :class:`PageInfo` - what the parser could do with one page (text layer
  present?  garbled?  looks like a scan?).  The fields exist so that a page
  can later be handed to an OCR provider without changing the contract.
* :class:`CharRef` - one character of the document together with its
  position on the page.  The character stream is the unit of comparison:
  it is independent of the paragraph structure and therefore of the
  typography.
* :class:`TableRegion` - a table that is *not* part of the character
  stream; it only records where it sits in the stream so that the
  comparison can use it as a split point.
* :class:`ParsedPdf` - the whole contract.

All of the structures are plain dataclasses: a 200 page PDF has roughly
200 000 characters and building that many pydantic models would dominate
the parse time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class PageInfo:
    """What the parser could make of a single page."""

    page: int
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0
    #: ``text`` for a page with a text layer, ``ocr`` for a recognised page
    source: str = "text"
    #: ``ok`` / ``warn`` / ``bad``
    quality: str = "ok"
    #: e.g. ``scanned_like``, ``garbled_text``, ``column_fallback``
    flags: list[str] = field(default_factory=list)
    char_count: int = 0


@dataclass(slots=True)
class CharRef:
    """One character of the document and where it sits on the page."""

    ch: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    #: identifier of the rendered line the character belongs to
    line_id: int = 0
    #: identifier of the logical block the character belongs to
    block_id: str | None = None
    #: the character belongs to a running header or footer
    running: bool = False


@dataclass(slots=True)
class TableRegion:
    """A table that is kept out of the character stream."""

    table_id: str
    page: int
    bbox: list[float] = field(default_factory=list)
    #: position of the table in the character stream
    char_index: int = 0
    #: ``ruled`` for a table with borders, ``inferred`` for a table that was
    #: recognised through the alignment of its text only
    kind: str = "ruled"


@dataclass(slots=True)
class ParsedPdf:
    """Everything the streaming comparison needs to know about a PDF."""

    pages: list[PageInfo] = field(default_factory=list)
    #: the characters of the document in reading order
    chars: list[CharRef] = field(default_factory=list)
    table_regions: list[TableRegion] = field(default_factory=list)
    #: the running headers / footers that were removed (for debugging)
    dropped_running: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def page(self, number: int) -> PageInfo | None:
        for info in self.pages:
            if info.page == number:
                return info
        return None

    def quality_of(self, page: int) -> str:
        info = self.page(page)
        return info.quality if info else "ok"
