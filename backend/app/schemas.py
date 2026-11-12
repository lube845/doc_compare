"""Pydantic schemas: comparison configuration and API payloads.

The comparison configuration mirrors PRD chapter 5 (global config), 5.2
(document local config) and 5.3 (spreadsheet local config).  It is persisted
with every comparison so a result can be reproduced later (chapter 14.6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


class CompareConfig(BaseModel):
    """Global comparison rules (PRD 5.1)."""

    # global
    ignore_whitespace: bool = True
    ignore_newlines: bool = True
    ignore_headers_footers: bool = True
    ignore_formatting: bool = True
    ignore_punctuation: bool = False
    ignore_case: bool = False
    ignore_blank_cells: bool = False
    ignore_formula: bool = False
    ignore_full_width: bool = True

    # document local (PRD 5.2)
    ignore_numbering: bool = False
    detect_moved_paragraphs: bool = True

    # spreadsheet local (PRD 5.3)
    compare_formula: bool = True
    compare_display_value: bool = True
    compare_cell_format: bool = False
    compare_row_order: bool = True
    ignore_blank_rows: bool = True
    compare_sheet_order: bool = True

    # thresholds (PRD 6.2)
    high_similarity_threshold: float = 0.85
    medium_similarity_threshold: float = 0.60
    row_match_threshold: float = 0.75

    # PDF 比对（PDF 比对改造实施计划 · 阶段 4 / 12）
    #: PDF 对 PDF 时是否使用字符流比对（关闭后回退到旧的块比对）
    pdf_stream_mode: bool = True
    #: 解析后端：legacy / pymupdf4llm / mineru
    pdf_backend: str = "legacy"
    #: 间隙区对齐的最低相似度
    align_min_similarity: float = 0.5
    #: 全文锚点的长度（Token 数），0 表示关闭锚点
    pdf_anchor_shingle: int = 8
    #: 单个间隙区的最大句数
    gap_max_sentences: int = 300
    #: 相邻改动之间的等长片段不超过该长度时合并为一处
    merge_gap_chars: int = 3
    #: 改动字符占整句的比例超过该值时整句替换
    replace_whole_ratio: float = 0.5
    #: 移动检测的最低相似度
    move_min_similarity: float = 0.9
    #: 参与移动检测的最短文本长度
    move_min_length: int = 20
    #: 低于该置信度的差异需要人工确认
    low_conf_threshold: float = 0.6
    #: 展示粒度：char / change / sentence / block
    diff_granularity: str = "change"
    #: 页数上限
    max_pages: int = 500
    #: 单个任务的超时时间（秒）
    task_timeout_sec: int = 1800

    # user supplied hints
    primary_key_columns: list[str] = Field(default_factory=list)
    compare_sheet_names: list[str] = Field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return self.model_dump()


class ComparisonCreate(BaseModel):
    file_a_id: str
    file_b_id: str
    config: CompareConfig = Field(default_factory=CompareConfig)


class ComparisonConfigUpdate(BaseModel):
    config: CompareConfig


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

FileStatus = Literal[
    "uploaded",
    "validating",
    "parsing",
    "normalizing",
    "completed",
    "failed",
]


class FileInfo(BaseModel):
    file_id: str
    filename: str
    file_type: str
    detected_type: str | None = None
    content_type: str = "document"
    size: int = 0
    parse_status: str = "uploaded"
    parse_stage: str | None = None
    parse_progress: int = 0
    parser: str | None = None
    message: str | None = None
    page_count: int | None = None
    sheet_count: int | None = None
    block_count: int | None = None
    uncovered: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    format_mismatch: bool = False
    detected_extension: str | None = None


class FileUploadResponse(BaseModel):
    files: list[FileInfo]


class FilePreviewBlock(BaseModel):
    block_id: str
    type: str
    order: int
    text: str
    level: int | None = None
    section_title: str | None = None
    page: int | None = None
    table: dict[str, Any] | None = None
    #: Pictures inside the block (hash, size, type); the bytes stay in the
    #: original document and are shown by the original-layout view.
    images: list[dict[str, Any]] = Field(default_factory=list)


class FilePreview(BaseModel):
    file_id: str
    filename: str
    file_type: str
    content_type: str
    blocks: list[FilePreviewBlock] = Field(default_factory=list)
    sheets: list[dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Comparisons
# --------------------------------------------------------------------------

ComparisonStatus = Literal[
    "uploaded",
    "validating",
    "parsing",
    "normalizing",
    "aligning",
    "comparing",
    "analyzing",
    "completed",
    "partial_completed",
    "failed",
]

CompareMode = Literal[
    "document_structure",
    "spreadsheet_structure",
    #: 历史任务：跨格式比对已下线，旧记录不属于任何现有模式
    "legacy",
]

#: 可以新建的比对模式
ACTIVE_COMPARE_MODES = frozenset({"document_structure", "spreadsheet_structure"})


def normalise_mode(value: str | None) -> CompareMode:
    """Map a stored mode to one the API can serialise.

    Comparisons created before the cross-format engine was removed carry a
    mode that no longer exists; they are reported as legacy history.
    """
    return value if value in ACTIVE_COMPARE_MODES else "legacy"  # type: ignore[return-value]


class ComparisonSummary(BaseModel):
    comparison_id: str
    file_a_id: str
    file_b_id: str
    file_a_name: str = ""
    file_b_name: str = ""
    mode: CompareMode = "document_structure"
    status: str = "uploaded"
    stage: str | None = None
    progress: int = 0
    message: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    config: CompareConfig = Field(default_factory=CompareConfig)
    notices: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    finished_at: datetime | None = None


class ChangeLocation(BaseModel):
    page: int | None = None
    section: str | None = None
    block_id: str | None = None
    sheet_id: str | None = None
    sheet_name: str | None = None
    coordinate: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    row_key: str | None = None
    column_key: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ChangeSide(BaseModel):
    node_id: str | None = None
    text: str = ""
    highlight: str | None = None
    location: ChangeLocation = Field(default_factory=ChangeLocation)


class ChangeEvent(BaseModel):
    change_id: str
    primary_type: Literal["added", "deleted", "modified", "moved", "unchanged"]
    category: str = "text"
    document_a: ChangeSide = Field(default_factory=ChangeSide)
    document_b: ChangeSide = Field(default_factory=ChangeSide)
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    order: int = 0


class ComparisonResult(BaseModel):
    comparison_id: str
    mode: CompareMode = "document_structure"
    stats: dict[str, int] = Field(default_factory=dict)
    changes: list[ChangeEvent] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    document_a: dict[str, Any] = Field(default_factory=dict)
    document_b: dict[str, Any] = Field(default_factory=dict)
    #: PDF 展示：左右页面的对应关系（PDF 展示 §4.3）
    page_mapping: list[dict[str, Any]] = Field(default_factory=list)


class ExportRequest(BaseModel):
    format: Literal["markdown", "html", "json"] = "markdown"
