"""Similarity helpers (PRD 6.2 / 12.5).

RapidFuzz is used when available; a pure Python fallback keeps the system
working without the native extension.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from ..domain.normalize import jaccard, tokenize

try:  # pragma: no cover - optional native speed-up
    from rapidfuzz import fuzz

    _HAS_RAPIDFUZZ = True
except Exception:  # pragma: no cover
    _HAS_RAPIDFUZZ = False


def ratio(a: str, b: str) -> float:
    """Normalised similarity of two strings in ``[0, 1]``."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return fuzz.ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


def token_ratio(a: str, b: str) -> float:
    """Similarity that ignores token ordering."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return fuzz.token_set_ratio(a, b) / 100.0
    return SequenceMatcher(None, sorted(tokenize(a)), sorted(tokenize(b))).ratio()


def content_ratio(a: str, b: str) -> float:
    """Blend of the two ratios - used for paragraph similarity."""
    return max(ratio(a, b), token_ratio(a, b))


def jaccard_similarity(a: str, b: str) -> float:
    return jaccard(tokenize(a), tokenize(b))


def combined_similarity(a: str, b: str) -> float:
    """Similarity used by the matchers (PRD 6.2, second level)."""
    if a == b:
        return 1.0
    return round(
        0.5 * content_ratio(a, b) + 0.3 * jaccard_similarity(a, b)
        + 0.2 * ratio(a, b),
        4,
    )


def best_match(
    target: str, candidates: list[str], threshold: float = 0.0
) -> tuple[int, float]:
    """Index of the most similar candidate and its score."""
    best_index, best_score = -1, 0.0
    for index, candidate in enumerate(candidates):
        score = combined_similarity(target, candidate)
        if score > best_score:
            best_index, best_score = index, score
    if best_score < threshold:
        return -1, best_score
    return best_index, best_score
