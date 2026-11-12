"""Row matching for spreadsheets and document tables (PRD 7.3).

The matching order follows the PRD:

1. unique business key
2. user specified key column
3. multi column fingerprint
4. row content similarity
5. everything else is added / deleted

The implementation is deliberately *content driven* so that inserting one row
in the middle of a 1000 row table does not turn the remaining 980 rows into
"modified" rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from ..engines import similarity
from .order import moved_indices


@dataclass
class RowMatch:
    a_index: int | None = None
    b_index: int | None = None
    match_type: str = "exact"  # exact | key | fuzzy | added | deleted
    similarity: float = 0.0
    row_key: str | None = None


@dataclass
class KeySpec:
    """A composite key over one or more columns."""

    columns: tuple[int, ...]
    label: str = ""

    def value_for(self, row: Sequence[str | None]) -> str:
        parts = []
        for column in self.columns:
            if 0 <= column < len(row):
                parts.append((row[column] or "").strip())
            else:
                parts.append("")
        return "\u0001".join(parts)


def detect_key_columns(
    rows_a: Sequence[Sequence[str | None]],
    rows_b: Sequence[Sequence[str | None]],
    max_candidates: int = 8,
) -> KeySpec | None:
    """Find a column that behaves like a unique business key.

    A column qualifies when its values are unique and non empty in *both*
    tables and the overlap is meaningful.
    """
    if not rows_a or not rows_b:
        return None

    width = max(max((len(row) for row in rows_a), default=0),
                max((len(row) for row in rows_b), default=0))

    best: tuple[float, KeySpec] | None = None
    for column in range(min(width, max_candidates)):
        values_a = [(row[column] or "").strip() for row in rows_a if column < len(row)]
        values_b = [(row[column] or "").strip() for row in rows_b if column < len(row)]
        if not values_a or not values_b:
            continue
        if any(value == "" for value in values_a) or any(value == "" for value in values_b):
            continue
        if len(set(values_a)) != len(values_a) or len(set(values_b)) != len(values_b):
            continue
        overlap = len(set(values_a) & set(values_b))
        if overlap < 0.5 * max(len(values_a), len(values_b)):
            continue
        score = overlap / max(len(values_a), len(values_b))
        candidate = KeySpec(columns=(column,), label=str(column))
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None


def match_rows(
    rows_a: Sequence[Sequence[str | None]],
    rows_b: Sequence[Sequence[str | None]],
    key_a: KeySpec | None = None,
    key_b: KeySpec | None = None,
    threshold: float = 0.75,
    extra_keys: Sequence[KeySpec] = (),
) -> list[RowMatch]:
    """Align two row sequences."""
    matches: list[RowMatch] = []
    matched_a: dict[int, int] = {}
    matched_b: dict[int, int] = {}

    key_specs_a = [key_a, *extra_keys] if key_a else list(extra_keys)
    key_specs_b = [key_b, *extra_keys] if key_b else list(extra_keys)

    # -- 1. key based matching ------------------------------------------
    for spec_a, spec_b in zip(key_specs_a, key_specs_b):
        if spec_a is None or spec_b is None:
            continue
        index_b: dict[str, int] = {}
        for b_index, row in enumerate(rows_b):
            value = spec_b.value_for(row)
            if value and value not in index_b:
                index_b[value] = b_index
        for a_index, row in enumerate(rows_a):
            if a_index in matched_a:
                continue
            value = spec_a.value_for(row)
            b_index = index_b.get(value)
            if b_index is None or b_index in matched_b:
                continue
            matched_a[a_index] = b_index
            matched_b[b_index] = a_index
            matches.append(RowMatch(a_index, b_index, "key", 1.0, value))

    # -- 2. full row fingerprint (exact match) ---------------------------
    fingerprint_b: dict[str, list[int]] = {}
    for b_index, row in enumerate(rows_b):
        fingerprint_b.setdefault(_fingerprint(row), []).append(b_index)
    for a_index, row in enumerate(rows_a):
        if a_index in matched_a:
            continue
        candidates = fingerprint_b.get(_fingerprint(row))
        if not candidates:
            continue
        b_index = next((c for c in candidates if c not in matched_b), None)
        if b_index is None:
            continue
        matched_a[a_index] = b_index
        matched_b[b_index] = a_index
        matches.append(RowMatch(a_index, b_index, "exact", 1.0))

    # -- 3. similarity matching -----------------------------------------
    free_a = [i for i in range(len(rows_a)) if i not in matched_a]
    free_b = [i for i in range(len(rows_b)) if i not in matched_b]
    if free_a and free_b:
        scored: list[tuple[float, int, int]] = []
        for a_index in free_a:
            for b_index in free_b:
                score = similarity.combined_similarity(
                    _row_text(rows_a[a_index]), _row_text(rows_b[b_index])
                )
                if score >= threshold:
                    scored.append((score, a_index, b_index))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        for score, a_index, b_index in scored:
            if a_index in matched_a or b_index in matched_b:
                continue
            matched_a[a_index] = b_index
            matched_b[b_index] = a_index
            matches.append(RowMatch(a_index, b_index, "fuzzy", score))

    # -- 4. leftovers ----------------------------------------------------
    for a_index in range(len(rows_a)):
        if a_index not in matched_a:
            matches.append(RowMatch(a_index, None, "deleted", 0.0))
    for b_index in range(len(rows_b)):
        if b_index not in matched_b:
            matches.append(RowMatch(None, b_index, "added", 0.0))

    matches.sort(key=lambda m: m.b_index if m.b_index is not None else (m.a_index or 0))
    return matches


def _fingerprint(row: Sequence[str | None]) -> str:
    return "\u0001".join((cell or "").strip() for cell in row)


def _row_text(row: Sequence[str | None]) -> str:
    return " ".join((cell or "").strip() for cell in row if cell)


def detect_moved_rows(matches: list[RowMatch]) -> None:
    """Flag rows that only changed position (PRD 7.2 / 7.5)."""
    pairs = [m for m in matches if m.a_index is not None and m.b_index is not None]
    pairs.sort(key=lambda m: m.a_index)  # type: ignore[arg-type]
    if len(pairs) < 2:
        return
    source = [m.a_index for m in pairs]
    target = [m.b_index for m in pairs]
    assert all(position is not None for position in source)
    assert all(position is not None for position in target)
    for index in moved_indices(source, target):
        pairs[index].match_type = "moved"
