"""File type detection (PRD chapter 4.2).

The extension is only a hint.  The real format is derived from the file
signature / container structure so that a file named ``report.docx`` but
actually containing a PDF is detected and reported as a mismatch.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PDF_SIGNATURE = b"%PDF"
RTF_SIGNATURE = b"{\\rtf"

SUPPORTED_EXTENSIONS = {".doc", ".docx", ".wps", ".et", ".xls", ".xlsx", ".pdf"}


@dataclass
class Detection:
    file_type: str
    extension: str
    confidence: str  # high | medium | low
    mismatch: bool = False
    detail: str = ""


def _ole_type(path: Path) -> str:
    """Distinguish doc / xls / wps / et inside an OLE2 container."""
    from .cfb import CompoundFile, CFBError

    try:
        with path.open("rb") as handle:
            head = handle.read(8 * 1024 * 1024)
        cfb = CompoundFile(head)
    except (CFBError, Exception):  # noqa: BLE001 - detection must never raise
        return "ole"

    names = {name for name in cfb.stream_names()}
    lowered = {name.lower() for name in names}

    if "WordDocument" in names:
        return "doc"
    if "Workbook" in names or "Book" in names:
        return "xls"
    if "EncryptedPackage" in names or "EncryptionInfo" in names:
        return "encrypted"
    # WPS / ET keep their payload in a stream named after the format.
    for candidate, kind in (
        ("wps", "wps"),
        ("et", "et"),
        ("Contents", "wps"),
        ("Package", "wps"),
    ):
        if candidate.lower() in lowered:
            return kind
    return "ole"


def detect_file_type(path: Path, filename: str | None = None) -> Detection:
    """Identify the real format of ``path``."""
    name = filename or path.name
    extension = Path(name).suffix.lower().lstrip(".")

    header = b""
    try:
        with path.open("rb") as handle:
            header = handle.read(4096)
    except OSError:
        pass

    actual = "unknown"
    if header.startswith(PDF_SIGNATURE):
        actual = "pdf"
    elif header.startswith(OLE_SIGNATURE):
        actual = _ole_type(path)
    elif header.startswith(RTF_SIGNATURE):
        actual = "rtf"
    elif header[:2] == b"PK":
        actual = _zip_type(path)

    if actual in {"doc", "xls", "wps", "et", "xlsx", "docx", "pdf"}:
        mismatch = extension != actual and f".{extension}" in SUPPORTED_EXTENSIONS
        detail = ""
        if mismatch:
            detail = f"文件后缀为 .{extension}，实际内容为 {actual.upper()} 格式"
        elif extension != actual:
            detail = f"文件后缀为 .{extension}，实际内容为 {actual.upper()} 格式"
        return Detection(
            file_type=actual,
            extension=extension or actual,
            confidence="high" if not mismatch else "high",
            mismatch=mismatch,
            detail=detail,
        )

    if actual == "encrypted":
        return Detection(
            file_type="encrypted",
            extension=extension,
            confidence="high",
            mismatch=f".{extension}" in SUPPORTED_EXTENSIONS,
            detail="文件已加密，请先解除密码保护",
        )

    return Detection(
        file_type="unknown",
        extension=extension,
        confidence="low",
        mismatch=f".{extension}" in SUPPORTED_EXTENSIONS,
        detail="无法识别的文件格式",
    )


def _zip_type(path: Path) -> str:
    """OOXML containers are zip files - look inside to tell them apart."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return "unknown"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if any(name.startswith("word/") for name in names):
        return "docx"
    if any(name.startswith("xl/") for name in names):
        return "xlsx"
    return "unknown"


def is_supported(path: Path, filename: str | None = None) -> bool:
    detection = detect_file_type(path, filename)
    return detection.file_type in {
        "doc",
        "docx",
        "wps",
        "et",
        "xls",
        "xlsx",
        "pdf",
    }
