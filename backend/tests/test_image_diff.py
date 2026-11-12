"""Picture comparison tests.

Pictures are compared by file hash: the same bytes are the same picture, a
replaced picture has a different hash.  The *content* of the picture is not
recognised - that limitation is stated in the result notices.
"""

from __future__ import annotations

from app.domain.models import Block, BlockType, Document, TableCell, TableData, TableRow
from app.domain.normalize import normalise
from app.engines.document_diff import diff_documents
from app.engines.image_diff import compare_images
from app.schemas import CompareConfig
from tests.fixtures import PNG_BLUE, PNG_RED

IMG_RED = {"hash": "red", "size": 69, "type": "image/png"}
IMG_BLUE = {"hash": "blue", "size": 69, "type": "image/png"}


def block(text: str, order: int, images=None) -> Block:
    metadata = {"images": images} if images is not None else {}
    return Block(
        block_id=f"b_{order}",
        type=BlockType.PARAGRAPH,
        order=order,
        original_text=text,
        normalized_text=normalise(text),
        metadata=metadata,
    )


def document(blocks: list[Block]) -> Document:
    return Document(document_id="doc", blocks=blocks)


def test_compare_images_same_bytes():
    assert compare_images([IMG_RED], [IMG_RED]) is None
    assert compare_images([], []) is None
    assert compare_images(None, None) is None


def test_compare_images_replaced():
    result = compare_images([IMG_RED], [IMG_BLUE])
    assert result["type"] == "modified"
    assert result["details"]["images_added"] == [IMG_BLUE]
    assert result["details"]["images_removed"] == [IMG_RED]


def test_compare_images_added_and_deleted():
    assert compare_images([], [IMG_BLUE])["type"] == "added"
    assert compare_images([IMG_RED], [])["type"] == "deleted"


def test_identical_text_with_replaced_picture_is_a_change():
    """Same text, different picture: still a difference (category image)."""
    a = document([block("正文", 0, [IMG_RED])])
    b = document([block("正文", 0, [IMG_BLUE])])
    changes, notices = diff_documents(a, b, CompareConfig())

    assert len(changes) == 1
    change = changes[0]
    assert change.category == "image"
    assert change.primary_type == "modified"
    assert change.details["images_removed"] == [IMG_RED]
    assert change.details["images_added"] == [IMG_BLUE]
    assert any("图片" in notice for notice in notices)


def test_same_picture_is_not_a_change():
    a = document([block("正文", 0, [IMG_RED])])
    b = document([block("正文", 0, [IMG_RED])])
    changes, notices = diff_documents(a, b, CompareConfig())

    assert changes == []
    assert any("图片" in notice for notice in notices)
    assert any("完全一致" in notice for notice in notices)


def test_text_and_picture_changes_are_reported_separately():
    a = document([block("100万元", 0, [IMG_RED])])
    b = document([block("120万元", 0, [IMG_BLUE])])
    changes, _ = diff_documents(a, b, CompareConfig())

    assert [change.category for change in changes] == ["numeric", "image"]


def _table(cell_images) -> TableData:
    cell = TableCell(
        cell_id="t0_r0_c0",
        row_index=0,
        column_index=0,
        original_text="",
        normalized_text="",
        value="",
        images=cell_images,
    )
    return TableData(
        table_id="t0",
        rows=[TableRow(row_id="t0_r0", row_index=0, cells=[cell])],
    )


def test_picture_inside_a_table_cell_is_compared():
    a = document(
        [
            Block(
                block_id="b_0",
                type=BlockType.TABLE,
                order=0,
                original_text="",
                normalized_text="",
                table=_table([IMG_RED]),
            )
        ]
    )
    b = document(
        [
            Block(
                block_id="b_0",
                type=BlockType.TABLE,
                order=0,
                original_text="",
                normalized_text="",
                table=_table([IMG_BLUE]),
            )
        ]
    )
    changes, _ = diff_documents(a, b, CompareConfig())

    assert len(changes) == 1
    assert changes[0].category == "table_image"
    assert changes[0].primary_type == "modified"
    assert changes[0].document_b.location.row_index == 0
    assert changes[0].document_b.location.column_index == 0


def test_real_png_bytes_have_different_hashes():
    import hashlib

    assert hashlib.sha256(PNG_RED).hexdigest() != hashlib.sha256(PNG_BLUE).hexdigest()
