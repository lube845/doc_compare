"""The OCR result of an upload is kept on disk.

The character stream of a PDF is not stored in the database, so a PDF is
parsed again when the comparison runs after a restart of the service.  For a
scanned document that would mean paying for the recognition of every page a
second time.  The result of the recognition is therefore written next to the
upload and read again the next time the file is parsed.

The cache holds the lines in PDF point coordinates, so they can be used
without touching the OCR provider again.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import settings
from .pdf_layout import PdfLine

#: the directory (inside the upload directory) that holds the caches
CACHE_DIRNAME = "ocr"


def cache_file(pdf_path: Path) -> Path | None:
    """The cache file of a PDF, ``None`` when the PDF is not an upload.

    Only the files of the upload directory are cached: the name of the
    cache is derived from the name of the file, and a file that happens to
    have the same name somewhere else must not overwrite it.
    """
    try:
        if pdf_path.parent.resolve() != settings.upload_dir.resolve():
            return None
    except OSError:  # pragma: no cover - defensive
        return None
    return settings.upload_dir / CACHE_DIRNAME / f"{pdf_path.stem}.json"


def line_to_dict(line: PdfLine) -> dict:
    """A line of the page, in a form that survives a round trip through JSON."""
    return {
        "text": line.text,
        "bbox": [round(value, 3) for value in line.bbox],
        "size": line.size,
        "chars": [
            [round(char[0], 3), round(char[1], 3), round(char[2], 3), round(char[3], 3), char[4]]
            for char in line.chars
        ],
    }


def line_from_dict(page_number: int, index: int, data: dict) -> PdfLine | None:
    """The line of a cached page."""
    try:
        chars = [
            (float(char[0]), float(char[1]), float(char[2]), float(char[3]), str(char[4]))
            for char in data.get("chars") or []
        ]
        bbox = tuple(float(value) for value in (data.get("bbox") or (0.0, 0.0, 0.0, 0.0)))
    except (TypeError, ValueError):
        return None
    if not chars:
        return None
    return PdfLine(
        page=page_number,
        text=str(data.get("text") or ""),
        bbox=bbox,  # type: ignore[arg-type]
        size=float(data.get("size") or 0.0),
        bold=False,
        font="",
        chars=chars,
    )


def page_to_dict(lines: list[PdfLine], confidence: float) -> dict:
    """The cached form of a recognised page."""
    return {
        "confidence": round(confidence, 4),
        "lines": [line_to_dict(line) for line in lines],
    }


def lines_from_dict(page_number: int, page: dict) -> list[PdfLine]:
    """The lines of a cached page."""
    lines: list[PdfLine] = []
    for index, data in enumerate(page.get("lines") or []):
        line = line_from_dict(page_number, index, data)
        if line is not None:
            lines.append(line)
    for position, line in enumerate(lines):
        line.index = position
    return lines


def load(pdf_path: Path, provider: str) -> dict[int, dict]:
    """The cached pages of a PDF (empty when there is no cache).

    The cache belongs to one channel: a result of another channel is ignored,
    so switching the provider re-recognises the pages instead of reusing a
    recognition that the new channel did not produce.
    """
    path = cache_file(pdf_path)
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("provider") != provider:
        return {}
    pages = payload.get("pages")
    if not isinstance(pages, dict):
        return {}
    result: dict[int, dict] = {}
    for key, value in pages.items():
        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            continue
    return result


def store(pdf_path: Path, provider: str, pages: dict[int, dict]) -> None:
    """Write the recognised pages to the cache.

    A failure to write the cache is not fatal: the recognition simply costs
    a little more the next time.
    """
    path = cache_file(pdf_path)
    if path is None or not pages:
        return
    existing = load(pdf_path, provider)
    existing.update(pages)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"provider": provider, "pages": existing}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        return
