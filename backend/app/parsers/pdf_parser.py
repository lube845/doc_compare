"""PDF parser built on PyMuPDF (PRD chapter 8).

The parser produces logical blocks (paragraphs) instead of raw page fragments,
because a PDF paragraph may span pages and two PDFs may paginate differently
(PRD 8.2).

The layout analysis itself lives in :mod:`app.parsers.pdf_layout`; this module
glues it together with the table detection and the unified data model.

Besides the logical blocks the parser also produces the *character stream*
of the document (:class:`app.domain.pdf_models.ParsedPdf`).  The blocks are
the result of guessing the structure of the page, the character stream is
what the page really says.  Two PDFs of the same document with a different
typography produce different blocks but (almost) the same character stream,
which is why the comparison of two PDFs uses the character stream.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.models import (
    Block,
    BlockType,
    ContentType,
    Document,
    ParseResult,
    Section,
    SourcePosition,
    TableCell,
    TableData,
    TableRow,
)
from ..domain.normalize import normalise
from ..domain.pdf_models import CharRef, PageInfo, ParsedPdf, TableRegion
from ..schemas import CompareConfig
from ..services import app_settings
from . import ocr_cache, ocr_parser
from .base import BaseParser, ParseFailed, ProgressCallback
from .ocr_client import OcrError
from .pdf_layout import (
    DetectedTable,
    PdfLine,
    PdfParagraph,
    assign_heading_levels,
    body_font_size,
    detect_columns,
    detect_running_text,
    detect_text_tables,
    extract_lines,
    group_lines,
    inside_any_table,
    merge_across_pages,
    page_text_size,
)

try:  # pragma: no cover - import shim for the two PyMuPDF module names
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    try:
        import fitz  # type: ignore
    except ImportError:
        fitz = None  # type: ignore


#: a page with fewer characters than this and with an image looks like a scan
SCANNED_CHAR_THRESHOLD = 20
#: share of garbled characters above which the text layer is unusable
GARBLED_RATIO = 0.3
#: an OCR confidence below this value makes the page suspicious
OCR_LOW_CONFIDENCE = 0.8
#: a table that ends this close to the bottom of the page blocks the
#: paragraph merge across the page break
TABLE_BOTTOM_RATIO = 0.97


class PdfParser(BaseParser):
    file_type = "pdf"
    display_name = "PDF 文档"
    content_type = ContentType.DOCUMENT.value
    known_gaps = ("扫描件 OCR", "复杂嵌套表格", "跨页表格结构")

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        if fitz is None:
            raise ParseFailed("未安装 PyMuPDF，无法解析 PDF 文件")

        config = options or CompareConfig()
        try:
            document = fitz.open(str(file_path))
        except Exception as exc:  # noqa: BLE001
            raise ParseFailed(f"无法打开 PDF 文件：{exc}") from exc

        if document.needs_pass:
            document.close()
            raise ParseFailed("PDF 文件已加密，请先解除密码保护")

        # -- 1. raw material: the lines and the tables of every page -----
        page_lines: dict[int, list[PdfLine]] = {}
        page_tables: dict[int, list[TableData]] = {}
        page_heights: dict[int, float] = {}
        page_widths: dict[int, float] = {}
        page_rotations: dict[int, int] = {}
        page_images: dict[int, int] = {}
        empty_pages: list[int] = []
        scanned_pages: list[int] = []
        #: the pages whose content was recognised by the OCR provider
        ocr_pages: set[int] = set()
        #: the average confidence of every recognised page
        ocr_confidence: dict[int, float] = {}
        #: why a page could not be recognised
        ocr_errors: list[str] = []
        #: the pages that were recognised during this parse (for the cache)
        ocr_new: dict[int, dict] = {}
        #: how many pages were sent to the provider (the budget counts the
        #: calls, not the successes - a failing provider would be called
        #: again for every page)
        ocr_attempts = 0
        #: True when the page limit stopped the recognition
        ocr_limit_hit = False
        ocr_budget = max(0, app_settings.get().max_pages)
        #: the pages that were recognised before, so that a restart of the
        #: service does not pay for the same recognition twice
        ocr_provider_name = app_settings.get().provider
        ocr_cached = ocr_cache.load(file_path, ocr_provider_name)

        def recognise(page, page_number: int) -> list[PdfLine]:
            """Recognise a scanned page; an empty list when it does not work.

            A page that cannot be recognised is not fatal: it is simply left
            out of the comparison, like a page without a text layer.
            """
            nonlocal ocr_attempts, ocr_limit_hit
            if not app_settings.get().enabled:
                return []
            cached = ocr_cached.get(page_number)
            if cached is not None:
                lines = ocr_cache.lines_from_dict(page_number, cached)
                if lines:
                    ocr_pages.add(page_number)
                    ocr_confidence[page_number] = float(cached.get("confidence") or 0.0)
                    return lines
            if ocr_attempts >= ocr_budget:
                ocr_limit_hit = True
                return []
            ocr_attempts += 1
            try:
                result = ocr_parser.ocr_page_lines(page, page_number)
            except OcrError as exc:
                ocr_errors.append(f"第 {page_number} 页：{exc}")
                return []
            except Exception as exc:  # noqa: BLE001 - never kill the parse
                ocr_errors.append(f"第 {page_number} 页：{exc}")
                return []
            if not result.lines:
                return []
            ocr_pages.add(page_number)
            ocr_confidence[page_number] = result.confidence
            ocr_new[page_number] = ocr_cache.page_to_dict(result.lines, result.confidence)
            return result.lines

        for page_index in range(document.page_count):
            page = document[page_index]
            page_number = page_index + 1
            if progress is not None:
                progress(
                    5 + int(85 * page_index / max(1, document.page_count)),
                    "parsing",
                )

            lines = extract_lines(page, page_number)
            tables = self._extract_tables(page, page_number)
            # The text coordinates do not follow the rotation of the page,
            # so the size of the page is taken in the text coordinate system.
            page_width, page_height = page_text_size(page)
            if len(detect_columns(lines, page_width)) < 2:
                # A page in two columns is not a table with two columns.
                tables.extend(self._text_tables(page, page_number, tables))

            page_images[page_number] = self._image_count(page)

            if not lines and not tables and page_images[page_number]:
                # A scan: without a text layer the page has to be recognised
                # before it can take part in the comparison.
                recognised = recognise(page, page_number)
                if recognised:
                    lines = recognised
                    if progress is not None:
                        progress(
                            5 + int(85 * (page_index + 1) / max(1, document.page_count)),
                            "parsing",
                        )

            page_lines[page_number] = lines
            page_tables[page_number] = tables
            page_heights[page_number] = page_height
            page_widths[page_number] = page_width
            page_rotations[page_number] = int(getattr(page, "rotation", 0) or 0) % 360

            if not lines and not tables:
                if page_images[page_number]:
                    scanned_pages.append(page_number)
                else:
                    empty_pages.append(page_number)

        if ocr_new:
            # The next parse of this file does not have to pay for the
            # recognition of these pages again.
            ocr_cache.store(file_path, ocr_provider_name, ocr_new)

        page_count = document.page_count
        metadata = dict(document.metadata or {})
        document.close()
        if progress is not None:
            progress(92, "normalizing")

        # -- 2. find the running headers / footers ------------------------
        # They are marked, not removed: whether they take part in the
        # comparison is decided by the rules of the comparison, not by the
        # parser.
        dropped: set[tuple[int, int]] = set()
        dropped_texts: list[str] = []
        dropped = detect_running_text(
            [
                (page_number, page_lines[page_number], page_heights[page_number])
                for page_number in sorted(page_lines)
            ]
        )
        # The indices refer to the raw list: read the texts before the
        # list is filtered below.
        dropped_texts = [
            page_lines[number][index].text
            for number, index in sorted(dropped)
            if number in page_lines and index < len(page_lines[number])
        ]
        for number, index in dropped:
            if number in page_lines and index < len(page_lines[number]):
                page_lines[number][index].running = True

        # -- 3. drop the text that belongs to a table ---------------------
        stream_lines: dict[int, list[PdfLine]] = {}
        for page_number in sorted(page_lines):
            lines = list(page_lines[page_number])

            # text that the table finder already covers
            bboxes = [
                list(table.source_position.bbox or [])
                for table in page_tables[page_number]
                if len(table.rows) >= 2 and table.column_count >= 2
            ]
            bboxes = [bbox for bbox in bboxes if len(bbox) == 4]
            if bboxes:
                lines = [line for line in lines if not inside_any_table(line, bboxes)]

            # The character stream keeps the running headers (they are only
            # marked), the blocks do not contain them.
            stream_lines[page_number] = lines
            page_lines[page_number] = [line for line in lines if not line.running]

        # -- 4. group the lines into logical paragraphs --------------------
        all_lines = [line for page_number in sorted(page_lines) for line in page_lines[page_number]]
        size = body_font_size(all_lines)

        paragraphs: list[PdfParagraph] = []
        for page_number in sorted(page_lines):
            paragraphs.extend(group_lines(page_lines[page_number], size))

        # A paragraph may continue on the next page (PRD 8.2).
        extent = {
            page_number: (
                max((line.x1 for line in lines), default=0.0),
                page_widths.get(page_number, 0.0),
            )
            for page_number, lines in page_lines.items()
        }
        blocked = self._pages_ending_with_table(page_tables, page_heights)
        paragraphs = merge_across_pages(paragraphs, size, extent, blocked)
        assign_heading_levels(paragraphs)

        # -- 5. build the blocks in reading order --------------------------
        blocks: list[Block] = []
        sections: list[Section] = []
        current_section: Section | None = None
        order = 0
        table_count = 0
        line_to_block: dict[tuple[int, int], str] = {}

        for page_number in sorted(page_lines):
            page_lines_of_page = page_lines[page_number]
            page_items: list[tuple[int, str, object]] = []
            for paragraph in paragraphs:
                if paragraph.page != page_number:
                    continue
                page_items.append((paragraph.lines[0].index, "paragraph", paragraph))
            for table in page_tables[page_number]:
                bbox = list(table.source_position.bbox or [])
                top = bbox[1] if len(bbox) == 4 else 0.0
                position = min(
                    (
                        line.index
                        for line in page_lines_of_page
                        if line.y0 >= top
                    ),
                    default=self._fallback_position(page_lines_of_page),
                )
                page_items.append((position, "table", table))

            # The reading order of the page, not the vertical position.
            page_items.sort(key=lambda item: (item[0], item[1]))

            for _y, kind, payload in page_items:
                if kind == "table":
                    table = payload
                    text = _table_text(table)
                    blocks.append(
                        Block(
                            block_id=f"b_{order}",
                            type=BlockType.TABLE,
                            order=order,
                            section_id=current_section.section_id if current_section else None,
                            section_title=current_section.title if current_section else None,
                            original_text=text,
                            normalized_text=normalise(text, config),
                            source_position=SourcePosition(
                                page=page_number, bbox=list(table.source_position.bbox or [])
                            ),
                            table=table,
                        )
                    )
                    order += 1
                    table_count += 1
                    continue

                paragraph = payload
                block = Block(
                    block_id=f"b_{order}",
                    type=(
                        BlockType.HEADING
                        if paragraph.kind == "heading"
                        else BlockType.LIST
                        if paragraph.kind == "list"
                        else BlockType.PARAGRAPH
                    ),
                    order=order,
                    level=paragraph.level,
                    original_text=paragraph.text,
                    normalized_text=normalise(paragraph.text, config),
                    source_position=SourcePosition(
                        page=paragraph.page,
                        bbox=list(paragraph.bbox),
                        line=paragraph.lines[0].index if paragraph.lines else 0,
                    ),
                    metadata={"pages": paragraph.pages, "font_size": paragraph.size},
                )
                if block.type == BlockType.HEADING:
                    current_section = Section(
                        section_id=f"sec_{order}",
                        title=paragraph.text,
                        level=paragraph.level or 1,
                        order=len(sections),
                    )
                    sections.append(current_section)
                    block.section_id = current_section.section_id
                else:
                    block.section_id = current_section.section_id if current_section else None
                    block.section_title = current_section.title if current_section else None
                blocks.append(block)
                for line in paragraph.lines:
                    line_to_block[(line.page, line.index)] = block.block_id
                order += 1

        # -- 6. the character stream (阶段 2) --------------------------------
        chars, table_regions = self._build_char_stream(
            stream_lines, page_tables, line_to_block
        )
        pages, warnings = self._page_quality(
            page_lines,
            page_heights,
            page_widths,
            page_images,
            chars,
            scanned_pages,
            ocr_pages,
            ocr_confidence,
            page_rotations,
        )
        ocr_enabled = app_settings.get().enabled
        if ocr_pages:
            warnings.append(
                f"共 {len(ocr_pages)} 页没有文本层，内容由 OCR 识别"
            )
        elif scanned_pages and not ocr_enabled:
            warnings.append(
                "当前未配置 OCR：扫描页的文字未参与比对。"
                "可在「设置」中配置 OCR 渠道后重新比对。"
            )
        parsed_pdf = ParsedPdf(
            pages=pages,
            chars=chars,
            table_regions=table_regions,
            dropped_running=dropped_texts,
            warnings=warnings,
        )

        if empty_pages and len(empty_pages) == page_count:
            return ParseResult(
                file_type=self.file_type,
                content_type=ContentType.DOCUMENT,
                parser="pdf_parser",
                parse_status="unsupported_ocr",
                message=self._ocr_failure_message(
                    empty_pages, ocr_errors, ocr_limit_hit, ocr_budget, ocr_enabled
                ),
                metadata={
                    "page_count": page_count,
                    "empty_pages": empty_pages,
                    "scanned": True,
                    "ocr_configured": ocr_enabled,
                },
                uncovered=["扫描件 OCR"],
            )

        if scanned_pages and len(scanned_pages) == page_count:
            return ParseResult(
                file_type=self.file_type,
                content_type=ContentType.DOCUMENT,
                parser="pdf_parser",
                parse_status="unsupported_ocr",
                message=self._ocr_failure_message(
                    scanned_pages, ocr_errors, ocr_limit_hit, ocr_budget, ocr_enabled
                ),
                metadata={
                    "page_count": page_count,
                    "empty_pages": empty_pages,
                    "scanned_pages": scanned_pages,
                    "scanned": True,
                    "ocr_configured": ocr_enabled,
                    "ocr_errors": ocr_errors,
                },
                uncovered=["扫描件 OCR"],
            )

        model = Document(
            document_id=f"doc_{file_path.stem}",
            blocks=blocks,
            sections=sections,
            metadata={
                "page_count": page_count,
                "table_count": table_count,
                "paragraph_count": len(paragraphs),
            },
        )
        status = "success"
        message = None
        if empty_pages:
            status = "partial"
            message = f"第 {', '.join(str(p) for p in empty_pages[:10])} 页未识别到文本"
        if scanned_pages:
            status = "partial"
            message = self._ocr_failure_message(
                scanned_pages, ocr_errors, ocr_limit_hit, ocr_budget, ocr_enabled
            )

        return ParseResult(
            file_type=self.file_type,
            content_type=ContentType.DOCUMENT,
            parser="pdf_parser",
            parse_status=status,
            message=message,
            metadata={
                "page_count": page_count,
                "block_count": len(blocks),
                "paragraph_count": len(paragraphs),
                "table_count": table_count,
                "empty_pages": empty_pages,
                "scanned_pages": scanned_pages,
                "scanned": bool(scanned_pages or ocr_pages),
                "ocr_configured": ocr_enabled,
                "ocr_pages": sorted(ocr_pages),
                "ocr_errors": ocr_errors,
                "pdf_metadata": metadata,
            },
            document=model,
            uncovered=list(self.known_gaps),
            parsed_pdf=parsed_pdf,
        )

    @staticmethod
    def _ocr_failure_message(
        scanned_pages: list[int],
        ocr_errors: list[str],
        limit_hit: bool = False,
        budget: int = 0,
        ocr_enabled: bool = False,
    ) -> str:
        """Why the scanned pages could not take part in the comparison."""
        listed = ", ".join(str(page) for page in scanned_pages[:10])
        if limit_hit:
            return (
                f"第 {listed} 页及之后的页超出单文件最多识别 {budget} 页的限制，"
                "这些页面的内容未参与比对"
            )
        if ocr_errors:
            reason = ocr_errors[0]
            return (
                f"第 {listed} 页 OCR 识别失败，这些页面的内容未参与比对"
                f"（{reason}）"
            )
        if not ocr_enabled:
            # The clearest case: the file is a scan and nothing can read it.
            return (
                f"该 PDF 是扫描件（第 {listed} 页没有文本层），"
                "当前未配置 OCR，无法识别其中的文字。"
                "请在「设置」中配置 OCR 渠道后重新上传。"
            )
        return (
            f"第 {listed} 页疑似扫描件，未识别出文字，这些页面的内容未参与比对"
        )

    # -- the character stream ------------------------------------------------

    def _build_char_stream(
        self,
        page_lines: dict[int, list[PdfLine]],
        page_tables: dict[int, list[TableData]],
        line_to_block: dict[tuple[int, int], str],
    ) -> tuple[list[CharRef], list[TableRegion]]:
        """Flatten the pages into the characters of the document.

        The characters keep the order of the reading order of their page,
        the box they occupy on the page and the logical block they belong
        to.  The text of a table is not part of the stream: the table is
        recorded as a region instead.
        """
        chars: list[CharRef] = []
        regions: list[TableRegion] = []
        line_id = 0

        for page_number in sorted(page_lines):
            lines = page_lines[page_number]

            # How many characters come before each line of the page.
            offsets: list[int] = []
            count = 0
            for line in lines:
                offsets.append(count)
                count += len(line.chars)

            for table in page_tables.get(page_number, []):
                bbox = list(table.source_position.bbox or [])
                if len(bbox) != 4:
                    continue
                position = min(
                    (
                        offsets[index]
                        for index, line in enumerate(lines)
                        if line.y0 >= bbox[1] - 1
                    ),
                    default=count,
                )
                regions.append(
                    TableRegion(
                        table_id=table.table_id,
                        page=page_number,
                        bbox=bbox,
                        char_index=position,
                        kind="inferred" if table.note else "ruled",
                    )
                )

            for line in lines:
                line_id += 1
                block_id = line_to_block.get((page_number, line.index))
                for x0, y0, x1, y1, glyph in line.chars:
                    chars.append(
                        CharRef(
                            ch=glyph,
                            page=page_number,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                            line_id=line_id,
                            block_id=block_id,
                            running=line.running,
                        )
                    )

        return chars, regions

    def _page_quality(
        self,
        page_lines: dict[int, list[PdfLine]],
        page_heights: dict[int, float],
        page_widths: dict[int, float],
        page_images: dict[int, int],
        chars: list[CharRef],
        scanned_pages: list[int],
        ocr_pages: set[int] | None = None,
        ocr_confidence: dict[int, float] | None = None,
        page_rotations: dict[int, int] | None = None,
    ) -> tuple[list[PageInfo], list[str]]:
        """Rate the quality of every page (the hook for a later OCR stage)."""
        recognised = ocr_pages or set()
        confidences = ocr_confidence or {}
        rotations = page_rotations or {}
        per_page: dict[int, int] = {}
        garbled_per_page: dict[int, int] = {}
        for char in chars:
            # A running header is layout, not content: it does not count
            # towards the quality of the page.
            if char.running:
                continue
            per_page[char.page] = per_page.get(char.page, 0) + 1
            if _is_garbled(char.ch):
                garbled_per_page[char.page] = garbled_per_page.get(char.page, 0) + 1

        pages: list[PageInfo] = []
        warnings: list[str] = []
        for page_number in sorted(page_lines):
            count = per_page.get(page_number, 0)
            garbled = garbled_per_page.get(page_number, 0)
            flags: list[str] = []
            quality = "ok"
            source = "text"

            if page_number in recognised:
                # The page was recognised instead of read from a text layer.
                source = "ocr"
                flags.append("ocr")
                if confidences.get(page_number, 0.0) < OCR_LOW_CONFIDENCE:
                    quality = "warn"
                    warnings.append(
                        f"第 {page_number} 页的 OCR 识别置信度较低，比对结果可能不准确"
                    )
            elif page_number in scanned_pages:
                flags.append("scanned_like")
                quality = "bad"
            elif count and garbled / count > GARBLED_RATIO:
                flags.append("garbled_text")
                quality = "warn"
            elif count < SCANNED_CHAR_THRESHOLD and page_images.get(page_number):
                flags.append("scanned_like")
                quality = "warn"

            if "scanned_like" in flags:
                warnings.append(f"第 {page_number} 页疑似扫描件，未参与文本比对")
            if "garbled_text" in flags:
                warnings.append(f"第 {page_number} 页文本层存在乱码，比对结果可能不准确")

            pages.append(
                PageInfo(
                    page=page_number,
                    width=page_widths.get(page_number, 0.0),
                    height=page_heights.get(page_number, 0.0),
                    rotation=rotations.get(page_number, 0),
                    source=source,
                    quality=quality,
                    flags=flags,
                    char_count=count,
                )
            )
        return pages, warnings

    # -- helpers -------------------------------------------------------------

    def _image_count(self, page) -> int:
        try:
            return len(page.get_images(full=True) or [])
        except Exception:  # noqa: BLE001 - defensive
            return 0

    def _pages_ending_with_table(
        self,
        page_tables: dict[int, list[TableData]],
        page_heights: dict[int, float],
    ) -> set[int]:
        """Pages whose last element is a table at the bottom of the page.

        The paragraph above the table and the paragraph below it do not
        belong together, even when the typography suggests it.
        """
        blocked: set[int] = set()
        for page_number, tables in page_tables.items():
            height = page_heights.get(page_number, 0.0)
            if not height:
                continue
            for table in tables:
                bbox = list(table.source_position.bbox or [])
                if len(bbox) != 4:
                    continue
                if bbox[3] >= height * TABLE_BOTTOM_RATIO:
                    blocked.add(page_number)
        return blocked

    @staticmethod
    def _fallback_position(lines: list[PdfLine]) -> int:
        return max((line.index for line in lines), default=-1) + 1

    # -- tables ------------------------------------------------------------
    def _text_tables(
        self,
        page,
        page_number: int,
        existing: list[TableData],
    ) -> list[TableData]:
        """Tables that have no ruling lines, only aligned text columns.

        PRD 8.3: when the ruling lines are missing, the alignment of the
        text is used to recognise the table.
        """
        detected: list[DetectedTable] = detect_text_tables(page)
        if not detected:
            return []

        ruled = [
            list(table.source_position.bbox or [])
            for table in existing
            if len(table.source_position.bbox or []) == 4
        ]

        tables: list[TableData] = []
        for index, found in enumerate(detected):
            if any(_overlaps(found.bbox, bbox) for bbox in ruled):
                continue
            rows: list[TableRow] = []
            for row_index, values in enumerate(found.rows):
                cells: list[TableCell] = []
                for column_index, value in enumerate(values):
                    text = (value or "").strip()
                    cells.append(
                        TableCell(
                            cell_id=(
                                f"p{page_number}x{index}"
                                f"_r{row_index}_c{column_index}"
                            ),
                            row_index=row_index,
                            column_index=column_index,
                            original_text=text,
                            normalized_text=normalise(text),
                            value=text,
                        )
                    )
                rows.append(
                    TableRow(
                        row_id=f"p{page_number}x{index}_r{row_index}",
                        row_index=row_index,
                        cells=cells,
                    )
                )
            tables.append(
                TableData(
                    table_id=f"p{page_number}x{index}",
                    rows=rows,
                    source_position=SourcePosition(
                        page=page_number, bbox=list(found.bbox)
                    ),
                    note="按文本对齐推断的表格",
                )
            )
        return tables

    def _extract_tables(self, page, page_number: int) -> list[TableData]:
        try:
            finder = page.find_tables()
        except Exception:  # noqa: BLE001 - table detection is best effort
            return []

        tables: list[TableData] = []
        for table_index, found in enumerate(getattr(finder, "tables", []) or []):
            try:
                extracted = found.extract()
            except Exception:  # noqa: BLE001
                continue
            if not extracted or not any(any(c for c in row) for row in extracted):
                continue
            extracted = _ordered_table_cells(page, found, extracted)
            rows: list[TableRow] = []
            for row_index, row in enumerate(extracted):
                cells: list[TableCell] = []
                for column_index, value in enumerate(row):
                    text = (value or "").strip()
                    cells.append(
                        TableCell(
                            cell_id=f"p{page_number}t{table_index}_r{row_index}_c{column_index}",
                            row_index=row_index,
                            column_index=column_index,
                            original_text=text,
                            normalized_text=normalise(text),
                            value=text,
                        )
                    )
                rows.append(
                    TableRow(
                        row_id=f"p{page_number}t{table_index}_r{row_index}",
                        row_index=row_index,
                        cells=cells,
                    )
                )
            bbox = None
            if getattr(found, "bbox", None):
                bbox = _unrotated_bbox(page, list(found.bbox))
            tables.append(
                TableData(
                    table_id=f"p{page_number}t{table_index}",
                    rows=rows,
                    source_position=SourcePosition(page=page_number, bbox=bbox),
                )
            )
        return tables


def _ordered_table_cells(page, found, extracted: list[list[str]]) -> list[list[str]]:
    """The cells of a table in reading order.

    ``find_tables`` returns the cells in the order of the *rotated* page.
    On a rotated page that order is not the order of the text, so the cells
    are sorted by their unrotated position.
    """
    rotation = int(getattr(page, "rotation", 0) or 0) % 360
    if rotation == 0:
        return extracted
    cells: list[tuple[float, float, str]] = []
    for row_index, row in enumerate(getattr(found, "rows", []) or []):
        for column_index, rect in enumerate(getattr(row, "cells", []) or []):
            if rect is None:
                continue
            box = _unrotated_bbox(page, list(rect))
            text = ""
            if row_index < len(extracted) and column_index < len(extracted[row_index]):
                text = extracted[row_index][column_index] or ""
            cells.append((round(box[1], 1), box[0], text))
    if not cells:
        return extracted

    groups: dict[float, list[tuple[float, str]]] = {}
    for top, left, text in cells:
        groups.setdefault(top, []).append((left, text))
    return [
        [text for _left, text in sorted(group, key=lambda item: item[0])]
        for _top, group in sorted(groups.items())
    ]


def _unrotated_bbox(page, bbox: list[float]) -> list[float]:
    """The box of a table in the coordinate system of the text.

    ``find_tables`` works on the rotated page while the text coordinates do
    not follow the rotation: on a rotated page the table would sit
    somewhere else than the text it contains.
    """
    try:
        matrix = page.derotation_matrix
    except Exception:  # noqa: BLE001 - defensive
        matrix = None
    if matrix is None:
        return list(bbox)
    try:
        rect = fitz.Rect(bbox) * matrix
        rect.normalize()
        return [rect.x0, rect.y0, rect.x1, rect.y1]
    except Exception:  # noqa: BLE001 - defensive
        return list(bbox)


def _is_garbled(char: str) -> bool:
    """True for the characters that show that the text layer is broken."""
    if not char:
        return False
    code = ord(char)
    if 0xE000 <= code <= 0xF8FF:  # private use area
        return True
    if char == "�":
        return True
    return code < 0x20 and char not in "\t\n\r"


def _table_text(table: TableData) -> str:
    return "\n".join(
        "\t".join(cell.original_text for cell in row.cells) for row in table.rows
    )


def _overlaps(first: list[float], second: list[float]) -> bool:
    """True when two boxes overlap."""
    if len(first) < 4 or len(second) < 4:
        return False
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )
