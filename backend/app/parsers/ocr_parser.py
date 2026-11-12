"""扫描件识别：把 PDF 页面渲染成图片，交给 OCR，再还原成页面上的行。

这个模块是 :mod:`app.parsers.pdf_parser` 和百度 OCR 之间的桥：

1. :func:`render_page` 用 PyMuPDF 把页面栅格化成 PNG（百度要求最长边不超过
   4096px，base64 之后不超过 8MB，所以 DPI 要按页面尺寸算，不能写死）。
2. :func:`ocr_page_lines` 调用百度「通用文字识别（标准含位置版）」，把返回的
   像素坐标换算成 PDF 的点坐标，产出和文本层页面一模一样的
   :class:`app.parsers.pdf_layout.PdfLine`。

坐标换算是关键：文本层的行来自 PyMuPDF 的 ``get_text``，坐标是 PDF 点；OCR 的
行来自位图，坐标是像素。两者必须换算到同一个坐标系，字符流比对和前端的高亮
矩形才成立。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..domain.models import ContentType, ParseResult
from ..schemas import CompareConfig
from ..services import app_settings
from .base import BaseParser, ProgressCallback
from .ocr_client import (
    BaseOcrProvider,
    OcrError,
    OcrLine,
    OcrNotConfigured,
    OcrPage,
    get_provider,
)
from .pdf_layout import PdfLine, order_lines

try:  # pragma: no cover - import shim for the two PyMuPDF module names
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    try:
        import fitz  # type: ignore
    except ImportError:
        fitz = None  # type: ignore


#: 百度要求的图片最长边（像素）
MAX_EDGE = 4096
#: base64 之后的大小上限（百度是 8MB，这里留一点余量）
MAX_BASE64_BYTES = 6 * 1024 * 1024
#: 图片太大时依次尝试的 DPI
DPI_FALLBACKS = (200, 150, 110)
#: 行高和字号的比例，用来从行高估算字号
SIZE_PER_HEIGHT = 0.8


@dataclass(slots=True)
class OcrPageResult:
    """What one recognised page looks like on the document."""

    lines: list[PdfLine] = field(default_factory=list)
    #: 平均置信度（0-1）
    confidence: float = 0.0
    #: 百度返回的日志号，出问题时用来定位
    log_id: str = ""


def _effective_dpi(page, dpi: int) -> float:
    """The DPI that keeps the page inside the size limits of the API."""
    try:
        longest = max(float(page.rect.width), float(page.rect.height))
    except Exception:  # noqa: BLE001 - defensive
        longest = 0.0
    if longest <= 0:
        return float(dpi)
    zoom = dpi / 72.0
    if longest * zoom > MAX_EDGE:
        zoom = MAX_EDGE / longest
    return max(1.0, zoom * 72.0)


def _page_transform(page):
    """The transform from the rendered page back to the document page.

    The text layer of a PDF reports its coordinates in the *unrotated* page
    space (that is what the front end expects), but a page that carries a
    ``/Rotate`` is rendered rotated.  The coordinates of the OCR result
    therefore have to be rotated back.

    Returns ``None`` when the page is not rotated.
    """
    try:
        matrix = page.derotation_matrix
    except Exception:  # noqa: BLE001 - defensive
        return None
    if matrix is None:
        return None
    if (
        abs(matrix.a - 1) < 1e-6
        and abs(matrix.b) < 1e-6
        and abs(matrix.c) < 1e-6
        and abs(matrix.d - 1) < 1e-6
        and abs(matrix.e) < 1e-6
        and abs(matrix.f) < 1e-6
    ):
        return None

    def transform(x: float, y: float) -> tuple[float, float]:
        return (
            matrix.a * x + matrix.c * y + matrix.e,
            matrix.b * x + matrix.d * y + matrix.f,
        )

    return transform


def render_page(page, dpi: int | None = None) -> tuple[bytes, float, float]:
    """Render ``page`` into a PNG.

    Returns the PNG bytes and the two scale factors that turn a pixel
    coordinate into a PDF point coordinate.
    """
    if fitz is None:
        raise OcrError("未安装 PyMuPDF，无法渲染扫描页")

    target = dpi or app_settings.get().dpi
    candidates = [target, *DPI_FALLBACKS]
    seen: list[float] = []
    for candidate in candidates:
        effective = _effective_dpi(page, candidate)
        if any(abs(effective - value) < 0.5 for value in seen):
            continue
        seen.append(effective)

        zoom = effective / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        try:
            pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
            image = pixmap.tobytes("png")
        except Exception as exc:  # noqa: BLE001 - a broken page must not kill the parse
            raise OcrError(f"渲染页面失败：{exc}") from exc

        if len(image) * 4 // 3 <= MAX_BASE64_BYTES:
            scale_x = float(page.rect.width) / max(1, pixmap.width)
            scale_y = float(page.rect.height) / max(1, pixmap.height)
            return image, scale_x, scale_y

    raise OcrError(
        f"页面渲染后仍然过大（base64 超过 {MAX_BASE64_BYTES // 1024 // 1024}MB）"
    )


def _chars_from_line(
    line: OcrLine,
    scale_x: float,
    scale_y: float,
    transform=None,
) -> list[tuple[float, float, float, float, str]]:
    """The characters of an OCR line, in PDF point coordinates."""
    chars: list[tuple[float, float, float, float, str]] = []
    for char in line.chars:
        x0, y0 = char.x0 * scale_x, char.y0 * scale_y
        x1, y1 = char.x1 * scale_x, char.y1 * scale_y
        if transform is not None:
            x0, y0 = transform(x0, y0)
            x1, y1 = transform(x1, y1)
        chars.append(
            (
                min(x0, x1),
                min(y0, y1),
                max(x0, x1),
                max(y0, y1),
                char.ch,
            )
        )
    if chars:
        return chars

    # The API did not deliver the single character positions: spread the
    # characters of the line over its box so that the character stream stays
    # complete (the exact position of a character is better than none).
    count = len(line.text)
    if count <= 0:
        return []
    x0, y0 = line.x0 * scale_x, line.y0 * scale_y
    x1, y1 = line.x1 * scale_x, line.y1 * scale_y
    if transform is not None:
        x0, y0 = transform(x0, y0)
        x1, y1 = transform(x1, y1)
    left = min(x0, x1)
    right = max(x0, x1)
    top = min(y0, y1)
    bottom = max(y0, y1)
    step = (right - left) / count if right > left else 0.0
    for index, glyph in enumerate(line.text):
        start = left + step * index
        chars.append((start, top, start + step, bottom, glyph))
    return chars


def ocr_page_to_lines(
    page_result: OcrPage,
    page_number: int,
    scale_x: float,
    scale_y: float,
    page_width: float = 0.0,
    transform=None,
) -> list[PdfLine]:
    """Turn the OCR result of one page into the lines of that page."""
    lines: list[PdfLine] = []
    for line in page_result.lines:
        text = (line.text or "").strip()
        if not text:
            continue
        chars = _chars_from_line(line, scale_x, scale_y, transform)
        if not chars:
            continue
        x0 = min(char[0] for char in chars)
        y0 = min(char[1] for char in chars)
        x1 = max(char[2] for char in chars)
        y1 = max(char[3] for char in chars)
        lines.append(
            PdfLine(
                page=page_number,
                text=text,
                bbox=(x0, y0, x1, y1),
                size=round((y1 - y0) * SIZE_PER_HEIGHT, 2),
                bold=False,
                font="",
                chars=chars,
            )
        )

    ordered = order_lines(lines, page_width)
    for position, line in enumerate(ordered):
        line.index = position
    return ordered


def ocr_page_lines(
    page,
    page_number: int,
    client: BaseOcrProvider | None = None,
    values: dict[str, str] | None = None,
    dpi: int | None = None,
) -> OcrPageResult:
    """Recognise one page and return its lines.

    Raises an :class:`OcrError` when the page cannot be recognised; the
    caller decides whether that is fatal.
    """
    current = app_settings.get()
    provider = client if client is not None else get_provider(current.provider)
    if provider is None:
        raise OcrNotConfigured("未配置 OCR 服务，无法识别扫描件")
    settings_values = values if values is not None else current.values

    image, scale_x, scale_y = render_page(page, dpi)
    result = provider.recognize(image, settings_values)
    page_width = 0.0
    try:
        page_width = float(page.rect.width)
    except Exception:  # noqa: BLE001 - defensive
        page_width = 0.0
    lines = ocr_page_to_lines(
        result,
        page_number,
        scale_x,
        scale_y,
        page_width,
        _page_transform(page),
    )
    return OcrPageResult(
        lines=lines,
        confidence=result.confidence,
        log_id=result.log_id,
    )


class OcrParser(BaseParser):
    """Parser for a file that has no text layer at all.

    A scanned PDF is still a PDF: it is parsed by
    :class:`app.parsers.pdf_parser.PdfParser`, which recognises the pages
    that need it.  This parser exists so that the ``scanned_pdf`` file type
    is not a dead end.
    """

    file_type = "scanned_pdf"
    display_name = "OCR 识别 (扫描件)"
    content_type = ContentType.DOCUMENT.value

    def supports(self, file_type: str) -> bool:
        return file_type == "scanned_pdf"

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        from .pdf_parser import PdfParser

        if not app_settings.get().enabled:
            return ParseResult(
                file_type=self.file_type,
                content_type=ContentType.DOCUMENT,
                parser="ocr_parser",
                parse_status="unsupported_ocr",
                message="OCR 识别尚未启用，该文件为扫描件，暂时无法参与内容比对",
                metadata={"ocr_enabled": False},
                uncovered=["扫描件 OCR"],
            )

        return PdfParser().parse(file_path, options, progress)
