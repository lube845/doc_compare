"""PDF 比对 · 全局对齐（V2 · 第七阶段）.

The alignment answers the question "which part of A corresponds to which
part of B?".  It works on the *whole* document and not on the pages: a page
that was added in the middle of the new document shifts every following
page, and a comparison that walks the pages would then report the rest of
the document as changed.

The alignment is built in two layers (V2 §7.3):

1. **Anchors** - a run of tokens that occurs in both documents is an
   anchor.  The runs are found with a rolling hash over the token
   sequences (a *shingle* of ``shingle`` tokens), and the anchors that keep
   the order of the document are kept (a longest increasing subsequence).
   A run that occurs only once in both documents is a strong anchor; a run
   that occurs several times is only used to fill the gaps between the
   strong ones.
2. **Regions** - the space between two anchors is a region that has to be
   compared token by token.

The anchors are verified before they are used: a hash collision never
produces a wrong anchor, because the tokens of the run are compared one by
one.
"""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass

from .pdf_tokens import TokenStream

#: the number of tokens of a shingle
SHINGLE = 8
#: a shingle that occurs more often than this in one document is not used
MAX_OCCURRENCE = 4

_MASK = (1 << 64) - 1
_BASE = 1000003


@dataclass(slots=True)
class Anchor:
    """A run of tokens that is the same in both documents."""

    a_start: int
    a_end: int
    b_start: int
    b_end: int


@dataclass(slots=True)
class Segment:
    """A part of the two documents that corresponds to each other."""

    a_start: int
    a_end: int
    b_start: int
    b_end: int
    #: true when the two parts are known to be the same and do not have to
    #: be compared again
    identical: bool = False


def align_tokens(
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    shingle: int = SHINGLE,
) -> list[Segment]:
    """Split the two documents into the parts that correspond to each other."""
    texts_a = tokens_a.texts
    texts_b = tokens_b.texts

    anchors = find_anchors(texts_a, texts_b, shingle)

    segments: list[Segment] = []
    cursor_a = 0
    cursor_b = 0

    def add(a0: int, a1: int, b0: int, b1: int, identical: bool) -> None:
        if segments and segments[-1].identical == identical:
            previous = segments[-1]
            previous.a_end = a1
            previous.b_end = b1
            return
        segments.append(Segment(a0, a1, b0, b1, identical))

    for anchor in anchors:
        anchor = _align_to_lines(anchor, tokens_a, tokens_b)
        if anchor.a_start < cursor_a or anchor.b_start < cursor_b:
            anchor = _cut_anchor(anchor, tokens_a, tokens_b, cursor_a, cursor_b)
            if anchor is None:
                continue
        if anchor.a_start > cursor_a or anchor.b_start > cursor_b:
            add(cursor_a, anchor.a_start, cursor_b, anchor.b_start, False)
        add(anchor.a_start, anchor.a_end, anchor.b_start, anchor.b_end, True)
        cursor_a = anchor.a_end
        cursor_b = anchor.b_end

    if cursor_a < len(texts_a) or cursor_b < len(texts_b):
        add(cursor_a, len(texts_a), cursor_b, len(texts_b), False)

    return segments


def _cut_anchor(
    anchor: Anchor,
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    cursor_a: int,
    cursor_b: int,
) -> Anchor | None:
    """Cut the overlap of an anchor with the anchor that came before it.

    When the border of an anchor is moved to the border of a line it may
    overlap the anchor before it.  The overlapped head is cut from *both*
    sides, so that the two sides keep corresponding to each other; the
    anchor is dropped when what is left does not carry the same text.
    """
    drop_a = max(0, cursor_a - anchor.a_start)
    drop_b = max(0, cursor_b - anchor.b_start)
    drop = max(drop_a, drop_b)

    a_start = anchor.a_start + drop
    b_start = anchor.b_start + drop
    if a_start >= anchor.a_end or b_start >= anchor.b_end:
        return None
    if tokens_a.texts[a_start : anchor.a_end] != tokens_b.texts[b_start : anchor.b_end]:
        return None
    return Anchor(a_start, anchor.a_end, b_start, anchor.b_end)


def _align_to_lines(
    anchor: Anchor,
    tokens_a: TokenStream,
    tokens_b: TokenStream,
) -> Anchor:
    """Move the end of an anchor to the end of the rendered line.

    An anchor that ends in the middle of a line makes the region next to
    it start in the middle of the same line, and a paragraph that was
    added would then carry the last characters of the paragraph before it
    (or lose the first characters of its own).  The anchor is only moved
    when both sides still carry the same text.
    """
    a_end = _line_end(tokens_a, anchor.a_end)
    b_end = _line_end(tokens_b, anchor.b_end)
    if a_end == anchor.a_end and b_end == anchor.b_end:
        return anchor
    if tokens_a.texts[anchor.a_start:a_end] != tokens_b.texts[anchor.b_start:b_end]:
        return anchor
    return Anchor(anchor.a_start, a_end, anchor.b_start, b_end)


def _line_end(tokens: TokenStream, end: int) -> int:
    """The end of the rendered line the token before ``end`` stands on."""
    if end <= 0 or end > len(tokens.tokens):
        return end
    last = end - 1
    line_id = tokens.tokens[last].line_id
    while last < len(tokens.tokens) - 1 and tokens.tokens[last + 1].line_id == line_id:
        last += 1
    return last + 1


def find_anchors(
    texts_a: list[str],
    texts_b: list[str],
    shingle: int = SHINGLE,
) -> list[Anchor]:
    """The runs of tokens that are the same in both documents."""
    if shingle <= 0:
        return []
    if len(texts_a) < shingle or len(texts_b) < shingle:
        return []

    vocabulary: dict[str, int] = {}
    ids_a = _ids(texts_a, vocabulary)
    ids_b = _ids(texts_b, vocabulary)

    positions_a = _positions(_hashes(ids_a, shingle))
    positions_b = _positions(_hashes(ids_b, shingle))

    strong: list[tuple[int, int]] = []
    weak: list[tuple[int, int]] = []
    for value, places_a in positions_a.items():
        places_b = positions_b.get(value)
        if not places_b:
            continue

        if len(places_a) == 1 and len(places_b) == 1:
            # The run occurs exactly once on both sides: it proves nothing
            # about the order, but it is the best anchor there is.
            if _same_run(texts_a, texts_b, places_a[0], places_b[0], shingle):
                strong.append((places_a[0], places_b[0]))
            continue

        # The run occurs several times.  The i-th occurrence of A is
        # paired with the i-th occurrence of B: the text of a contract
        # repeats itself, and the order is what decides (V2 §7.4).
        common = min(len(places_a), len(places_b))
        for index in range(common):
            start_a = places_a[index]
            start_b = places_b[index]
            if _same_run(texts_a, texts_b, start_a, start_b, shingle):
                weak.append((start_a, start_b))
                continue
            repaired = _repair(texts_a, texts_b, start_a, places_b, index, shingle)
            if repaired is not None:
                weak.append((start_a, repaired))

    anchors = _to_anchors(strong, shingle)

    # The weak anchors fill the gaps between the strong ones.
    if weak:
        anchors = _fill_gaps(anchors, weak, shingle)

    return anchors


def _repair(
    texts_a: list[str],
    texts_b: list[str],
    start_a: int,
    places_b: list[int],
    index: int,
    shingle: int,
) -> int | None:
    """The occurrence of the run in B that goes with the one in A.

    A paragraph that was added shifts the repetitions that come after it.
    The neighbour of the expected place is looked at, and the one that
    really carries the same text is taken.
    """
    for distance in (1, -1, 2, -2, 3, -3):
        place = index + distance
        if place < 0 or place >= len(places_b):
            continue
        candidate = places_b[place]
        if _same_run(texts_a, texts_b, start_a, candidate, shingle):
            return candidate
    return None


# --------------------------------------------------------------------------
# The anchors
# --------------------------------------------------------------------------


def _to_anchors(pairs: list[tuple[int, int]], shingle: int) -> list[Anchor]:
    """The pairs that keep the order of the document, without overlap."""
    if not pairs:
        return []

    ordered = _longest_increasing(sorted(pairs))
    anchors: list[Anchor] = []
    for start_a, start_b in ordered:
        if anchors:
            previous = anchors[-1]
            if start_a < previous.a_end or start_b < previous.b_end:
                # The runs overlap: the first one already covers them.
                continue
        anchors.append(
            Anchor(
                a_start=start_a,
                a_end=start_a + shingle,
                b_start=start_b,
                b_end=start_b + shingle,
            )
        )
    return anchors


def _fill_gaps(
    anchors: list[Anchor],
    weak: list[tuple[int, int]],
    shingle: int,
) -> list[Anchor]:
    """Put the weak anchors into the gaps between the strong ones."""
    ordered = sorted(weak)
    starts = [pair[0] for pair in ordered]

    result: list[Anchor] = []
    cursor_a = 0
    cursor_b = 0

    def fill(low_a: int, high_a: int, low_b: int, high_b: int) -> None:
        nonlocal cursor_a, cursor_b
        left = bisect.bisect_left(starts, low_a)
        right = bisect.bisect_left(starts, high_a)
        candidates = [
            pair
            for pair in ordered[left:right]
            if low_b <= pair[1] < high_b
            and pair[0] + shingle <= high_a
            and pair[1] + shingle <= high_b
        ]
        for anchor in _to_anchors(candidates, shingle):
            if anchor.a_start < cursor_a or anchor.b_start < cursor_b:
                continue
            result.append(anchor)
            cursor_a = anchor.a_end
            cursor_b = anchor.b_end

    for anchor in anchors:
        fill(cursor_a, anchor.a_start, cursor_b, anchor.b_start)
        result.append(anchor)
        cursor_a = anchor.a_end
        cursor_b = anchor.b_end

    fill(cursor_a, 10**9, cursor_b, 10**9)
    return result


def _longest_increasing(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The longest subsequence of the pairs that keeps the order of B."""
    if not pairs:
        return []

    tails: list[int] = []
    tails_at: list[int] = []
    previous = [-1] * len(pairs)

    for index, (_start_a, start_b) in enumerate(pairs):
        place = bisect.bisect_left(tails, start_b)
        if place > 0:
            previous[index] = tails_at[place - 1]
        if place == len(tails):
            tails.append(start_b)
            tails_at.append(index)
        else:
            tails[place] = start_b
            tails_at[place] = index

    result: list[tuple[int, int]] = []
    cursor = tails_at[-1]
    while cursor != -1:
        result.append(pairs[cursor])
        cursor = previous[cursor]
    result.reverse()
    return result


# --------------------------------------------------------------------------
# The rolling hash
# --------------------------------------------------------------------------


def _ids(texts: list[str], vocabulary: dict[str, int]) -> list[int]:
    """The number of every token, in a vocabulary shared by both documents."""
    return [vocabulary.setdefault(text, len(vocabulary)) for text in texts]


def _hashes(ids: list[int], size: int) -> list[int]:
    """The rolling hash of every run of ``size`` tokens."""
    length = len(ids)
    if length < size:
        return []

    power = 1
    for _ in range(size - 1):
        power = (power * _BASE) & _MASK

    value = 0
    for position in range(size):
        value = (value * _BASE + ids[position]) & _MASK

    hashes = [value]
    for position in range(size, length):
        value = (
            (value - ids[position - size] * power) * _BASE + ids[position]
        ) & _MASK
        hashes.append(value)
    return hashes


def _positions(hashes: list[int]) -> dict[int, list[int]]:
    """The places every hash occurs at."""
    positions: dict[int, list[int]] = {}
    for index, value in enumerate(hashes):
        places = positions.get(value)
        if places is None:
            positions[value] = [index]
        else:
            places.append(index)
    return positions


def _same_run(
    texts_a: list[str],
    texts_b: list[str],
    start_a: int,
    start_b: int,
    size: int,
) -> bool:
    """True when the two runs of tokens are the same.

    The hash only tells that the runs *probably* are the same; this check
    makes it certain.
    """
    if start_a + size > len(texts_a) or start_b + size > len(texts_b):
        return False
    return texts_a[start_a : start_a + size] == texts_b[start_b : start_b + size]


# --------------------------------------------------------------------------
# The page map
# --------------------------------------------------------------------------


def page_pairs(
    tokens_a: TokenStream,
    tokens_b: TokenStream,
    segments: list[Segment],
) -> list[dict]:
    """The pages of A and the pages of B that carry the same content.

    Every token of a segment that is known to be identical corresponds to
    the token of the other document at the same offset.  The votes of
    those pairs give the page map of the viewer: it is what lets the two
    readers scroll together even when the pagination of the two documents
    is different (PDF 展示 §4.3).

    A page that carries nothing but added content has no entry in the map -
    the viewer then falls back to the nearest mapped page.
    """
    if not segments:
        return []

    votes: dict[int, Counter] = defaultdict(Counter)
    for segment in segments:
        if not segment.identical:
            continue
        for offset in range(segment.a_end - segment.a_start):
            token_a = tokens_a.tokens[segment.a_start + offset]
            token_b = tokens_b.tokens[segment.b_start + offset]
            votes[token_a.page][token_b.page] += 1

    pairs: list[dict] = []
    for page_a in sorted(votes):
        counts = votes[page_a]
        page_b, matched = counts.most_common(1)[0]
        total = sum(counts.values())
        pairs.append(
            {
                "left_page": page_a,
                "right_page": page_b,
                # How much of the paired content of the page went to the
                # same page of the other document.
                "confidence": round(matched / total, 4) if total else 0.0,
            }
        )
    return pairs
