"""PDF 比对 · 语义文本比对 V2.

The modules of this package implement the comparison of two PDFs through
their token stream:

* :mod:`app.compare.pdf_normalize` - the normalised text and the mapping
  back to the page,
* :mod:`app.compare.pdf_tokens` - the tokens (the unit of the comparison)
  and their reference to the characters of the PDF,
* :mod:`app.compare.pdf_anchor` - the global alignment of the two token
  sequences,
* :mod:`app.compare.pdf_align` - the matching of the paragraphs,
* :mod:`app.compare.pdf_diff` - the differences inside the aligned regions,
* :mod:`app.compare.pdf_tables` - the comparison of the tables,
* :mod:`app.compare.pdf_compare` - the entry point.
"""

from __future__ import annotations

from .pdf_compare import can_stream_compare, compare_pdfs

__all__ = ["can_stream_compare", "compare_pdfs"]
