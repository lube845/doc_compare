"""LibreOffice bridge.

Several legacy formats (``.doc``, ``.wps``, ``.et``) are only reliably
readable through a converter.  When ``soffice`` is installed the file is
converted to an OOXML equivalent and handed to the corresponding OOXML parser.

The original file is never modified - conversions are written to a temporary
directory (PRD 12.2: "原始文件必须保留，转换文件只用于解析").
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

SOFFICE_CANDIDATES = ("soffice", "libreoffice")

#: converted files awaiting cleanup (PRD 16.2 - 临时文件定期清理).
#: The list is per thread: a parse that finishes must not delete the
#: conversion of a parse that runs at the same time.
_converted = threading.local()


def _pending() -> list[Path]:
    """The conversions of the current thread."""
    paths = getattr(_converted, "paths", None)
    if paths is None:
        paths = []
        _converted.paths = paths
    return paths

#: target format for the conversion, keyed by source format
_TARGETS = {
    "doc": "docx",
    "wps": "docx",
    "et": "xlsx",
    "xls": "xlsx",
    "rtf": "docx",
}


def soffice_path() -> str | None:
    for candidate in SOFFICE_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def is_available() -> bool:
    return soffice_path() is not None


def convert(file_path: Path, target: str, timeout: int = 120) -> Path | None:
    """Convert ``file_path`` to ``target`` format, returning the new path.

    Returns ``None`` when LibreOffice is unavailable or the conversion failed.
    """
    binary = soffice_path()
    if binary is None:
        return None

    with tempfile.TemporaryDirectory(prefix="doc_convert_") as tmp:
        outdir = Path(tmp)
        profile = (outdir / "profile").as_uri()
        cmd = [
            binary,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--norestore",
            "--convert-to",
            target,
            "--outdir",
            str(outdir),
            str(file_path),
        ]
        try:
            subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None

        suffix = f".{target}"
        candidates = sorted(outdir.glob(f"*{suffix}"))
        if not candidates:
            return None
        converted = candidates[0]
        handle, destination_name = tempfile.mkstemp(
            prefix="doc_conv_", suffix=suffix
        )
        os.close(handle)
        destination = Path(destination_name)
        shutil.copy2(converted, destination)
        _pending().append(destination)
        return destination


def convert_to_ooxml(file_path: Path, source_format: str) -> Path | None:
    """Convert a legacy format to its OOXML equivalent."""
    target = _TARGETS.get(source_format)
    if not target:
        return None
    return convert(file_path, target)


def cleanup_converted() -> None:
    """Delete the conversion artefacts created by the current thread."""
    while True:
        paths = _pending()
        if not paths:
            return
        path = paths.pop()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
