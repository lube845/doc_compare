"""Comparison task endpoints (PRD chapter 13.4)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased

from ..db import ChangeEventRecord, Comparison, FileRecord, get_session
from ..domain.formats import (
    format_mismatch_message,
    is_spreadsheet_format,
    same_format,
)
from ..domain.models import ParseResult
from ..schemas import (
    ChangeEvent,
    ChangeSide,
    ComparisonConfigUpdate,
    ComparisonCreate,
    ComparisonResult,
    ComparisonSummary,
    CompareConfig,
    normalise_mode,
)
from ..services.cache import load_parse
from ..services.tasks import execute_comparison
from ..services.report import build_report
from ..storage import new_comparison_id

router = APIRouter(prefix="/api/comparisons", tags=["comparisons"])

def _summary(record: Comparison, session: Session) -> ComparisonSummary:
    file_a = session.get(FileRecord, record.file_a_id)
    file_b = session.get(FileRecord, record.file_b_id)
    return ComparisonSummary(
        comparison_id=record.comparison_id,
        file_a_id=record.file_a_id,
        file_b_id=record.file_b_id,
        file_a_name=file_a.filename if file_a else "",
        file_b_name=file_b.filename if file_b else "",
        mode=normalise_mode(record.mode),  # type: ignore[arg-type]
        status=record.status,
        stage=record.stage,
        progress=record.progress or 0,
        message=record.message,
        stats=dict(record.stats or {}),
        config=CompareConfig(**(record.config_json or {})),
        notices=list(record.notices or []),
        created_at=record.created_at,
        finished_at=record.finished_at,
    )


def _changes(session: Session, comparison_id: str) -> list[ChangeEvent]:
    records = (
        session.query(ChangeEventRecord)
        .filter_by(comparison_id=comparison_id)
        .order_by(ChangeEventRecord.order, ChangeEventRecord.change_id)
        .all()
    )
    events: list[ChangeEvent] = []
    for record in records:
        events.append(
            ChangeEvent(
                change_id=record.change_id.split(":")[-1],
                primary_type=record.primary_type,  # type: ignore[arg-type]
                category=record.category,
                document_a=ChangeSide(**(record.document_a or {})),
                document_b=ChangeSide(**(record.document_b or {})),
                summary=record.summary,
                details=record.details or {},
                order=record.order,
            )
        )
    return events


def _require_parse(session: Session, file_id: str) -> ParseResult:
    result = load_parse(session, file_id)
    if result is None:
        record = session.get(FileRecord, file_id)
        if record is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        raise HTTPException(
            status_code=409,
            detail=record.message or "文件解析结果不可用，请重新上传",
        )
    return result


@router.get("", response_model=list[ComparisonSummary])
def list_comparisons(
    session: Session = Depends(get_session),
    limit: int = Query(100, ge=1, le=500, description="返回数量"),
    offset: int = Query(0, ge=0, description="跳过数量"),
    keyword: str | None = Query(None, description="按文件名搜索"),
    status: str | None = Query(None, description="按任务状态筛选"),
) -> list[ComparisonSummary]:
    """Historical comparison records, newest first.

    The list is the entry point of the history view: every item carries the
    ``comparison_id`` needed to load the stored result again.
    """
    file_a = aliased(FileRecord)
    file_b = aliased(FileRecord)
    query = (
        session.query(Comparison)
        .outerjoin(file_a, Comparison.file_a_id == file_a.file_id)
        .outerjoin(file_b, Comparison.file_b_id == file_b.file_id)
    )

    if status and status != "all":
        query = query.filter(Comparison.status == status)
    if keyword:
        needle = f"%{keyword.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(file_a.filename).like(needle),
                func.lower(file_b.filename).like(needle),
            )
        )

    records = (
        query.order_by(Comparison.created_at.desc(), Comparison.comparison_id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_summary(record, session) for record in records]


@router.post("", response_model=ComparisonSummary, status_code=201)
def create_comparison(
    payload: ComparisonCreate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    wait: bool = Query(False, description="同步执行并直接返回结果"),
) -> ComparisonSummary:
    """Create a comparison task (PRD 13.4)."""
    file_a = session.get(FileRecord, payload.file_a_id)
    file_b = session.get(FileRecord, payload.file_b_id)
    if file_a is None or file_b is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    for record in (file_a, file_b):
        if record.parse_status in {"failed", "unsupported_ocr"}:
            raise HTTPException(
                status_code=400,
                detail=f"文件「{record.filename}」无法参与比对：{record.message or record.parse_status}",
            )

    # Only same-format comparisons are supported: a document and a
    # spreadsheet have nothing to align, so the pair is rejected here.
    if not same_format(file_a.file_type, file_b.file_type):
        raise HTTPException(
            status_code=400,
            detail=format_mismatch_message(file_a.file_type, file_b.file_type),
        )

    comparison = Comparison(
        comparison_id=new_comparison_id(),
        file_a_id=payload.file_a_id,
        file_b_id=payload.file_b_id,
        mode=(
            "spreadsheet_structure"
            if is_spreadsheet_format(file_a.file_type)
            else "document_structure"
        ),
        status="uploaded",
        config_json=payload.config.model_dump(),
        stats={},
        notices=[],
    )
    session.add(comparison)
    session.commit()

    if wait:
        execute_comparison(comparison.comparison_id)
        # The task writes through its own session - reload our copy.
        session.refresh(comparison)
    else:
        background.add_task(execute_comparison, comparison.comparison_id)
    return _summary(comparison, session)


@router.get("/{comparison_id}", response_model=ComparisonSummary)
def get_comparison(
    comparison_id: str, session: Session = Depends(get_session)
) -> ComparisonSummary:
    comparison = session.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="比对任务不存在")
    return _summary(comparison, session)


@router.get("/{comparison_id}/result", response_model=ComparisonResult)
def get_result(
    comparison_id: str, session: Session = Depends(get_session)
) -> ComparisonResult:
    comparison = session.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="比对任务不存在")

    changes = _changes(session, comparison_id)
    layout = comparison.page_mapping or {}
    file_a = session.get(FileRecord, comparison.file_a_id)
    file_b = session.get(FileRecord, comparison.file_b_id)

    def document(record: FileRecord | None, side: str) -> dict:
        if record is None:
            return {}
        return {
            "file_id": record.file_id,
            "file_name": record.filename,
            "file_type": record.file_type,
            "page_count": record.page_count,
            "size": record.size,
            "raw_url": f"/api/files/{record.file_id}/raw",
            "no_text_pages": list(layout.get(f"{side}_no_text_pages") or []),
        }

    return ComparisonResult(
        comparison_id=comparison_id,
        mode=normalise_mode(comparison.mode),  # type: ignore[arg-type]
        stats=dict(comparison.stats or {}),
        changes=changes,
        notices=list(comparison.notices or []),
        document_a=document(file_a, "left"),
        document_b=document(file_b, "right"),
        page_mapping=list(layout.get("pairs") or []),
    )


@router.get("/{comparison_id}/changes", response_model=list[ChangeEvent])
def list_changes(
    comparison_id: str,
    session: Session = Depends(get_session),
    change_type: str | None = Query(None, description="added/deleted/modified/moved"),
    sheet: str | None = Query(None, description="按工作表筛选"),
    section: str | None = Query(None, description="按章节筛选"),
    keyword: str | None = Query(None, description="关键词搜索"),
) -> list[ChangeEvent]:
    """Difference list with the filters of PRD 10.4."""
    comparison = session.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="比对任务不存在")

    events = _changes(session, comparison_id)

    if change_type and change_type != "all":
        events = [event for event in events if event.primary_type == change_type]
    if sheet:
        events = [
            event
            for event in events
            if event.document_a.location.sheet_name == sheet
            or event.document_b.location.sheet_name == sheet
        ]
    if section:
        events = [
            event
            for event in events
            if event.document_a.location.section == section
            or event.document_b.location.section == section
        ]
    if keyword:
        needle = keyword.lower()
        events = [
            event
            for event in events
            if needle in event.summary.lower()
            or needle in event.document_a.text.lower()
            or needle in event.document_b.text.lower()
        ]
    return events


@router.get("/{comparison_id}/changes/{change_id}", response_model=ChangeEvent)
def get_change(
    comparison_id: str, change_id: str, session: Session = Depends(get_session)
) -> ChangeEvent:
    for event in _changes(session, comparison_id):
        if event.change_id == change_id:
            return event
    raise HTTPException(status_code=404, detail="差异不存在")


@router.put("/{comparison_id}/config", response_model=ComparisonSummary)
def update_config(
    comparison_id: str,
    payload: ComparisonConfigUpdate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    wait: bool = Query(False, description="同步执行并直接返回结果"),
) -> ComparisonSummary:
    """Change the comparison rules and re-run the task (PRD 13.4)."""
    comparison = session.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="比对任务不存在")

    comparison.config_json = payload.config.model_dump()
    comparison.status = "uploaded"
    comparison.stage = None
    comparison.progress = 0
    # The task is waiting for a worker: nobody owns it any more.
    comparison.started_at = None
    session.commit()

    if wait:
        execute_comparison(comparison.comparison_id)
        session.expire_all()
    else:
        background.add_task(execute_comparison, comparison.comparison_id)
    return _summary(comparison, session)


@router.post("/{comparison_id}/export")
def export_report(
    comparison_id: str,
    session: Session = Depends(get_session),
    fmt: str = Query("markdown", alias="format"),
):
    """Export the difference report (PRD 13.4 / 10.4)."""
    comparison = session.get(Comparison, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="比对任务不存在")

    file_a = session.get(FileRecord, comparison.file_a_id)
    file_b = session.get(FileRecord, comparison.file_b_id)
    changes = _changes(session, comparison_id)

    report = build_report(
        comparison=comparison,
        file_a_name=file_a.filename if file_a else "",
        file_b_name=file_b.filename if file_b else "",
        changes=changes,
    )

    if fmt == "json":
        return JSONResponse(
            {
                "comparison_id": comparison_id,
                "stats": comparison.stats,
                "changes": [change.model_dump() for change in changes],
            }
        )
    if fmt == "html":
        return PlainTextResponse(
            report.to_html(), media_type="text/html; charset=utf-8"
        )
    return PlainTextResponse(
        report.to_markdown(), media_type="text/markdown; charset=utf-8"
    )
