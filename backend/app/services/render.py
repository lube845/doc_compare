"""Original-layout preview support for Word documents.

The frontend renders the DOCX container itself (``docx-preview``), so the
browser needs a DOCX - not the extracted text.  A ``.docx`` upload is already
a DOCX; the legacy formats (``.doc`` / ``.wps``) are converted once with
LibreOffice and the result is cached next to the uploads, so a preview request
never repeats the conversion.

The cache lives in ``<uploads>/render/`` rather than next to the original
file: :func:`app.storage.stored_path` globs ``<file_id>.*`` and would
otherwise pick the converted file up as if it were the upload.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from ..config import settings
from ..parsers import libreoffice

#: formats whose original layout can be shown in the browser
RENDERABLE_TYPES = frozenset({"doc", "docx", "wps"})

#: DOCX media type (the container docx-preview understands)
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

#: LibreOffice is single-instance; serialise the conversions.
_lock = threading.Lock()


def cache_dir() -> Path:
    path = settings.upload_dir / "render"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_path(file_id: str) -> Path:
    return cache_dir() / f"{file_id}.docx"


def ensure_render_docx(source: Path, file_id: str, file_type: str) -> Path | None:
    """Return a DOCX that the browser can render, or ``None``.

    ``docx`` files are returned as they are; ``doc`` / ``wps`` files are
    converted with LibreOffice and cached.  ``None`` means the layout preview
    is unavailable (unsupported format, LibreOffice missing, conversion
    failed) and the caller should fall back to the text preview.
    """
    if file_type not in RENDERABLE_TYPES or not source.exists():
        return None
    if file_type == "docx":
        return source

    cached = cached_path(file_id)
    if cached.exists() and cached.stat().st_size > 0:
        return cached

    with _lock:
        # Another request may have produced the file while we waited.
        if cached.exists() and cached.stat().st_size > 0:
            return cached

        converted = libreoffice.convert_to_ooxml(source, file_type)
        if converted is None:
            return None

        temporary = cached.with_suffix(".tmp")
        try:
            shutil.copyfile(converted, temporary)
            os.replace(temporary, cached)
        except OSError:
            return None
        finally:
            # The conversion is only needed for the copy; the cache holds
            # the result.
            libreoffice.cleanup_converted()
            temporary.unlink(missing_ok=True)

    return cached if cached.exists() else None
