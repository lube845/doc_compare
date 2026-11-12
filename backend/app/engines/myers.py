"""Myers diff over token sequences (PRD 6.3).

The implementation is the classic O(ND) greedy algorithm with a recorded
trace, which allows backtracking into a minimal edit script.
"""

from __future__ import annotations

from dataclasses import dataclass

EQUAL = "equal"
DELETE = "delete"
INSERT = "insert"


@dataclass
class Hunk:
    """A contiguous region of the diff."""

    tag: str
    a_start: int
    a_end: int
    b_start: int
    b_end: int

    @property
    def is_change(self) -> bool:
        return self.tag != EQUAL


def myers(a: list[str], b: list[str]) -> list[Hunk]:
    """Return the diff hunks between token lists ``a`` and ``b``."""
    n, m = len(a), len(b)
    if n == 0 and m == 0:
        return []
    if n == 0:
        return [Hunk(INSERT, 0, 0, 0, m)]
    if m == 0:
        return [Hunk(DELETE, 0, n, 0, 0)]

    max_d = n + m
    v: dict[int, int] = {1: 0}
    trace: list[dict[int, int]] = []

    for d in range(max_d + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v.get(k - 1, 0) < v.get(k + 1, 0)):
                x = v.get(k + 1, 0)
            else:
                x = v.get(k - 1, 0) + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                return _backtrack(trace, a, b, d)

    # Unreachable in practice - the loop always terminates above.
    return [Hunk(DELETE, 0, n, 0, 0), Hunk(INSERT, 0, 0, 0, m)]


def _backtrack(trace: list[dict[int, int]], a: list[str], b: list[str], d: int) -> list[Hunk]:
    x, y = len(a), len(b)
    ops: list[tuple[str, int, int]] = []

    for depth in range(len(trace) - 1, -1, -1):
        v = trace[depth]
        k = x - y
        if k == -depth or (
            k != depth and v.get(k - 1, 0) < v.get(k + 1, 0)
        ):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = v.get(prev_k, 0)
        prev_y = prev_x - prev_k

        while x > prev_x and y > prev_y:
            ops.append((EQUAL, x - 1, y - 1))
            x -= 1
            y -= 1

        if depth > 0:
            if x == prev_x:
                ops.append((INSERT, -1, y - 1))
                y -= 1
            else:
                ops.append((DELETE, x - 1, -1))
                x -= 1

    ops.reverse()
    return _group(ops)


def _group(ops: list[tuple[str, int, int]]) -> list[Hunk]:
    """Merge consecutive operations of the same kind into hunks."""
    hunks: list[Hunk] = []
    for tag, i, j in ops:
        if hunks and hunks[-1].tag == tag:
            hunk = hunks[-1]
            if tag == EQUAL:
                hunk.a_end = i + 1
                hunk.b_end = j + 1
            elif tag == DELETE:
                hunk.a_end = i + 1
            else:
                hunk.b_end = j + 1
            continue

        if tag == EQUAL:
            hunks.append(Hunk(EQUAL, i, i + 1, j, j + 1))
        elif tag == DELETE:
            hunks.append(Hunk(DELETE, i, i + 1, j, j))
        else:
            hunks.append(Hunk(INSERT, i, i, j, j + 1))
    return hunks


def diff_text(old: str, new: str, tokenizer) -> list[Hunk]:
    """Convenience wrapper that tokenises both sides before diffing."""
    return myers(tokenizer(old), tokenizer(new))
