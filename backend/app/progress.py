"""Progress reporting.

Every long running step (parsing a file, aligning two documents) can report
its progress through a callback.  The callback receives the percentage that
is done and a short stage name, both of which are stored with the task so
that the frontend can show the state of a background job.
"""

from __future__ import annotations

from typing import Callable

#: ``progress(percent, stage)`` - percent is an integer between 0 and 100.
ProgressCallback = Callable[[int, str], None]


def report_to(callback: ProgressCallback | None, percent: int, stage: str) -> None:
    """Call ``callback`` if there is one."""
    if callback is None:
        return
    callback(max(0, min(100, int(percent))), stage)
