"""Best effort text extraction for WPS containers.

WPS Office writes ``.wps`` / ``.et`` files in its own binary layout inside an
OLE2 container.  There is no public specification for that layout, so when
LibreOffice is unavailable the parser falls back to scanning the raw streams
for readable text.

The result is intentionally marked as ``partial`` so the UI can tell the user
that structure recovery is incomplete (PRD 15.2 - "WPS 解析失败 -> 尝试兼容转换").
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_ALLOWED = re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f]")

_UTF16_RUN = re.compile(r"(?:[\x20-\x7e　-〿㐀-䶿一-鿿豈-﫿]){2,}")
_ASCII_RUN = re.compile(r"[\x20-\x7e]{4,}")


def readable_runs(data: bytes, min_length: int = 4) -> list[str]:
    """Return readable text runs found in ``data``."""
    runs: list[str] = []

    try:
        text = data.decode("utf-16-le", errors="ignore")
    except Exception:  # noqa: BLE001
        text = ""
    for match in _UTF16_RUN.finditer(text):
        run = match.group(0).strip()
        if len(run) >= min_length:
            runs.append(run)

    for match in _ASCII_RUN.finditer(data.decode("latin-1", errors="ignore")):
        run = match.group(0).strip()
        if len(run) >= min_length:
            runs.append(run)

    # Longest runs first - the payload stream carries the document body.
    runs.sort(key=len, reverse=True)
    return runs


def best_text(data: bytes) -> str:
    """The single most likely body text of an OLE2 container."""
    runs = readable_runs(data)
    if not runs:
        return ""
    return runs[0]


def join_runs(runs: list[str], limit: int = 200) -> str:
    """Join the most promising runs into one blob."""
    selected: list[str] = []
    length = 0
    for run in runs:
        if length >= limit:
            break
        selected.append(run)
        length += len(run)
    return "\n".join(selected)
