"""Order analysis helpers.

Used to decide which aligned items actually *moved* (PRD 6.2 level 3 and
PRD 7.2).  The items that keep their relative order form the longest
increasing subsequence of their positions; everything outside of it moved.
"""

from __future__ import annotations

import bisect


def longest_increasing_subsequence_indices(values: list[int]) -> set[int]:
    """Indices of one longest strictly increasing subsequence of ``values``."""
    if not values:
        return set()

    tails: list[int] = []
    previous = [-1] * len(values)

    for index, value in enumerate(values):
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if values[tails[middle]] < value:
                low = middle + 1
            else:
                high = middle
        if low > 0:
            previous[index] = tails[low - 1]
        if low == len(tails):
            tails.append(index)
        else:
            tails[low] = index

    result: set[int] = set()
    cursor = tails[-1]
    while cursor != -1:
        result.add(cursor)
        cursor = previous[cursor]
    return result


def moved_indices(source_positions: list[int], target_positions: list[int]) -> set[int]:
    """Indices of the items whose relative order changed.

    ``source_positions`` and ``target_positions`` are parallel lists holding
    the position of each item in the first and in the second sequence.
    """
    if len(source_positions) != len(target_positions):
        return set()
    keep = longest_increasing_subsequence_indices(target_positions)
    return {index for index in range(len(target_positions)) if index not in keep}
