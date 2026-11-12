"""WPS 表格 (``.et``) parser.

Resolution order mirrors the WPS writer parser: LibreOffice -> OOXML ->
best effort OLE2 recovery.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..domain.models import (
    Cell,
    ContentType,
    ParseResult,
    Sheet,
    SheetRow,
    SourcePosition,
    Workbook,
)
from ..domain.normalize import normalise
from ..schemas import CompareConfig
from . import libreoffice
from .base import BaseParser, ParseFailed, ProgressCallback
from .cfb import CompoundFile, CFBError
from .heuristic import readable_runs

_SPLIT_RE = re.compile(r"[\t\r\n]+")


class EtParser(BaseParser):
    file_type = "et"
    display_name = "WPS 表格"
    content_type = ContentType.SPREADSHEET.value
    known_gaps = ("公式文本", "单元格样式", "工作表内部结构")

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        config = options or CompareConfig()

        if libreoffice.is_available():
            converted = libreoffice.convert_to_ooxml(file_path, "et")
            if converted is not None:
                from .excel_parser import XlsxParser

                result = XlsxParser().parse(converted, config, progress)
                result.file_type = self.file_type
                result.parser = "et_parser"
                result.uncovered = list(self.known_gaps)
                return result

        if zipfile.is_zipfile(file_path):
            from .excel_parser import XlsxParser

            result = XlsxParser().parse(file_path, config, progress)
            result.file_type = self.file_type
            result.parser = "et_parser"
            result.uncovered = list(self.known_gaps)
            return result

        return self._parse_ole(file_path, config)

    # -- fallback ----------------------------------------------------------
    def _parse_ole(self, file_path: Path, config: CompareConfig) -> ParseResult:
        try:
            data = file_path.read_bytes()
            cfb = CompoundFile(data)
        except (CFBError, OSError) as exc:
            raise ParseFailed(f"无法读取 ET 文件：{exc}") from exc

        rows_text: list[str] = []
        for name, blob in cfb.iter_streams():
            if len(blob) < 32:
                continue
            runs = readable_runs(blob)
            for run in runs:
                rows_text.extend(_SPLIT_RE.split(run))

        rows_text = [row.strip() for row in rows_text if row.strip()]
        if not rows_text:
            raise ParseFailed(
                "ET 文件解析失败：未能提取到表格内容，建议安装 LibreOffice 以获得完整解析"
            )

        sheet = _sheet_from_rows(rows_text, 0)
        model = Workbook(
            workbook_id=f"wb_{file_path.stem}",
            sheets=[sheet],
            metadata={"sheet_count": 1},
        )
        return ParseResult(
            file_type=self.file_type,
            content_type=ContentType.SPREADSHEET,
            parser="et_parser",
            parse_status="partial",
            message="ET 文件通过兼容模式解析，工作表与公式结构可能不完整",
            metadata={"sheet_count": 1},
            workbook=model,
            uncovered=list(self.known_gaps),
        )


def _sheet_from_rows(rows_text: list[str], index: int) -> Sheet:
    rows: list[SheetRow] = []
    max_column = 0
    for row_index, row_text in enumerate(rows_text):
        values = row_text.split("\t")
        max_column = max(max_column, len(values))
        cells: list[Cell] = []
        for column_index, value in enumerate(values):
            cells.append(
                Cell(
                    cell_id=f"s{index}_r{row_index}_c{column_index}",
                    coordinate=f"R{row_index + 1}C{column_index + 1}",
                    row_index=row_index,
                    column_index=column_index,
                    value=value,
                    normalized_value=normalise(value),
                    source_position=SourcePosition(
                        row_index=row_index,
                        column_index=column_index,
                    ),
                )
            )
        rows.append(SheetRow(row_id=f"s{index}_r{row_index}", index=row_index, cells=cells))
    return Sheet(
        sheet_id=f"s{index}",
        name="Sheet1",
        order=index,
        rows=rows,
        column_count=max_column,
        metadata={"parser": "heuristic-ole"},
    )
