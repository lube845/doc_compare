"""File management endpoints (PRD chapter 4)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import FileRecord, ParseTask, SessionLocal, get_session
from ..domain.models import Cell, ParseResult
from ..parsers.base import ParseFailed, UnsupportedFormat
from ..parsers.detect import detect_file_type
from ..schemas import FileInfo, FilePreview, FilePreviewBlock
from ..services.cache import load_parse
from ..services.pipeline import parse_and_store
from ..services import leases
from ..services.render import DOCX_MEDIA_TYPE, RENDERABLE_TYPES, ensure_render_docx
from ..storage import checksum, save_upload

router = APIRouter(prefix="/api/files", tags=["files"])


def _file_info(record: FileRecord) -> FileInfo:
    return FileInfo(
        file_id=record.file_id,
        filename=record.filename,
        file_type=record.file_type,
        detected_type=record.detected_type,
        content_type=record.content_type,
        size=record.size,
        parse_status=record.parse_status,
        parse_stage=record.parse_task.stage if record.parse_task else None,
        parse_progress=record.parse_task.progress if record.parse_task else 0,
        parser=record.parser,
        message=record.message,
        page_count=record.page_count,
        sheet_count=record.sheet_count,
        block_count=record.block_count,
        uncovered=list(record.uncovered or []),
        created_at=record.created_at,
        format_mismatch=bool(record.format_mismatch),
        detected_extension=record.extension,
    )


def _preview_value(cell: Cell) -> str | None:
    """The text a user expects to see in the grid.

    For a formula cell the stored ``value`` is empty (the parser keeps the
    formula separately); the evaluated result lives in ``display_value``.
    """
    if cell.formula and cell.display_value is not None:
        return cell.display_value
    return cell.value


@router.post("/upload", response_model=FileInfo)
async def upload_file(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> FileInfo:
    """Upload a single file and validate it (PRD 4.1 / 4.2).

    The file is only stored here - the parsing runs in the background so
    that a large document does not block the upload.  The client polls
    ``GET /api/files/{file_id}`` for the parse status.
    """
    filename = file.filename or "unnamed"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if f".{extension}" not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式：.{extension}，"
            f"当前支持 {', '.join(sorted(settings.allowed_extensions))}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > settings.max_upload_size:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小超过限制（{settings.max_upload_size // 1024 // 1024} MB）",
        )

    file_id, path = save_upload(filename, data)
    detection = detect_file_type(path, filename)

    if detection.file_type == "encrypted":
        raise HTTPException(status_code=400, detail="文件已加密，请先解除密码保护")
    if detection.file_type == "unknown":
        raise HTTPException(
            status_code=400,
            detail="无法识别的文件格式，请确认文件是否完整",
        )

    record = FileRecord(
        file_id=file_id,
        filename=filename,
        stored_path=str(path),
        extension=extension,
        detected_type=detection.file_type,
        file_type=detection.file_type,
        content_type="document"
        if detection.file_type in {"doc", "docx", "wps", "pdf"}
        else "spreadsheet",
        size=len(data),
        checksum=checksum(data),
        parse_status="uploaded",
        format_mismatch=detection.mismatch,
        uncovered=[],
    )
    session.add(record)
    # Create the parse task up front: if the process dies before the
    # background parse starts, the startup recovery picks the task up.
    session.add(
        ParseTask(task_id=f"pt_{record.file_id}", file_id=record.file_id, status="uploaded")
    )
    session.commit()

    background.add_task(_parse_in_background, record.file_id)

    return _file_info(record)


def _parse_in_background(file_id: str) -> None:
    """Parse an uploaded file in the background.

    The request session is closed when the response is sent, so the parse
    runs with its own session.
    """
    session = SessionLocal()
    try:
        record = session.get(FileRecord, file_id)
        if record is None:
            return
        # Another worker (a task recovery or a comparison) may have taken the
        # file over already - the parse must run only once.
        if record.parse_status != "uploaded":
            return
        try:
            parse_and_store(session, record)
        except leases.AlreadyClaimed:
            # The file is parsed by another worker.
            return
        except (ParseFailed, UnsupportedFormat):
            session.refresh(record)
        except Exception as exc:  # noqa: BLE001 - keep the file readable
            session.rollback()
            record = session.get(FileRecord, file_id)
            if record is not None:
                record.parse_status = "failed"
                record.message = f"解析失败：{exc}"
                task = session.get(ParseTask, f"pt_{file_id}")
                if task is not None:
                    task.status = "failed"
                    task.stage = "failed"
                    task.error = record.message
                session.commit()
    finally:
        session.close()


@router.get("/{file_id}/raw")
def raw_file(file_id: str, session: Session = Depends(get_session)):
    """The original file, for the PDF viewer (PDF 展示 §3.3).

    The browser renders the PDF from this URL; the file is served inline
    and never modified.  Only PDFs are served - the other formats have
    their own preview.
    """
    record = session.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if record.file_type != "pdf":
        raise HTTPException(status_code=404, detail="仅 PDF 文件支持原始页面预览")

    path = Path(record.stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="原始文件不存在")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=record.filename,
        content_disposition_type="inline",
    )


@router.get("/{file_id}/render")
def render_file(file_id: str, session: Session = Depends(get_session)):
    """The DOCX container used to show a document in its original layout.

    ``docx`` uploads are served as they are; ``doc`` / ``wps`` uploads are
    converted once with LibreOffice and the result is cached (see
    :mod:`app.services.render`).  The original file is never modified.
    """
    record = session.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if record.file_type not in RENDERABLE_TYPES:
        raise HTTPException(status_code=404, detail="仅 Word 文档支持原貌预览")

    source = Path(record.stored_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="原始文件不存在")

    render_path = ensure_render_docx(source, file_id, record.file_type)
    if render_path is None:
        raise HTTPException(
            status_code=409,
            detail="无法生成原貌预览：文件格式不受支持或 LibreOffice 转换失败",
        )

    return FileResponse(
        render_path,
        media_type=DOCX_MEDIA_TYPE,
        filename=record.filename,
        content_disposition_type="inline",
    )


@router.get("/{file_id}", response_model=FileInfo)
def get_file(file_id: str, session: Session = Depends(get_session)) -> FileInfo:
    record = session.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return _file_info(record)


@router.get("/{file_id}/preview", response_model=FilePreview)
def preview_file(file_id: str, session: Session = Depends(get_session)) -> FilePreview:
    record = session.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    result = load_parse(session, file_id)
    if result is None:
        raise HTTPException(
            status_code=409,
            detail=record.message or "文件尚未解析，请重新上传",
        )

    blocks: list[FilePreviewBlock] = []
    if result.document is not None:
        for block in result.document.blocks:
            blocks.append(
                FilePreviewBlock(
                    block_id=block.block_id,
                    type=block.type.value,
                    order=block.order,
                    text=block.original_text,
                    level=block.level,
                    section_title=block.section_title,
                    page=block.source_position.page,
                    images=list(block.metadata.get("images") or []),
                    table=(
                        {
                            "table_id": block.table.table_id,
                            "rows": [
                                [cell.original_text for cell in row.cells]
                                for row in block.table.rows
                            ],
                            # Visual layout of the table (spans of merged
                            # cells); empty when the format does not provide
                            # one (native DOC reader, PDF tables).
                            "layout": block.table.layout,
                        }
                        if block.table
                        else None
                    ),
                )
            )

    sheets: list[dict] = []
    if result.workbook is not None:
        for sheet in result.workbook.sheets:
            sheets.append(
                {
                    "sheet_id": sheet.sheet_id,
                    "name": sheet.name,
                    "order": sheet.order,
                    "hidden": sheet.hidden,
                    "column_count": sheet.column_count,
                    # Formula cells store the formula, not the value.  The
                    # grid must show the evaluated (cached) result, otherwise
                    # every formula cell renders as an empty cell.
                    "rows": [
                        [_preview_value(cell) for cell in row.cells]
                        for row in sheet.rows
                    ],
                    "formulas": [
                        [cell.formula for cell in row.cells] for row in sheet.rows
                    ],
                    "cell_ids": [
                        [cell.cell_id for cell in row.cells] for row in sheet.rows
                    ],
                }
            )

    return FilePreview(
        file_id=file_id,
        filename=record.filename,
        file_type=record.file_type,
        content_type=record.content_type,
        blocks=blocks,
        sheets=sheets,
    )
