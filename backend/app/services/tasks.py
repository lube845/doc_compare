"""Background task execution (PRD 13.4).

The comparison and the file parsing run in the background: the HTTP request
only creates the task and returns.  This module owns what the background
worker does, so the API layer stays thin and the same code can be used again
when an interrupted task has to be recovered after a restart.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..db import Comparison, FileRecord, ParseTask, SessionLocal
from ..domain.models import ParseResult
from ..engines.dispatcher import UnsupportedPair
from ..parsers.base import ParseFailed, UnsupportedFormat
from ..services.cache import load_parse
from ..services.pipeline import parse_and_store, run_comparison
from . import leases

logger = logging.getLogger("doc_compare.tasks")

#: how long a comparison waits for its files to be parsed
PARSE_TIMEOUT = 1800.0

#: parse statuses that mean "the file is ready"
_PARSED = {"success", "partial"}
#: parse statuses that mean "the file will never be usable"
_PARSE_FAILED = {"failed", "unsupported_ocr"}
#: comparison statuses that mean "a worker was running this task"
COMPARISON_RUNNING = {
    "uploaded",
    "validating",
    "waiting_files",
    "parsing",
    "normalizing",
    "aligning",
    "comparing",
    "analyzing",
}


def wait_for_parse(session: Session, file_id: str) -> ParseResult:
    """Return the parse result of ``file_id``, waiting for a running parse.

    The parse of a file runs in the background, so a comparison that is
    started while a file is still being parsed waits for it instead of
    failing right away.
    """
    deadline = time.monotonic() + PARSE_TIMEOUT
    # How often an "uploaded" file was seen: the upload worker needs a moment
    # to pick the file up, only after that the parse may really be lost.
    idle_polls = 0
    while True:
        record = session.get(FileRecord, file_id)
        if record is None:
            raise HTTPException(status_code=404, detail="文件不存在")

        status = record.parse_status
        if status in _PARSED:
            result = load_parse(session, file_id)
            if result is not None:
                return result
            raise HTTPException(
                status_code=409,
                detail=record.message or "文件解析结果不可用，请重新上传",
            )
        if status in _PARSE_FAILED:
            raise HTTPException(
                status_code=409,
                detail=record.message or f"文件「{record.filename}」无法参与比对",
            )
        if status == "uploaded":
            idle_polls += 1
            if idle_polls >= 4:
                # The background parse never started (the upload task was lost
                # because the process restarted) - run it here instead.
                try:
                    return parse_and_store(session, record)
                except leases.AlreadyClaimed:
                    # Somebody else parses the file - keep waiting.
                    idle_polls = 0
                except (ParseFailed, UnsupportedFormat) as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            idle_polls = 0
        if time.monotonic() > deadline:
            raise HTTPException(status_code=409, detail="文件解析超时，请稍后重试")

        time.sleep(0.5)
        # Release the read transaction and drop the cached state, so the next
        # round sees the progress written by the parse worker.
        session.commit()
        session.expire_all()


def execute_comparison(
    comparison_id: str,
    from_status: str = "uploaded",
    expected_started_at: datetime | None = None,
) -> None:
    """Parse (if needed) and run the comparison in its own session.

    Background tasks run after the request scoped session has been closed,
    so a dedicated session is opened here.  The task is claimed first: the
    claim is a single conditional UPDATE, so exactly one worker runs it.
    """
    session = SessionLocal()
    heartbeat = None
    try:
        claim = leases.claim_comparison(
            session, comparison_id, from_status, expected_started_at
        )
        if claim is None:
            # Another worker owns the task.
            logger.info("比对任务 %s 已被其它进程认领，跳过", comparison_id)
            return
        session.expire_all()
        comparison = session.get(Comparison, comparison_id)
        if comparison is None:
            return
        heartbeat = leases.start_heartbeat("comparison", comparison_id)

        parse_a = wait_for_parse(session, comparison.file_a_id)
        parse_b = wait_for_parse(session, comparison.file_b_id)
        run_comparison(session, comparison, parse_a, parse_b)
    except HTTPException as exc:
        _mark_failed(session, comparison_id, str(exc.detail))
    except UnsupportedPair as exc:
        _mark_failed(session, comparison_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - a failure must not kill the task
        _mark_failed(session, comparison_id, f"比对失败：{exc}")
        logger.exception("比对任务 %s 执行失败", comparison_id)
    finally:
        if heartbeat is not None:
            heartbeat.set()
        session.close()


def _mark_failed(session: Session, comparison_id: str, message: str) -> None:
    """Record the failure of a task.

    Only a task that is still running is marked as failed: a task that
    somebody else owns must not be overwritten.
    """
    try:
        session.rollback()
        session.query(Comparison).filter(
            Comparison.comparison_id == comparison_id,
            Comparison.status.in_(list(COMPARISON_RUNNING)),
        ).update(
            {
                "status": "failed",
                "stage": "failed",
                "message": message,
                "finished_at": leases.now(),
            },
            synchronize_session=False,
        )
        session.commit()
    except Exception:  # noqa: BLE001 - the failure must be recorded, not raise
        logger.exception("记录任务 %s 的失败状态时出错", comparison_id)


def recover_interrupted_work() -> None:
    """Re-run the work that was interrupted by a restart.

    Background tasks live in the process that started them.  When the process
    stops, the tasks stop with it while their database state still says
    "running".  On startup these tasks are claimed and executed again; the
    claim is a conditional ``UPDATE`` on the status and the timestamp of the
    row, so concurrent workers cannot take the same task twice.  A task that
    is still running somewhere else keeps its timestamp fresh (heartbeat)
    and is never taken over.
    """
    _recover_parses()
    _recover_comparisons()


def _recover_parses() -> None:
    session = SessionLocal()
    try:
        stuck = (
            session.query(ParseTask)
            .filter(ParseTask.status.in_(["uploaded", "parsing", "recovering"]))
            .all()
        )
        for task in stuck:
            record = session.get(FileRecord, task.file_id)
            if record is None:
                continue
            try:
                parse_and_store(session, record)
            except leases.AlreadyClaimed:
                # The file is parsed somewhere else.
                continue
            except (ParseFailed, UnsupportedFormat) as exc:
                logger.warning("恢复解析 %s 失败：%s", record.filename, exc)
            except Exception:  # noqa: BLE001 - one file must not stop the rest
                logger.exception("恢复解析 %s 时发生意外错误", record.filename)
    finally:
        session.close()


def _recover_comparisons() -> None:
    session = SessionLocal()
    try:
        stuck = (
            session.query(Comparison)
            .filter(Comparison.status.in_(list(COMPARISON_RUNNING) + ["recovering"]))
            .all()
        )
        if not stuck:
            return
        logger.info("发现 %d 个中断的比对任务，重新执行", len(stuck))
        for comparison in stuck:
            observed_status = comparison.status
            observed_started = comparison.started_at
            if observed_status == "recovering":
                # Only a claim whose heartbeat stopped may be taken over.
                if observed_started is not None and (
                    leases.now() - observed_started
                ).total_seconds() < leases.RECOVERY_STALE_SECONDS:
                    continue
            claim = leases.claim_comparison(
                session,
                comparison.comparison_id,
                observed_status,
                observed_started,
                to_status="recovering",
            )
            if claim is None:
                continue
            execute_comparison(
                comparison.comparison_id,
                from_status="recovering",
                expected_started_at=claim,
            )
    finally:
        session.close()
