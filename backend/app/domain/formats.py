"""文件格式相关的公共判断。

产品只支持**同格式**比对：两个文件的实际格式必须完全一致
（DOC↔DOC、DOCX↔DOCX、WPS↔WPS、PDF↔PDF、XLS↔XLS、XLSX↔XLSX、ET↔ET）。

格式不一致的组合在创建比对任务时就被拒绝（HTTP 400），因此比对引擎
永远不需要在两种不同结构的内容之间建立对应关系。
"""

from __future__ import annotations

#: 文档类格式（正文、标题、段落、表格）
DOCUMENT_FORMATS = frozenset({"doc", "docx", "wps", "pdf"})

#: 表格类格式（工作表、行、单元格）
SPREADSHEET_FORMATS = frozenset({"xls", "xlsx", "et"})

#: 全部受支持的格式
SUPPORTED_FORMATS = DOCUMENT_FORMATS | SPREADSHEET_FORMATS

#: 格式代码 → 展示名称
FORMAT_LABELS = {
    "doc": "DOC",
    "docx": "DOCX",
    "wps": "WPS",
    "et": "ET",
    "xls": "XLS",
    "xlsx": "XLSX",
    "pdf": "PDF",
}


def format_label(file_type: str | None) -> str:
    """Human readable name of a format code."""
    code = (file_type or "").strip().lower()
    return FORMAT_LABELS.get(code, code.upper() or "未知")


def is_spreadsheet_format(file_type: str | None) -> bool:
    """True when the format is one of the spreadsheet formats."""
    return (file_type or "").strip().lower() in SPREADSHEET_FORMATS


def same_format(type_a: str | None, type_b: str | None) -> bool:
    """True when the two files have the very same format."""
    return (type_a or "").strip().lower() == (type_b or "").strip().lower()


def format_mismatch_message(type_a: str | None, type_b: str | None) -> str:
    """The message shown when two files of different formats are paired."""
    return (
        f"仅支持同格式比对：文件 A 为 {format_label(type_a)}，"
        f"文件 B 为 {format_label(type_b)}，请上传两个格式相同的文件"
    )
