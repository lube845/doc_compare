"""Spreadsheet parsers for ``.xlsx`` (openpyxl) and ``.xls`` (xlrd).

Both produce the same :class:`Workbook` model so the spreadsheet diff engine
never needs to know which format a workbook came from (PRD chapter 7).
"""

from __future__ import annotations

import datetime as dt
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
from .base import BaseParser, ParseFailed, ProgressCallback


def _cell_id(sheet_index: int, row: int, column: int) -> str:
    return f"c{sheet_index}_r{row}_c{column}"


class XlsxParser(BaseParser):
    file_type = "xlsx"
    display_name = "Microsoft Excel (OOXML)"
    content_type = ContentType.SPREADSHEET.value
    known_gaps = ("数据透视表内部结构", "Power Query", "复杂图表对象", "嵌入式对象")

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        import openpyxl

        try:
            workbook = openpyxl.load_workbook(
                str(file_path), data_only=False, rich_text=False
            )
            cached = openpyxl.load_workbook(
                str(file_path), data_only=True, rich_text=False
            )
        except Exception as exc:  # noqa: BLE001 - openpyxl raises many types
            raise ParseFailed(f"无法解析 XLSX 文件：{exc}") from exc

        sheets: list[Sheet] = []
        for index, worksheet in enumerate(workbook.worksheets):
            if progress is not None:
                progress(10 + int(80 * index / max(1, len(workbook.worksheets))), "parsing")
            cached_sheet = cached[worksheet.title] if worksheet.title in cached.sheetnames else None
            sheet = _sheet_from_openpyxl(worksheet, cached_sheet, index)
            sheets.append(sheet)
        if progress is not None:
            progress(92, "normalizing")

        model = Workbook(
            workbook_id=f"wb_{file_path.stem}",
            sheets=sheets,
            metadata={"sheet_count": len(sheets)},
        )
        return ParseResult(
            file_type=self.file_type,
            content_type=ContentType.SPREADSHEET,
            parser="excel_parser",
            parse_status="success",
            metadata={"sheet_count": len(sheets)},
            workbook=model,
            uncovered=list(self.known_gaps),
        )


def _sheet_from_openpyxl(worksheet, cached_sheet, index: int) -> Sheet:
    rows: list[SheetRow] = []
    max_column = 0
    for row_index, row in enumerate(worksheet.iter_rows()):
        cells: list[Cell] = []
        for column_index, raw in enumerate(row):
            max_column = max(max_column, column_index + 1)
            value = raw.value
            formula = None
            display = None
            if isinstance(value, str) and value.startswith("="):
                formula = value
                value = None
            if cached_sheet is not None:
                try:
                    display = cached_sheet.cell(
                        row=row_index + 1, column=column_index + 1
                    ).value
                except Exception:  # noqa: BLE001
                    display = None
            text = _stringify(value)
            cells.append(
                Cell(
                    cell_id=_cell_id(index, row_index, column_index),
                    coordinate=raw.coordinate,
                    row_index=row_index,
                    column_index=column_index,
                    value=text,
                    normalized_value=normalise(text),
                    formula=formula,
                    display_value=_stringify(display) if display is not None else None,
                    data_type=raw.data_type,
                    number_format=raw.number_format,
                    style_id=str(raw.style_id) if raw.style_id is not None else None,
                    source_position=SourcePosition(
                        sheet_name=worksheet.title,
                        row_index=row_index,
                        column_index=column_index,
                        coordinate=raw.coordinate,
                    ),
                )
            )
        rows.append(SheetRow(row_id=f"s{index}_r{row_index}", index=row_index, cells=cells))

    hidden = getattr(worksheet, "sheet_state", "visible") != "visible"
    return Sheet(
        sheet_id=f"s{index}",
        name=worksheet.title,
        order=index,
        rows=rows,
        column_count=max_column,
        hidden=hidden,
        metadata={"dimensions": worksheet.dimensions},
    )


class XlsParser(BaseParser):
    file_type = "xls"
    display_name = "Microsoft Excel 97-2003"
    content_type = ContentType.SPREADSHEET.value
    known_gaps = ("公式文本", "单元格样式", "数据透视表内部结构")

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        import xlrd

        try:
            book = xlrd.open_workbook(str(file_path))
        except Exception as exc:  # noqa: BLE001
            raise ParseFailed(f"无法解析 XLS 文件：{exc}") from exc

        sheets: list[Sheet] = []
        for index, sheet in enumerate(book.sheets()):
            rows: list[SheetRow] = []
            max_column = sheet.ncols
            for row_index in range(sheet.nrows):
                cells: list[Cell] = []
                for column_index in range(sheet.ncols):
                    raw = sheet.cell(row_index, column_index)
                    value = _xlrd_value(raw, book)
                    text = _stringify(value)
                    cells.append(
                        Cell(
                            cell_id=_cell_id(index, row_index, column_index),
                            coordinate=_coordinate(row_index, column_index),
                            row_index=row_index,
                            column_index=column_index,
                            value=text,
                            normalized_value=normalise(text),
                            data_type=_xlrd_type(raw.ctype),
                            source_position=SourcePosition(
                                sheet_name=sheet.name,
                                row_index=row_index,
                                column_index=column_index,
                                coordinate=_coordinate(row_index, column_index),
                            ),
                        )
                    )
                rows.append(
                    SheetRow(row_id=f"s{index}_r{row_index}", index=row_index, cells=cells)
                )
            hidden = getattr(sheet, "visibility", 0) != 0
            sheets.append(
                Sheet(
                    sheet_id=f"s{index}",
                    name=sheet.name,
                    order=index,
                    rows=rows,
                    column_count=max_column,
                    hidden=hidden,
                )
            )

        model = Workbook(
            workbook_id=f"wb_{file_path.stem}", sheets=sheets,
            metadata={"sheet_count": len(sheets)},
        )
        return ParseResult(
            file_type=self.file_type,
            content_type=ContentType.SPREADSHEET,
            parser="excel_parser",
            parse_status="success",
            metadata={"sheet_count": len(sheets)},
            workbook=model,
            uncovered=list(self.known_gaps),
        )


def _xlrd_value(raw, book):
    import xlrd

    if raw.ctype == xlrd.XL_CELL_EMPTY:
        return None
    if raw.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(raw.value)
    if raw.ctype == xlrd.XL_CELL_DATE:
        try:
            parts = xlrd.xldate_as_tuple(raw.value, book.datemode)
            return dt.datetime(*parts)
        except Exception:  # noqa: BLE001
            return raw.value
    if raw.ctype == xlrd.XL_CELL_ERROR:
        return None
    if raw.ctype == xlrd.XL_CELL_NUMBER:
        number = raw.value
        if number == int(number):
            return int(number)
        return number
    return raw.value


def _xlrd_type(ctype: int) -> str:
    import xlrd

    mapping = {
        xlrd.XL_CELL_EMPTY: "n",
        xlrd.XL_CELL_TEXT: "s",
        xlrd.XL_CELL_NUMBER: "n",
        xlrd.XL_CELL_DATE: "d",
        xlrd.XL_CELL_BOOLEAN: "b",
        xlrd.XL_CELL_ERROR: "e",
    }
    return mapping.get(ctype, "n")


def _stringify(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0, 0):
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return repr(value)
    return str(value)


def _coordinate(row_index: int, column_index: int) -> str:
    letters = ""
    column = column_index + 1
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row_index + 1}"
