"""File storage.

Original files are kept untouched; converted files (LibreOffice output) live
in a temporary directory only (PRD 12.2 / 16.2).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from .config import settings


def new_file_id() -> str:
    return f"file_{uuid.uuid4().hex[:16]}"


def new_comparison_id() -> str:
    return f"cmp_{uuid.uuid4().hex[:16]}"


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_upload(filename: str, data: bytes) -> tuple[str, Path]:
    """Persist an uploaded file and return ``(file_id, path)``."""
    settings.ensure_directories()
    file_id = new_file_id()
    suffix = Path(filename).suffix.lower()
    target = settings.upload_dir / f"{file_id}{suffix}"
    target.write_bytes(data)
    return file_id, target


def stored_path(file_id: str) -> Path | None:
    settings.ensure_directories()
    for path in settings.upload_dir.glob(f"{file_id}.*"):
        return path
    return None
