"""PDF 比对 · 段落层（V2 · 第八阶段）.

The paragraphs of a PDF are a *guess* of the typography: the same text may
be cut into paragraphs at another place in another document.  They are
therefore never used to decide whether two texts are equal - the tokens
decide that.  The paragraphs are an auxiliary structure (V2 §8.1):

* they say which paragraph a difference belongs to,
* they give the reader the context of the difference,
* they tell the reader which paragraph of the other document corresponds to
  the paragraph a difference was found in.

One paragraph may be matched with several paragraphs and the other way
round; the matching keeps the order of the document.
"""

from __future__ import annotations

from dataclasses import dataclass

from .pdf_anchor import _longest_increasing
from .pdf_tokens import TokenStream

#: the smallest similarity two paragraphs may have to be the same paragraph
PARAGRAPH_MIN_SIM = 0.5
#: the cost of leaving a paragraph unmatched
SKIP_COST = 1.0
#: above this number of pairs the similarity pass is skipped
MAX_DP_CELLS = 40000

try:  # pragma: no cover - optional native speed-up
    from rapidfuzz import fuzz

    _HAS_RAPIDFUZZ = True
except Exception:  # pragma: no cover
    _HAS_RAPIDFUZZ = False


@dataclass(slots=True)
class ParagraphPair:
    """One paragraph of A and the paragraph of B that goes with it."""

    a_index: int | None = None
    b_index: int | None = None
    similarity: float = 0.0
    confidence: float = 1.0


def text_ratio(a: str, b: str) -> float:
    """The similarity of two strings, between 0 and 1."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return fuzz.ratio(a, b) / 100.0
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def align_paragraphs(
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    min_similarity: float = PARAGRAPH_MIN_SIM,
) -> list[ParagraphPair]:
    """Match the paragraphs of the two documents."""
    paragraphs_a = tokens_a.paragraphs
    paragraphs_b = tokens_b.paragraphs
    if not paragraphs_a or not paragraphs_b:
        return []

    pairs: list[ParagraphPair] = []
    taken_a: set[int] = set()
    taken_b: set[int] = set()

    # -- 1. the paragraphs that are the same ------------------------------
    count_a: dict[str, int] = {}
    for paragraph in paragraphs_a:
        count_a[paragraph.text] = count_a.get(paragraph.text, 0) + 1
    positions_b: dict[str, list[int]] = {}
    for paragraph in paragraphs_b:
        positions_b.setdefault(paragraph.text, []).append(paragraph.index)

    candidates: list[tuple[int, int]] = []
    for paragraph in paragraphs_a:
        places = positions_b.get(paragraph.text)
        if not places:
            continue
        if count_a[paragraph.text] != 1 or len(places) != 1:
            # A paragraph that occurs several times proves nothing.
            continue
        candidates.append((paragraph.index, places[0]))

    for index_a, index_b in _longest_increasing(sorted(candidates)):
        pairs.append(ParagraphPair(index_a, index_b, 1.0, 1.0))
        taken_a.add(index_a)
        taken_b.add(index_b)

    # -- 2. the paragraphs that were changed ------------------------------
    rest_a = [p for p in paragraphs_a if p.index not in taken_a]
    rest_b = [p for p in paragraphs_b if p.index not in taken_b]
    if rest_a and rest_b and len(rest_a) * len(rest_b) <= MAX_DP_CELLS:
        pairs.extend(_align_rest(rest_a, rest_b, min_similarity))

    pairs.sort(key=lambda pair: (pair.a_index if pair.a_index is not None else 10**9))
    return pairs


def _align_rest(
    paragraphs_a: list,
    paragraphs_b: list,
    min_similarity: float,
) -> list[ParagraphPair]:
    """Match the paragraphs that are not the same, keeping their order."""
    count_a = len(paragraphs_a)
    count_b = len(paragraphs_b)

    infinity = float("inf")
    cost = [[infinity] * (count_b + 1) for _ in range(count_a + 1)]
    step: list[list[tuple | None]] = [
        [None] * (count_b + 1) for _ in range(count_a + 1)
    ]
    cost[count_a][count_b] = 0.0

    for i in range(count_a, -1, -1):
        for j in range(count_b, -1, -1):
            if i == count_a and j == count_b:
                continue
            best = infinity
            choice: tuple | None = None

            if i < count_a:
                value = cost[i + 1][j] + SKIP_COST
                if value < best:
                    best, choice = value, ("skip_a",)
            if j < count_b:
                value = cost[i][j + 1] + SKIP_COST
                if value < best:
                    best, choice = value, ("skip_b",)
            if i < count_a and j < count_b:
                similarity = text_ratio(
                    paragraphs_a[i].text, paragraphs_b[j].text
                )
                if similarity >= min_similarity:
                    value = cost[i + 1][j + 1] + (1.0 - similarity)
                    if value < best:
                        best, choice = value, ("pair",)

            cost[i][j] = best
            step[i][j] = choice

    pairs: list[ParagraphPair] = []
    i = j = 0
    while i < count_a or j < count_b:
        choice = step[i][j]
        if choice is None:
            break
        if choice[0] == "pair":
            similarity = text_ratio(paragraphs_a[i].text, paragraphs_b[j].text)
            pairs.append(
                ParagraphPair(
                    a_index=paragraphs_a[i].index,
                    b_index=paragraphs_b[j].index,
                    similarity=similarity,
                    confidence=max(0.3, similarity),
                )
            )
            i += 1
            j += 1
        elif choice[0] == "skip_a":
            i += 1
        else:
            j += 1
    return pairs


def paragraph_map(pairs: list[ParagraphPair]) -> dict[int, int]:
    """The paragraph of B that goes with every paragraph of A."""
    return {
        pair.a_index: pair.b_index
        for pair in pairs
        if pair.a_index is not None and pair.b_index is not None
    }
