"""Comparison of the pictures inside a document.

The parsers only keep the *hash* of a picture (the bytes stay in the
document), so the comparison works on the hashes: the same picture has the
same hash, a replaced one does not.  A picture that was re-encoded produces a
different hash as well - that is reported as a change, which is the safe
direction (a false positive is visible, a false negative is not).
"""

from __future__ import annotations

from typing import Any


def _hashes(images: list[dict[str, Any]] | None) -> list[str]:
    return [str(image.get("hash") or "") for image in images or []]


def compare_images(
    images_a: list[dict[str, Any]] | None,
    images_b: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Compare two lists of pictures.

    Returns ``None`` when both sides carry the same pictures, otherwise a
    dictionary with the change type, a human readable summary and the
    details (which pictures were added and which were removed).
    """
    hashes_a = _hashes(images_a)
    hashes_b = _hashes(images_b)
    if hashes_a == hashes_b:
        return None

    removed = [image for image in images_a or [] if str(image.get("hash")) not in hashes_b]
    added = [image for image in images_b or [] if str(image.get("hash")) not in hashes_a]
    kept = [image for image in images_a or [] if str(image.get("hash")) in hashes_b]

    if removed and added:
        change_type = "modified"
        summary = f"图片已更换（新增 {len(added)} 张、删除 {len(removed)} 张）"
    elif removed:
        change_type = "deleted"
        summary = f"图片已删除（{len(removed)} 张）"
    else:
        change_type = "added"
        summary = f"图片已新增（{len(added)} 张）"

    return {
        "type": change_type,
        "summary": summary,
        "details": {
            "images_added": added,
            "images_removed": removed,
            "images_kept": kept,
        },
    }
