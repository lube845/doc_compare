"""Leases for the background work.

A task that is being worked on keeps a timestamp fresh (the heartbeat); a
task whose timestamp got old was interrupted by a restart and may be taken
over.  The claim itself is a single conditional UPDATE, so only one worker
can own a task - two workers that start at the same time cannot both run
the same comparison.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..db import Comparison, ParseTask, SessionLocal

logger = logging.getLogger("doc_compare.leases")

#: how often a running task refreshes its claim
HEARTBEAT_SECONDS = 30.0

#: how long a task may stay without a heartbeat before another worker is
#: allowed to take it over
RECOVERY_STALE_SECONDS = 300.0

#: the statuses of a task that is still being worked on
_ACTIVE_COMPARISON = {
    "uploaded",
    "validating",
    "waiting_files",
    "parsing",
    "normalizing",
    "aligning",
    "comparing",
    "analyzing",
    "recovering",
}
_ACTIVE_PARSE = {"uploaded", "parsing", "recovering"}


def now() -> datetime:
    """The current time in UTC.

    The timestamps are stored without a timezone (that is how the database
    keeps them), so they are compared in the same shape.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AlreadyClaimed(Exception):
    """Another worker owns the task."""


def stale_before() -> datetime:
    """The timestamp a task has to be older than to be taken over."""
    return now() - timedelta(seconds=RECOVERY_STALE_SECONDS)


def start_heartbeat(kind: str, item_id: str) -> threading.Event:
    """Keep the claim of a running task alive.

    The returned event stops the heartbeat; the caller has to set it when
    the work is done.
    """
    stop = threading.Event()

    def run() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            session = SessionLocal()
            try:
                if kind == "comparison":
                    row = session.get(Comparison, item_id)
                    if row is None or row.status not in _ACTIVE_COMPARISON:
                        return
                    session.query(Comparison).filter(
                        Comparison.comparison_id == item_id
                    ).update({"started_at": now()}, synchronize_session=False)
                else:
                    row = session.get(ParseTask, item_id)
                    if row is None or row.status not in _ACTIVE_PARSE:
                        return
                    session.query(ParseTask).filter(
                        ParseTask.task_id == item_id
                    ).update({"started_at": now()}, synchronize_session=False)
                session.commit()
            except Exception:  # noqa: BLE001 - the heartbeat must never die
                logger.exception("刷新任务租约失败：%s %s", kind, item_id)
            finally:
                session.close()

    threading.Thread(
        target=run, name=f"heartbeat-{kind}", daemon=True
    ).start()
    return stop


def claim_comparison(
    session: Session,
    comparison_id: str,
    from_status: str,
    expected_started_at: datetime | None = None,
    to_status: str = "parsing",
) -> datetime | None:
    """Claim a comparison for this worker.

    The claim is a conditional UPDATE on the status *and* the timestamp of
    the row, so exactly one worker can take a task.  A task that is still
    running somewhere else keeps its timestamp fresh through the heartbeat
    and is therefore never taken over.

    The timestamp of the claim is returned; it identifies the owner.
    """
    conditions = [
        Comparison.comparison_id == comparison_id,
        Comparison.status == from_status,
    ]
    if expected_started_at is None:
        conditions.append(Comparison.started_at.is_(None))
    else:
        conditions.append(Comparison.started_at == expected_started_at)

    claim_time = now()
    claimed = (
        session.query(Comparison)
        .filter(*conditions)
        .update(
            {
                "status": to_status,
                "stage": "waiting_files",
                "progress": 5,
                "started_at": claim_time,
            },
            synchronize_session=False,
        )
    )
    session.commit()
    return claim_time if claimed else None


def claim_parse_task(
    session: Session, task_id: str, allow_terminal: bool = False
) -> bool:
    """Claim the parse of a file for this worker.

    A task that was never started can always be taken; a task that is
    running somewhere else only when its heartbeat stopped.  A task that
    already finished is taken again only when the file itself says it was
    never parsed (a lost parse task).
    """
    conditions = [
        ParseTask.task_id == task_id,
        or_(
            ParseTask.status == "uploaded",
            and_(
                ParseTask.status.in_(["parsing", "recovering"]),
                or_(
                    ParseTask.started_at.is_(None),
                    ParseTask.started_at < stale_before(),
                ),
            ),
        ),
    ]
    if allow_terminal:
        conditions[1] = or_(
            conditions[1],
            ParseTask.status.in_(
                ["success", "partial", "failed", "unsupported_ocr"]
            ),
        )
    claimed = (
        session.query(ParseTask)
        .filter(*conditions)
        .update({"status": "parsing", "started_at": now()}, synchronize_session=False)
    )
    session.commit()
    return bool(claimed)