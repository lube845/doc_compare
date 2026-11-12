"""Spreadsheet comparison acceptance tests (PRD 17.3)."""

from __future__ import annotations

from app.engines.spreadsheet_diff import diff_workbooks
from app.parsers.registry import parse_file
from app.schemas import CompareConfig

HEADERS = ["编号", "名称", "金额"]
ROWS = [
    ["001", "项目A", 100],
    ["002", "项目B", 200],
    ["003", "项目C", 300],
]


def build(sheets: dict):
    import openpyxl
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "book.xlsx"
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(title=name)
        for row in rows:
            worksheet.append(row)
    workbook.save(str(path))
    return path


def compare(sheets_a: dict, sheets_b: dict, formulas_a=None, formulas_b=None):
    config = CompareConfig()
    path_a = build(sheets_a)
    path_b = build(sheets_b)
    result_a = parse_file(path_a, "a.xlsx", config)
    result_b = parse_file(path_b, "b.xlsx", config)
    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    return changes


def types_of(changes) -> list[str]:
    return [change.primary_type for change in changes]


def test_added_row():
    rows = [HEADERS, *ROWS, ["004", "项目D", 400]]
    changes = compare({"Sheet1": [HEADERS, *ROWS]}, {"Sheet1": rows})
    assert "added" in types_of(changes)
    added = [c for c in changes if c.primary_type == "added"][0]
    assert "004" in added.document_b.text


def test_deleted_row():
    changes = compare(
        {"Sheet1": [HEADERS, *ROWS]}, {"Sheet1": [HEADERS, ROWS[0], ROWS[2]]}
    )
    assert "deleted" in types_of(changes)


def test_modified_cell():
    changed = [HEADERS, ROWS[0], ["002", "项目B", 250], ROWS[2]]
    changes = compare({"Sheet1": [HEADERS, *ROWS]}, {"Sheet1": changed})
    assert "modified" in types_of(changes)
    modified = [c for c in changes if c.primary_type == "modified"][0]
    assert modified.document_a.text.strip() == "200"
    assert modified.document_b.text.strip() == "250"


def test_added_column():
    rows = [[*row, "备注"] for row in ROWS]
    changes = compare({"Sheet1": [HEADERS, *ROWS]}, {"Sheet1": [[*HEADERS, "备注"], *rows]})
    assert "added" in types_of(changes)
    assert any(c.category == "column" for c in changes)


def test_deleted_column():
    changes = compare(
        {"Sheet1": [HEADERS, *ROWS]},
        {"Sheet1": [["编号", "名称"], *[row[:2] for row in ROWS]]},
    )
    assert any(c.category == "column" for c in changes)


def test_column_order_change():
    swapped = [["名称", "编号", "金额"], ["项目A", "001", 100], ["项目B", "002", 200], ["项目C", "003", 300]]
    changes = compare({"Sheet1": [HEADERS, *ROWS]}, {"Sheet1": swapped})
    # The headers are matched by name, so only the values move - which is
    # exactly the behaviour we want (no false "modified" on every cell).
    assert not any(c.category == "column" for c in changes)


def test_sheet_order_change():
    a = {"Sheet1": [HEADERS, *ROWS], "Sheet2": [["x"]]}
    b = {"Sheet2": [["x"]], "Sheet1": [HEADERS, *ROWS]}
    changes = compare(a, b)
    assert any(c.category == "sheet" and c.primary_type == "moved" for c in changes)


def test_sheet_added_and_deleted():
    a = {"Sheet1": [HEADERS, *ROWS]}
    b = {"Sheet1": [HEADERS, *ROWS], "Sheet2": [["x"]]}
    changes = compare(a, b)
    assert any(c.category == "sheet" and c.primary_type == "added" for c in changes)

    changes = compare(b, a)
    assert any(c.category == "sheet" and c.primary_type == "deleted" for c in changes)


def test_formula_change():
    import openpyxl
    import tempfile
    from pathlib import Path

    config = CompareConfig()
    path_a = Path(tempfile.mkdtemp()) / "a.xlsx"
    path_b = Path(tempfile.mkdtemp()) / "b.xlsx"

    for path, formula in ((path_a, "=SUM(A1:A5)"), (path_b, "=SUM(A1:A6)")):
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "Sheet1"
        worksheet.append(["项目", "合计"])
        worksheet.append(["A", 100])
        worksheet["B2"] = formula
        workbook.save(str(path))

    result_a = parse_file(path_a, "a.xlsx", config)
    result_b = parse_file(path_b, "b.xlsx", config)
    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    assert any(c.category == "formula" for c in changes), changes


def test_same_display_value_but_different_formula():
    """Case 10 - the formula changed although the cached value did not."""
    import openpyxl
    import tempfile
    from pathlib import Path

    config = CompareConfig()
    path_a = Path(tempfile.mkdtemp()) / "a.xlsx"
    path_b = Path(tempfile.mkdtemp()) / "b.xlsx"

    for path, formula in ((path_a, "=SUM(A1:A5)"), (path_b, "=SUM(A1:A6)")):
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "Sheet1"
        worksheet.append(["项目", "合计"])
        for index in range(1, 6):
            worksheet.append([f"第{index}项", 20])
        worksheet["B7"] = formula
        workbook.save(str(path))

    result_a = parse_file(path_a, "a.xlsx", config)
    result_b = parse_file(path_b, "b.xlsx", config)
    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    formula_changes = [c for c in changes if c.category == "formula"]
    assert formula_changes, "显示值相同但公式不同，应识别为公式变化"


def test_inserted_row_does_not_cascade():
    """Core acceptance case of PRD 17.3 - 1000 rows with one insertion."""
    header = ["编号", "名称", "金额"]
    rows_a = [[f"{i:04d}", f"记录{i}", i * 10] for i in range(1000)]
    rows_b = list(rows_a)
    rows_b.insert(20, ["9999", "新增记录", 999])

    changes = compare({"Sheet1": [header, *rows_a]}, {"Sheet1": [header, *rows_b]})
    added = [c for c in changes if c.primary_type == "added"]
    modified = [c for c in changes if c.primary_type == "modified"]
    assert len(added) == 1, f"应只识别出一行新增，实际 {len(added)} 行"
    assert len(modified) == 0, f"后续 980 行不应判定为修改，实际 {len(modified)} 行"


def _parse_formula_pair(formula_a: str, formula_b: str):
    """Build two one-formula workbooks and parse them."""
    import openpyxl
    import tempfile
    from pathlib import Path

    config = CompareConfig()
    paths = []
    for formula in (formula_a, formula_b):
        path = Path(tempfile.mkdtemp()) / "book.xlsx"
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "Sheet1"
        worksheet.append(["项目", "合计"])
        worksheet["B2"] = formula
        workbook.save(str(path))
        paths.append(path)
    result_a = parse_file(paths[0], "a.xlsx", config)
    result_b = parse_file(paths[1], "b.xlsx", config)
    return result_a, result_b, config


def test_formula_and_result_both_different_yield_two_results():
    """Formula changed *and* evaluated result changed → two findings."""
    result_a, result_b, config = _parse_formula_pair("=SUM(A1:A5)", "=SUM(A1:A6)")
    cell_a = result_a.workbook.sheets[0].rows[1].cells[1]
    cell_b = result_b.workbook.sheets[0].rows[1].cells[1]
    cell_a.display_value = "100"
    cell_b.display_value = "200"

    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    categories = [change.category for change in changes]
    assert categories.count("formula") == 1, categories
    assert categories.count("value") == 1, categories
    value_change = next(c for c in changes if c.category == "value")
    assert "取值结果变化" in value_change.summary
    assert "100" in value_change.summary and "200" in value_change.summary


def test_same_formula_but_different_result_is_a_difference():
    """Same formula but different evaluated result must not be "unchanged"."""
    result_a, result_b, config = _parse_formula_pair("=SUM(A1:A5)", "=SUM(A1:A5)")
    cell_a = result_a.workbook.sheets[0].rows[1].cells[1]
    cell_b = result_b.workbook.sheets[0].rows[1].cells[1]
    cell_a.display_value = "100"
    cell_b.display_value = "200"

    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    assert [change.category for change in changes] == ["value"]
    assert "取值结果变化" in changes[0].summary


def test_different_formula_with_same_result_is_only_a_formula_difference():
    """Formula changed but the evaluated result is equal → one formula finding."""
    result_a, result_b, config = _parse_formula_pair("=SUM(A1:A5)", "=SUM(A1:A6)")
    cell_a = result_a.workbook.sheets[0].rows[1].cells[1]
    cell_b = result_b.workbook.sheets[0].rows[1].cells[1]
    cell_a.display_value = "100"
    cell_b.display_value = "100"

    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    assert [change.category for change in changes] == ["formula"]


def test_formula_replacing_equal_literal_is_only_a_formula_result():
    """A literal 3 vs a formula showing 3 is one finding, not a 3 → 3 value change."""
    import openpyxl
    import tempfile
    from pathlib import Path

    config = CompareConfig()
    path_a = Path(tempfile.mkdtemp()) / "a.xlsx"
    path_b = Path(tempfile.mkdtemp()) / "b.xlsx"

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["项目", "合计"])
    worksheet.append(["A", 3])
    workbook.save(str(path_a))

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["项目", "合计"])
    worksheet["A2"] = "A"
    worksheet["B2"] = "=1+2"
    workbook.save(str(path_b))

    result_a = parse_file(path_a, "a.xlsx", config)
    result_b = parse_file(path_b, "b.xlsx", config)
    result_b.workbook.sheets[0].rows[1].cells[1].display_value = "3"

    changes, _ = diff_workbooks(result_a.workbook, result_b.workbook, config)
    assert [change.category for change in changes] == ["formula"], [
        change.summary for change in changes
    ]


def test_merged_title_row_above_header_is_not_mistaken_for_header():
    """A merged title row above the header must not break column alignment.

    Regression: the engine used row 0 as the header, so a workbook with a
    merged title in row 1 and the real header in row 2 compared every data
    cell against the wrong column (identical uploads reported ~240 false
    differences).
    """
    import openpyxl
    import tempfile
    from pathlib import Path

    config = CompareConfig()
    path = Path(tempfile.mkdtemp()) / "same.xlsx"

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["员工名单"])
    worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=3)
    worksheet.append(["序号", "部门", "姓名"])
    for index in range(1, 6):
        worksheet.append([str(index), "信息科技部", f"员工{index}"])
    workbook.save(str(path))

    result_a = parse_file(path, "a.xlsx", config)
    result_b = parse_file(path, "b.xlsx", config)
    changes, notices = diff_workbooks(result_a.workbook, result_b.workbook, config)
    assert changes == [], [c.summary for c in changes]
    assert any("完全一致" in notice for notice in notices)


def test_different_format_same_content(xls_available):
    """Case 11 - XLS and XLSX with the same content."""
    import xlrd

    if not xls_available:
        import pytest

        pytest.skip("xlrd 不可用，无法读取 XLS")

    import subprocess
    import tempfile
    from pathlib import Path

    rows = [HEADERS, *ROWS]
    path_xlsx = build({"Sheet1": rows})

    # Write a real XLS file with xlwt when available, otherwise skip.
    try:
        import xlwt
    except ImportError:
        import pytest

        pytest.skip("xlwt 不可用，无法生成 XLS 样本")

    path_xls = Path(tempfile.mkdtemp()) / "a.xls"
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("Sheet1")
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            sheet.write(row_index, column_index, value)
    workbook.save(str(path_xls))

    config = CompareConfig()
    result_xlsx = parse_file(path_xlsx, "a.xlsx", config)
    result_xls = parse_file(path_xls, "a.xls", config)
    changes, _ = diff_workbooks(result_xlsx.workbook, result_xls.workbook, config)
    assert changes == []
