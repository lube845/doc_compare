"""Parser registry / dispatcher (PRD 12.1 - Parser Dispatcher)."""

from __future__ import annotations

from pathlib import Path

from ..domain.models import ParseResult
from ..schemas import CompareConfig
from . import libreoffice
from .base import BaseParser, ParseFailed, ProgressCallback, UnsupportedFormat
from .detect import Detection, detect_file_type
from .doc_parser import DocParser
from .docx_parser import DocxParser
from .et_parser import EtParser
from .excel_parser import XlsParser, XlsxParser
from .ocr_parser import OcrParser
from .pdf_parser import PdfParser
from .wps_parser import WpsParser

_PARSERS: list[BaseParser] = [
    DocxParser(),
    DocParser(),
    WpsParser(),
    XlsxParser(),
    XlsParser(),
    EtParser(),
    PdfParser(),
    OcrParser(),
]

#: every parser of the registry, including the ones whose engine is not
#: enabled (the OCR parser falls back to the PDF parser when no OCR provider
#: is configured)
ALL_PARSERS: list[BaseParser] = list(_PARSERS)


def get_parser(file_type: str) -> BaseParser | None:
    for parser in _PARSERS:
        if parser.supports(file_type):
            return parser
    return None


def detect(path: Path, filename: str | None = None) -> Detection:
    return detect_file_type(path, filename)


def parse_file(
    path: Path,
    filename: str | None = None,
    config: CompareConfig | None = None,
    file_type: str | None = None,
    progress: ProgressCallback | None = None,
) -> ParseResult:
    """Detect the format and parse ``path`` into the unified model."""
    detection = detect(path, filename)
    resolved = file_type or detection.file_type

    parser = get_parser(resolved)
    if parser is None:
        raise UnsupportedFormat(
            f"不支持的文件格式：{resolved or detection.extension}"
        )

    try:
        result = parser.parse(path, config, progress)
    except ParseFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - never let one file kill a task
        raise ParseFailed(f"文件解析失败：{exc}") from exc
    finally:
        # Conversion artefacts are only needed while parsing (PRD 16.2).
        libreoffice.cleanup_converted()

    result.metadata.setdefault("detected_extension", detection.extension)
    result.metadata.setdefault("detected_type", detection.file_type)
    result.metadata["format_mismatch"] = detection.mismatch
    if detection.mismatch and detection.detail:
        result.metadata["format_warning"] = detection.detail
    return result
