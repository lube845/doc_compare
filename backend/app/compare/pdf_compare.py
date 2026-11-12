"""PDF 比对 · 比对入口（V2 · 第十一阶段）.

Two PDFs are compared through their token streams: the tokens of one
document are aligned with the tokens of the other one and the aligned
pairs are compared token by token.  The result is a list of changes that
knows where on the page the difference is.

The chain is the one of the V2 plan:

    characters -> normalisation -> tokens -> global alignment
               -> token diff -> coordinates -> changes

The old engine (block by block) stays available: it is used for every
other combination of formats and it is the fallback when the character
stream is not available.
"""

from __future__ import annotations

from ..domain.models import ParseResult
from ..progress import ProgressCallback
from ..schemas import ChangeEvent, ChangeLocation, ChangeSide, CompareConfig
from .pdf_align import align_paragraphs, paragraph_map
from .pdf_anchor import page_pairs
from .pdf_diff import PdfDiffItem, diff_streams
from .pdf_normalize import normalise_stream, squeeze
from .pdf_tables import compare_tables

#: how a difference of the stream maps to a difference of the result
_PRIMARY_TYPES = {
    "insert": "added",
    "delete": "deleted",
    "replace": "modified",
    "move": "moved",
}


def can_stream_compare(parse_a: ParseResult, parse_b: ParseResult) -> bool:
    """True when the two documents can be compared through their streams."""
    return (
        parse_a.file_type == "pdf"
        and parse_b.file_type == "pdf"
        and parse_a.parsed_pdf is not None
        and parse_b.parsed_pdf is not None
    )


def compare_pdfs(
    parse_a: ParseResult,
    parse_b: ParseResult,
    config: CompareConfig | None = None,
    progress: ProgressCallback | None = None,
    layout: dict | None = None,
) -> tuple[list[ChangeEvent], list[str]]:
    """Compare two PDFs through their token streams.

    ``layout`` is filled with the page map of the two documents and with
    the pages that have no text layer: the PDF viewer needs both, and only
    this function has the aligned tokens they are built from (PDF 展示
    §4.3 / §6.2).
    """
    config = config or CompareConfig()
    parsed_a = parse_a.parsed_pdf
    parsed_b = parse_b.parsed_pdf
    if parsed_a is None or parsed_b is None:
        raise ValueError("PDF 解析结果缺少字符流，无法使用流式比对")

    if progress is not None:
        progress(45, "aligning")

    stream_a = normalise_stream(parsed_a, config)
    stream_b = normalise_stream(parsed_b, config)

    if progress is not None:
        progress(60, "aligning")

    quality_a = {info.page: info.quality for info in parsed_a.pages}
    quality_b = {info.page: info.quality for info in parsed_b.pages}
    items, tokens_a, tokens_b, segments = diff_streams(
        stream_a, stream_b, quality_a, quality_b, config
    )

    if layout is not None:
        layout["pairs"] = page_pairs(tokens_a, tokens_b, segments)
        layout["left_no_text_pages"] = _no_text_pages(parsed_a)
        layout["right_no_text_pages"] = _no_text_pages(parsed_b)

    if progress is not None:
        progress(80, "comparing")

    # The paragraphs are an auxiliary structure: they say which paragraph
    # of the other document belongs to a difference (V2 §8.1).
    mapping = paragraph_map(
        align_paragraphs(tokens_a, tokens_b, config.align_min_similarity)
    )
    reverse = {value: key for key, value in mapping.items()}

    changes: list[ChangeEvent] = []
    for index, item in enumerate(items):
        changes.append(_to_event(item, index, tokens_a, tokens_b, mapping, reverse))

    table_changes, table_notices = compare_tables(
        parse_a.document, parse_b.document, config, prefix="change"
    )
    changes.extend(table_changes)

    # The order of the list is the order the reader sees the differences in.
    for index, change in enumerate(changes):
        change.order = index

    notices: list[str] = []
    # The two documents may report the same warning (both have the same
    # scanned page); it is shown once.
    for warning in list(parsed_a.warnings) + list(parsed_b.warnings) + list(table_notices):
        if warning not in notices:
            notices.append(warning)
    if not changes:
        if any("未参与" in notice or "扫描" in notice for notice in notices):
            # Some pages were not compared: "no difference" would pretend
            # they were (pdf展示 §6.2).
            notices.append("未发现文本差异；存在未参与比对的页面，见上方提示")
        else:
            notices.append("两份 PDF 的内容完全一致，未发现差异")

    if progress is not None:
        progress(92, "comparing")

    return changes, notices


def _no_text_pages(parsed) -> list[int]:
    """The pages the parser found without a usable text layer."""
    return [
        info.page for info in parsed.pages if "scanned_like" in (info.flags or [])
    ]


def _to_event(
    item: PdfDiffItem,
    index: int,
    tokens_a,
    tokens_b,
    mapping: dict[int, int],
    reverse: dict[int, int],
) -> ChangeEvent:
    """Turn a difference of the stream into an event of the result."""
    primary = _PRIMARY_TYPES.get(item.kind, "modified")
    details: dict = {
        "fragments": item.fragments,
        "confidence": item.confidence,
        "kind": item.kind,
        "scope": item.scope,
    }
    if item.tags:
        details["tags"] = item.tags
    if item.context_before:
        details["context_before"] = item.context_before
    if item.context_after:
        details["context_after"] = item.context_after

    old_paragraph = item.old_paragraph
    new_paragraph = item.new_paragraph
    if old_paragraph is None and new_paragraph is not None:
        old_paragraph = reverse.get(new_paragraph)
    elif new_paragraph is None and old_paragraph is not None:
        new_paragraph = mapping.get(old_paragraph)
    details["paragraph_a"] = _paragraph_info(tokens_a, old_paragraph)
    details["paragraph_b"] = _paragraph_info(tokens_b, new_paragraph)

    document_a = ChangeSide(
        node_id=item.old_block,
        text=item.old_text,
        highlight=item.old_text or None,
        location=ChangeLocation(
            page=item.old_page,
            block_id=item.old_block,
            extra={
                "rects": item.old_rects,
                "rect_pages": item.old_rect_pages,
                "confidence": item.confidence,
            },
        ),
    )
    document_b = ChangeSide(
        node_id=item.new_block,
        text=item.new_text,
        highlight=item.new_text or None,
        location=ChangeLocation(
            page=item.new_page,
            block_id=item.new_block,
            extra={
                "rects": item.new_rects,
                "rect_pages": item.new_rect_pages,
                "confidence": item.confidence,
            },
        ),
    )

    return ChangeEvent(
        change_id=f"change_{index + 1:04d}",
        primary_type=primary,  # type: ignore[arg-type]
        category=_category(item),
        document_a=document_a,
        document_b=document_b,
        summary=_summary(item),
        details=details,
        order=item.order,
    )


def _paragraph_info(tokens, index: int | None) -> dict | None:
    """What the reader needs to know about the paragraph of a change."""
    if index is None or not tokens.paragraphs:
        return None
    if index >= len(tokens.paragraphs):
        return None
    paragraph = tokens.paragraphs[index]
    return {
        "index": paragraph.index,
        "block_id": paragraph.block_id,
        "page": paragraph.page,
        "text": paragraph.text[:200],
    }


def _category(item: PdfDiffItem) -> str:
    """The kind of information that changed."""
    for tag in item.tags:
        if tag in {"amount", "date", "ratio", "number"}:
            return tag
    return "text"


def _summary(item: PdfDiffItem) -> str:
    """A short description of the change."""
    from ..domain.normalize import summarise

    if item.kind == "insert":
        return f"新增内容：{summarise(item.new_text, 60)}"
    if item.kind == "delete":
        return f"删除内容：{summarise(item.old_text, 60)}"
    if item.kind == "move":
        return "内容位置发生变化，内容一致"

    # A change of a date or of a number is described by the value itself.
    for tag, label, pattern in (
        ("date", "日期变化", r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}"),
        ("amount", "金额变化", r"[¥￥$€£]\s?[\d,.]+|\d[\d,.]*\s*(?:万元|亿元|元)"),
        ("number", "数值变化", r"[-+]?\d[\d,]*(?:\.\d+)?%?"),
    ):
        if tag not in item.tags:
            continue
        import re

        old = re.findall(pattern, squeeze(item.old_text))
        new = re.findall(pattern, squeeze(item.new_text))
        if old or new:
            return f"{label}：{'、'.join(old) or '空'} → {'、'.join(new) or '空'}"

    return f"{summarise(item.old_text, 40)} → {summarise(item.new_text, 40)}"
