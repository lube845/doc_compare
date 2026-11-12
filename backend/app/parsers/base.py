"""Common parser interface (PRD chapter 11.2)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..domain.models import ParseResult
from ..progress import ProgressCallback
from ..schemas import CompareConfig


class BaseParser(ABC):
    """Every format specific parser implements this interface."""

    #: format identifier, e.g. ``docx``
    file_type: str = ""
    #: human readable name used in the UI
    display_name: str = ""
    #: content type produced by this parser
    content_type: str = "document"
    #: capabilities that this parser does *not* cover (PRD 7.7)
    known_gaps: tuple[str, ...] = ()

    def supports(self, file_type: str) -> bool:
        return file_type == self.file_type

    @abstractmethod
    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        """Parse ``file_path`` into the unified data model.

        ``progress`` is called with ``(percent, stage)`` while the parser
        works, so a long running parse can report its progress.
        """

    def get_metadata(self) -> dict:
        return {
            "file_type": self.file_type,
            "display_name": self.display_name,
            "content_type": self.content_type,
            "known_gaps": list(self.known_gaps),
        }


class UnsupportedFormat(Exception):
    """Raised when a file cannot be parsed at all."""


class ParseFailed(Exception):
    """Raised when a parser recognises the format but cannot read the file."""

    def __init__(self, message: str, status: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
