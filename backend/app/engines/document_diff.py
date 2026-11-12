"""Document diff engine (PRD chapter 6).

Responsibilities:

* Block alignment across documents (the three level matching strategy of 6.2).
* Word level text diffing for matched blocks (6.3).
* Table diffing for tables embedded in documents (6.4).
* Move detection for paragraphs that only changed position (6.2 level 3).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..domain.models import Block, BlockType, Document, TableData
from ..progress import ProgressCallback
from ..domain.normalize import normalise, tokenize_offsets
from ..schemas import ChangeEvent, ChangeLocation, ChangeSide, CompareConfig
from . import similarity
from .image_diff import compare_images
from .myers import EQUAL, myers
from .order import moved_indices
from .table_diff import diff_tables


@dataclass
class BlockMatch:
    """The alignment result for one pair of blocks."""

    a_index: int | None = None
    b_index: int | None = None
    match_type: str = "exact"  # exact | fuzzy | modified | added | deleted | moved
    similarity: float = 0.0
    details: dict = field(default_factory=dict)


def align_blocks(
    blocks_a: list[Block],
    blocks_b: list[Block],
    config: CompareConfig,
    progress: ProgressCallback | None = None,
    texts_a: list[str] | None = None,
    texts_b: list[str] | None = None,
) -> list[BlockMatch]:
    """Align two block sequences.

    Level 1 is an exact hash comparison, level 2 a similarity comparison and
    level 3 the global move detection (PRD 6.2).

    ``texts_a`` / ``texts_b`` are the config-aware normalised texts of the
    blocks.  ``diff_documents`` computes them once so that changing a
    comparison rule takes effect without re-parsing the file; callers that
    omit them fall back to the fields stored by the parser.
    """
    if progress is not None:
        progress(45, "aligning")
    matches: list[BlockMatch] = []
    matched_a: dict[int, int] = {}
    matched_b: dict[int, int] = {}

    if texts_a is None:
        texts_a = [block.normalized_text for block in blocks_a]
    if texts_b is None:
        texts_b = [block.normalized_text for block in blocks_b]

    # -- level 1: exact matching, order aware ---------------------------
    matcher = SequenceMatcher(None, texts_a, texts_b, autojunk=False)
    replace_regions: list[tuple[list[int], list[int]]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                a_index, b_index = i1 + offset, j1 + offset
                matched_a[a_index] = b_index
                matched_b[b_index] = a_index
                matches.append(
                    BlockMatch(a_index, b_index, "exact", 1.0)
                )
        elif tag == "replace":
            replace_regions.append(
                (list(range(i1, i2)), list(range(j1, j2)))
            )
        # 'delete' and 'insert' regions stay unmatched for now.

    # -- level 1b: global exact matching (catches moved paragraphs) ------
    index_by_text: dict[str, list[int]] = {}
    for b_index, text in enumerate(texts_b):
        if b_index in matched_b or not text:
            continue
        index_by_text.setdefault(text, []).append(b_index)

    for a_index, text in enumerate(texts_a):
        if a_index in matched_a or not text:
            continue
        candidates = index_by_text.get(text)
        if not candidates:
            continue
        b_index = candidates.pop(0)
        matched_a[a_index] = b_index
        matched_b[b_index] = a_index
        matches.append(BlockMatch(a_index, b_index, "exact", 1.0))

    # -- level 2: similarity matching inside replace regions -------------
    if progress is not None and replace_regions:
        progress(55, "aligning")
    for a_indices, b_indices in replace_regions:
        free_b = [index for index in b_indices if index not in matched_b]
        if not free_b:
            continue
        used_b: set[int] = set()
        for a_index in a_indices:
            if a_index in matched_a:
                continue
            target = texts_a[a_index]
            scored = sorted(
                (
                    (
                        similarity.combined_similarity(target, texts_b[b_index]),
                        b_index,
                    )
                    for b_index in free_b
                    if b_index not in used_b
                ),
                key=lambda item: (-item[0], item[1]),
            )
            if not scored:
                continue
            best_score, best_index = scored[0]
            if best_score < config.medium_similarity_threshold:
                continue
            if best_score < config.high_similarity_threshold:
                # A medium confidence match is only accepted when it is
                # clearly better than the runner up - otherwise the pair is
                # ambiguous and must not be forced (PRD 15.2).
                runner_up = scored[1][0] if len(scored) > 1 else 0.0
                if best_score - runner_up < 0.05:
                    continue
            used_b.add(best_index)
            matched_a[a_index] = best_index
            matched_b[best_index] = a_index
            matches.append(
                BlockMatch(a_index, best_index, "fuzzy", best_score)
            )

        # When a replaced region has the same number of blocks on both sides
        # the blocks correspond one to one - pair the leftovers by position.
        leftover_a = [i for i in a_indices if i not in matched_a]
        leftover_b = [i for i in b_indices if i not in matched_b]
        if leftover_a and len(leftover_a) == len(leftover_b):
            for a_index, b_index in zip(leftover_a, leftover_b):
                score = similarity.combined_similarity(texts_a[a_index], texts_b[b_index])
                matched_a[a_index] = b_index
                matched_b[b_index] = a_index
                matches.append(
                    BlockMatch(a_index, b_index, "fuzzy", score)
                )

    # -- level 3: global fuzzy matching for leftovers -------------------
    if progress is not None:
        progress(65, "aligning")
    free_a = [index for index in range(len(blocks_a)) if index not in matched_a]
    free_b = [index for index in range(len(blocks_b)) if index not in matched_b]
    if free_a and free_b and config.detect_moved_paragraphs:
        _global_fuzzy_match(
            free_a,
            free_b,
            matched_a,
            matched_b,
            matches,
            config,
            texts_a,
            texts_b,
        )

    # -- leftovers become additions / deletions -------------------------
    for a_index in range(len(blocks_a)):
        if a_index not in matched_a:
            matches.append(BlockMatch(a_index, None, "deleted", 0.0))
    for b_index in range(len(blocks_b)):
        if b_index not in matched_b:
            matches.append(BlockMatch(None, b_index, "added", 0.0))

    if config.detect_moved_paragraphs:
        _mark_moves(matches, blocks_a, blocks_b, config)
    else:
        _split_out_of_order(matches, texts_a, texts_b)

    matches.sort(
        key=lambda m: (
            m.b_index if m.b_index is not None else (m.a_index or 0) + 0.5
        )
    )
    return matches


def _global_fuzzy_match(
    free_a: list[int],
    free_b: list[int],
    matched_a: dict[int, int],
    matched_b: dict[int, int],
    matches: list[BlockMatch],
    config: CompareConfig,
    texts_a: list[str],
    texts_b: list[str],
) -> None:
    """Match remaining blocks when the similarity is unambiguous."""
    threshold = config.high_similarity_threshold
    scored: list[tuple[float, int, int]] = []
    for a_index in free_a:
        target = texts_a[a_index]
        for b_index in free_b:
            score = similarity.combined_similarity(target, texts_b[b_index])
            if score >= threshold:
                scored.append((score, a_index, b_index))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))

    for score, a_index, b_index in scored:
        if a_index in matched_a or b_index in matched_b:
            continue
        matched_a[a_index] = b_index
        matched_b[b_index] = a_index
        matches.append(BlockMatch(a_index, b_index, "fuzzy", score))


def _mark_moves(
    matches: list[BlockMatch],
    blocks_a: list[Block],
    blocks_b: list[Block],
    config: CompareConfig,
) -> None:
    """Flag matched pairs whose relative order changed (PRD 6.2 level 3)."""
    if not config.detect_moved_paragraphs:
        return
    pairs = sorted(
        (m for m in matches if m.a_index is not None and m.b_index is not None),
        key=lambda m: m.a_index,  # type: ignore[arg-type]
    )
    previous_b = -1
    moved = False
    for match in pairs:
        assert match.b_index is not None
        if match.b_index < previous_b:
            moved = True
        previous_b = max(previous_b, match.b_index)

    if not moved:
        return

    # A pair is a move when its position in B breaks the order of the items
    # that stayed in place.  The items that kept their relative order form
    # the longest increasing subsequence of their B positions.
    target_positions = [m.b_index for m in pairs]
    assert all(position is not None for position in target_positions)
    for index in moved_indices([m.a_index for m in pairs], target_positions):
        match = pairs[index]
        match.match_type = "moved"
        match.details["a_index"] = match.a_index
        match.details["b_index"] = match.b_index


def _split_out_of_order(
    matches: list[BlockMatch], texts_a: list[str], texts_b: list[str]
) -> None:
    """Report reordered paragraphs as delete + add when move detection is off.

    The alignment still pairs blocks by content; without move detection that
    reorder would otherwise disappear from the result.  Only pairs whose text
    is unique on both sides are split, so repeated paragraphs are not
    misreported because of an ambiguous order.
    """
    pairs = sorted(
        (m for m in matches if m.a_index is not None and m.b_index is not None),
        key=lambda m: m.a_index,  # type: ignore[arg-type]
    )
    if len(pairs) < 2:
        return

    target_positions = [m.b_index for m in pairs]
    assert all(position is not None for position in target_positions)
    inverted = moved_indices([m.a_index for m in pairs], target_positions)
    if not inverted:
        return

    counts_a = Counter(texts_a)
    counts_b = Counter(texts_b)
    split: set[int] = set()
    for index in inverted:
        match = pairs[index]
        assert match.a_index is not None and match.b_index is not None
        text_a = texts_a[match.a_index]
        text_b = texts_b[match.b_index]
        if text_a and counts_a[text_a] == 1 and counts_b[text_b] == 1:
            split.add(id(match))
    if not split:
        return

    replacement: list[BlockMatch] = []
    for match in matches:
        if id(match) in split:
            replacement.append(BlockMatch(match.a_index, None, "deleted", 0.0))
            replacement.append(BlockMatch(None, match.b_index, "added", 0.0))
        else:
            replacement.append(match)
    matches[:] = replacement


# --------------------------------------------------------------------------
# Text diffing
# --------------------------------------------------------------------------


def diff_block_text(
    text_a: str, text_b: str
) -> tuple[str | None, str | None, list[dict]]:
    """Return the highlighted A fragment, highlighted B fragment and details."""
    tokens_a = tokenize_offsets(text_a)
    tokens_b = tokenize_offsets(text_b)

    words_a = [token for token, _s, _e in tokens_a]
    words_b = [token for token, _s, _e in tokens_b]

    hunks = myers(words_a, words_b)

    a_ranges: list[tuple[int, int]] = []
    b_ranges: list[tuple[int, int]] = []
    details: list[dict] = []
    for hunk in hunks:
        if hunk.tag == EQUAL:
            continue
        if hunk.a_end > hunk.a_start and tokens_a:
            start = tokens_a[hunk.a_start][1]
            end = tokens_a[hunk.a_end - 1][2]
            a_ranges.append(
                _extend_numeric_unit(text_a, tokens_a, hunk.a_end, start, end)
            )
        if hunk.b_end > hunk.b_start and tokens_b:
            start = tokens_b[hunk.b_start][1]
            end = tokens_b[hunk.b_end - 1][2]
            b_ranges.append(
                _extend_numeric_unit(text_b, tokens_b, hunk.b_end, start, end)
            )
        details.append(
            {
                "type": hunk.tag,
                "a": text_a[slice(*a_ranges[-1])] if a_ranges else "",
                "b": text_b[slice(*b_ranges[-1])] if b_ranges else "",
            }
        )

    highlight_a = _merge_ranges(text_a, a_ranges)
    highlight_b = _merge_ranges(text_b, b_ranges)
    return highlight_a, highlight_b, details


_NUMBER_UNITS = {"万元", "亿元", "元", "万", "亿", "千", "%", "％"}


def _extend_numeric_unit(
    text: str,
    tokens: list[tuple[str, int, int]],
    next_index: int,
    start: int,
    end: int,
) -> tuple[int, int]:
    """Include the unit right after a changed number (``100`` → ``100万元``)."""
    changed = text[start:end]
    if not changed or not changed.replace(",", "").replace(".", "").isdigit():
        return start, end
    while next_index < len(tokens):
        token, token_start, token_end = tokens[next_index]
        if token_start != end or token not in _NUMBER_UNITS:
            break
        end = token_end
        next_index += 1
    return start, end


def _merge_ranges(text: str, ranges: list[tuple[int, int]]) -> str | None:
    if not ranges:
        return None
    ranges = sorted(ranges)
    merged: list[list[int]] = [list(ranges[0])]
    for start, end in ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts = [text[start:end] for start, end in merged]
    return " … ".join(part for part in parts if part) or None


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _visible_blocks(blocks: list[Block], config: CompareConfig) -> list[Block]:
    """Drop the blocks the comparison rules ask to ignore."""
    if not config.ignore_headers_footers:
        return blocks
    return [
        block for block in blocks if not block.metadata.get("header_footer")
    ]


def diff_documents(
    document_a: Document,
    document_b: Document,
    config: CompareConfig,
    progress: ProgressCallback | None = None,
) -> tuple[list[ChangeEvent], list[str]]:
    """Compare two documents and return the change events."""
    notices: list[str] = []
    blocks_a = _visible_blocks(document_a.blocks, config)
    blocks_b = _visible_blocks(document_b.blocks, config)
    # The parser normalises with defaults; redo it with the rules of this
    # comparison so that a configuration change takes effect without a
    # re-parse.
    texts_a = [normalise(block.original_text, config) for block in blocks_a]
    texts_b = [normalise(block.original_text, config) for block in blocks_b]
    matches = align_blocks(
        blocks_a,
        blocks_b,
        config,
        progress,
        texts_a=texts_a,
        texts_b=texts_b,
    )

    changes: list[ChangeEvent] = []
    counter = 0
    total = max(1, len(matches))

    for index, match in enumerate(matches):
        if progress is not None and index % 5 == 0:
            progress(70 + int(20 * index / total), "comparing")
        counter += 1
        change_id = f"change_{counter:04d}"
        block_a = blocks_a[match.a_index] if match.a_index is not None else None
        block_b = blocks_b[match.b_index] if match.b_index is not None else None

        if block_a is not None and block_b is not None:
            if block_a.type == BlockType.TABLE and block_b.type == BlockType.TABLE:
                table_changes, _ = diff_tables(
                    block_a.table, block_b.table, config, prefix=change_id
                )
                changes.extend(table_changes)
                counter += len(table_changes)
                continue

            image_change = compare_images(
                block_a.metadata.get("images"), block_b.metadata.get("images")
            )

            assert match.a_index is not None and match.b_index is not None
            if texts_a[match.a_index] == texts_b[match.b_index]:
                # Identical text - report a move and/or the picture change.
                if match.match_type == "moved":
                    changes.append(
                        _make_change(
                            change_id,
                            "moved",
                            "text",
                            block_a,
                            block_b,
                            summary="段落位置发生变化，内容一致",
                            order=match.b_index or 0,
                            details={"match_type": "moved"},
                        )
                    )
                if image_change is not None:
                    counter += 1
                    changes.append(
                        _make_image_change(
                            f"change_{counter:04d}",
                            image_change,
                            block_a,
                            block_b,
                            order=match.b_index or 0,
                        )
                    )
                continue

            highlight_a, highlight_b, details = diff_block_text(
                block_a.original_text, block_b.original_text
            )
            category = _categorise(block_a.original_text, block_b.original_text)
            changes.append(
                _make_change(
                    change_id,
                    "modified",
                    category,
                    block_a,
                    block_b,
                    summary=_summarise(block_a.original_text, block_b.original_text),
                    order=match.b_index or 0,
                    details={"match_type": match.match_type, "similarity": match.similarity,
                             "fragments": details},
                    highlight_a=highlight_a,
                    highlight_b=highlight_b,
                )
            )
            if image_change is not None:
                counter += 1
                changes.append(
                    _make_image_change(
                        f"change_{counter:04d}",
                        image_change,
                        block_a,
                        block_b,
                        order=match.b_index or 0,
                    )
                )
        elif block_b is not None:
            changes.append(
                _make_change(
                    change_id,
                    "added",
                    "text",
                    None,
                    block_b,
                    summary=_summarise_added(block_b),
                    order=block_b.order,
                )
            )
        elif block_a is not None:
            changes.append(
                _make_change(
                    change_id,
                    "deleted",
                    "text",
                    block_a,
                    None,
                    summary=_summarise_deleted(block_a),
                    order=block_a.order,
                )
            )

    image_total = _count_images(document_a) + _count_images(document_b)
    if image_total:
        notices.append(
            f"文档包含 {image_total} 张图片：已按图片文件哈希比对其是否新增、删除或替换；"
            "图片内的文字/图形内容不做识别。"
        )

    if not changes:
        notices.append("两份文件内容完全一致，未发现差异")

    if progress is not None:
        progress(92, "comparing")

    return changes, notices


def _count_images(document: Document) -> int:
    """The number of distinct pictures in a document."""
    total = 0
    for block in document.blocks:
        total += len(block.metadata.get("images") or [])
        if block.table is not None:
            for row in block.table.rows:
                for cell in row.cells:
                    if not cell.merged:  # vertical merge repeats the same cell
                        total += len(cell.images or [])
    return total


def _make_image_change(
    change_id: str,
    image_change: dict,
    block_a: Block,
    block_b: Block,
    order: int,
) -> ChangeEvent:
    """A change event for the pictures of a matched block pair."""
    return _make_change(
        change_id,
        image_change["type"],
        "image",
        block_a,
        block_b,
        summary=image_change["summary"],
        order=order,
        details={"match_type": "image", **image_change["details"]},
    )


def _make_change(
    change_id: str,
    primary_type: str,
    category: str,
    block_a: Block | None,
    block_b: Block | None,
    summary: str,
    order: int,
    details: dict | None = None,
    highlight_a: str | None = None,
    highlight_b: str | None = None,
) -> ChangeEvent:
    return ChangeEvent(
        change_id=change_id,
        primary_type=primary_type,  # type: ignore[arg-type]
        category=category,
        document_a=_side(block_a, highlight_a),
        document_b=_side(block_b, highlight_b),
        summary=summary,
        details=details or {},
        order=order,
    )


def _side(block: Block | None, highlight: str | None) -> ChangeSide:
    if block is None:
        return ChangeSide()
    return ChangeSide(
        node_id=block.block_id,
        text=block.original_text,
        highlight=highlight,
        location=ChangeLocation(
            page=block.source_position.page,
            section=block.section_title,
            block_id=block.block_id,
            extra={"order": block.order},
        ),
    )


_NUMERIC_RE = r"[-+]?\d[\d,]*(?:\.\d+)?%?"


def _categorise(text_a: str, text_b: str) -> str:
    """Classify a modification (PRD 13.3 - ``category``)."""
    import re

    numbers_a = re.findall(_NUMERIC_RE, text_a or "")
    numbers_b = re.findall(_NUMERIC_RE, text_b or "")
    dates_a = re.findall(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text_a or "")
    dates_b = re.findall(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text_b or "")
    if dates_a != dates_b:
        return "date"
    if numbers_a != numbers_b:
        return "numeric"
    if (text_a or "").strip() != (text_a or "").strip():
        return "formatting"
    return "text"


def _summarise(text_a: str, text_b: str) -> str:
    """Short, human readable description of a modification."""
    import re

    dates_a = re.findall(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text_a or "")
    dates_b = re.findall(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text_b or "")
    if dates_a != dates_b and (dates_a or dates_b):
        return f"日期变化：{'、'.join(dates_a) or '空'} → {'、'.join(dates_b) or '空'}"

    numbers_a = re.findall(_NUMERIC_RE, text_a or "")
    numbers_b = re.findall(_NUMERIC_RE, text_b or "")
    if numbers_a != numbers_b and (numbers_a or numbers_b):
        return f"数值变化：{'、'.join(numbers_a) or '空'} → {'、'.join(numbers_b) or '空'}"
    from ..domain.normalize import summarise

    a_short = summarise(text_a, 40)
    b_short = summarise(text_b, 40)
    return f"{a_short} → {b_short}"


def _summarise_added(block: Block) -> str:
    from ..domain.normalize import summarise

    return f"新增内容：{summarise(block.original_text, 60)}"


def _summarise_deleted(block: Block) -> str:
    from ..domain.normalize import summarise

    return f"删除内容：{summarise(block.original_text, 60)}"
