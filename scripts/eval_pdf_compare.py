"""Evaluation of the PDF comparison (PDF 比对改造实施计划 · 阶段 0 / 1.2.3).

The script builds a set of synthetic PDF pairs, runs the comparison over
them and prints the metrics that decide whether the comparison is useful:

* **false positives** - the pairs that hold the same text and only differ in
  their typography must not produce a single difference,
* **recall** - every pair that holds a known change must produce that
  change,
* **time** - how long the parsing and the comparison take.

Usage::

    python scripts/eval_pdf_compare.py            # inside the backend image
    python scripts/eval_pdf_compare.py --legacy   # only the old engine
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
for _candidate in (BACKEND, Path("/app"), Path.cwd()):
    if (_candidate / "app" / "compare").exists():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

import fitz  # noqa: E402

from app.compare.pdf_compare import compare_pdfs  # noqa: E402
from app.parsers.pdf_parser import PdfParser  # noqa: E402
from app.schemas import CompareConfig  # noqa: E402

BASE = Path("/tmp/eval_pdf_compare")

PARAGRAPHS = [
    "第一章 总则",
    "本合同由甲乙双方于2024年3月15日在北京市签署，双方经友好协商，就合作事项达成如下协议。",
    "第一条 标的金额为人民币1200000.00元，分三期支付。",
    "第二条 交付期限为2024年6月30日前，逾期交付的，每日按合同金额的0.05%支付违约金。",
    "第三条 本合同一式两份，甲乙双方各执一份，具有同等法律效力。",
    "第四条 因不可抗力导致合同无法履行的，双方均不承担违约责任。",
    "第五条 本合同未尽事宜，由双方另行协商并签订补充协议。",
]

TABLE_ROWS = [
    ["编号", "项目名称", "金额"],
    ["001", "咨询服务", "120000"],
    ["002", "技术服务", "350000"],
]


# --------------------------------------------------------------------------
# The sample generator
# --------------------------------------------------------------------------


def build(
    path: Path,
    paragraphs: list[str],
    *,
    margin: float = 72.0,
    size: float = 10.0,
    leading: float | None = None,
    table: list[list[str]] | None = None,
    columns: int = 1,
    page_break_after: int | None = None,
) -> Path:
    """Write a PDF with a free choice of the typography.

    ``page_break_after`` starts a new page after the given paragraph -
    the same text with another pagination.
    """
    leading = leading if leading is not None else size * 1.5
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    per_line = max(1, int((595 - 2 * margin) / size))
    if columns == 1:
        y = margin + size
        for index, paragraph in enumerate(paragraphs):
            for start in range(0, len(paragraph), per_line):
                page.insert_text(
                    (margin, y), paragraph[start : start + per_line],
                    fontsize=size, fontname="china-s",
                )
                y += leading
            y += leading * 0.6
            if y > 790 or (page_break_after is not None and index == page_break_after):
                page = document.new_page(width=595, height=842)
                y = margin + size
    else:
        width = (595 - 3 * margin) / columns
        ys = [margin + size] * columns
        # The text flows down the first column and continues in the next
        # one - that is what a two column document looks like.
        per_column = (len(paragraphs) + columns - 1) // columns
        for index, paragraph in enumerate(paragraphs):
            column = min(columns - 1, index // per_column)
            x = margin + column * (width + margin)
            y = ys[column]
            for start in range(0, len(paragraph), int(width / size)):
                page.insert_text(
                    (x, y), paragraph[start : start + int(width / size)],
                    fontsize=size, fontname="china-s",
                )
                y += leading
            ys[column] = y + leading * 0.6
    if table:
        y += 20
        for row in table:
            x = margin
            for cell in row:
                page.insert_text((x, y), cell, fontsize=size, fontname="china-s")
                x += 150
            y += leading * 1.2
    document.save(str(path))
    document.close()
    return path


def samples() -> list[dict]:
    """The sample pairs and what is expected from them."""
    changed_amount = list(PARAGRAPHS)
    changed_amount[2] = changed_amount[2].replace("1200000.00", "1350000.00")

    changed_date = list(PARAGRAPHS)
    changed_date[3] = changed_date[3].replace("2024年6月30日", "2024年7月15日")

    # The same text, but cut into paragraphs at another place.
    split = list(PARAGRAPHS)
    head, tail = split[2].split("，", 1)
    split = split[:2] + [head + "，", tail] + split[3:]

    cases: list[dict] = [
        # -- the same text, another typography: no difference at all -----
        {"name": "同内容·窄边距10pt", "kind": "identical"},
        {"name": "同内容·宽边距12pt", "kind": "identical", "b": {"margin": 130, "size": 12}},
        {"name": "同内容·双栏", "kind": "identical", "b": {"columns": 2}},
        {
            "name": "同内容·段落切分不同",
            "kind": "identical",
            "b": {"paragraphs": split},
        },
        {
            "name": "同内容·分页位置不同",
            "kind": "identical",
            "a": {"page_break_after": 2},
            "b": {"page_break_after": 4},
        },
        # -- one change each --------------------------------------------
        {
            "name": "改金额",
            "kind": "modified",
            "needle": "1350000.00",
            "b": {"paragraphs": changed_amount},
        },
        {
            "name": "改日期",
            "kind": "modified",
            "needle": "2024年7月15日",
            "b": {"paragraphs": changed_date},
        },
        {
            "name": "删一段",
            "kind": "deleted",
            "needle": "第四条",
            "b": {"paragraphs": PARAGRAPHS[:5] + PARAGRAPHS[6:]},
        },
        {
            "name": "加一段",
            "kind": "added",
            "needle": "第六条",
            "b": {"paragraphs": PARAGRAPHS + ["第六条 争议由双方协商解决。"]},
        },
        {
            "name": "调换顺序",
            "kind": "moved",
            "b": {"paragraphs": [PARAGRAPHS[1], PARAGRAPHS[0]] + PARAGRAPHS[2:]},
        },
        {
            "name": "改表格单元格",
            "kind": "modified",
            "needle": "360000",
            "a": {"table": TABLE_ROWS},
            "b": {"table": [TABLE_ROWS[0], TABLE_ROWS[1], ["002", "技术服务", "360000"]]},
        },
    ]
    return cases


# --------------------------------------------------------------------------
# The evaluation
# --------------------------------------------------------------------------


def _squeeze(text: str) -> str:
    """The text without the spaces the PDF extraction may add."""
    return "".join((text or "").split())


def run(legacy: bool = False) -> dict:
    BASE.mkdir(parents=True, exist_ok=True)
    parser = PdfParser()
    config = CompareConfig()

    false_positives = 0
    found = 0
    expected = 0
    parse_seconds = 0.0
    compare_seconds = 0.0
    rows: list[tuple[str, str, int, bool]] = []

    for case in samples():
        name = case["name"]
        kind = case["kind"]
        options_a = case.get("a", {})
        options_b = case.get("b", {})
        path_a = build(
            BASE / f"{name}_a.pdf",
            options_a.get("paragraphs", PARAGRAPHS),
            table=options_a.get("table"),
            **{k: v for k, v in options_a.items() if k not in {"paragraphs", "table"}},
        )
        path_b = build(
            BASE / f"{name}_b.pdf",
            options_b.get("paragraphs", PARAGRAPHS),
            table=options_b.get("table"),
            **{k: v for k, v in options_b.items() if k not in {"paragraphs", "table"}},
        )

        start = time.time()
        parse_a = parser.parse(path_a, config)
        parse_b = parser.parse(path_b, config)
        parse_seconds += time.time() - start

        start = time.time()
        if legacy:
            from app.engines.dispatcher import compare as compare_parsed

            _mode, changes, _notices = compare_parsed(
                parse_a, parse_b, CompareConfig(pdf_stream_mode=False)
            )
        else:
            changes, _notices = compare_pdfs(parse_a, parse_b, config)
        compare_seconds += time.time() - start

        count = len(changes)
        if kind == "identical":
            expected += 1
            hit = count == 0
            false_positives += count
        else:
            expected += 1
            needle = _squeeze(case.get("needle", ""))
            hit = any(
                needle in _squeeze(change.document_a.text + change.document_b.text)
                for change in changes
                if needle
            )
            if kind == "moved":
                hit = any(change.primary_type == "moved" for change in changes)
        found += 1 if hit else 0
        rows.append((name, kind, count, hit))

    print(f"{'样本':<24}{'期望':<12}{'差异数':>8}{'命中':>8}")
    for name, kind, count, hit in rows:
        print(f"{name:<24}{kind:<12}{count:>8}{'✓' if hit else '✗':>8}")

    print()
    print(f"样本数        : {expected}")
    print(f"误报（应为空）: {false_positives}")
    print(f"命中          : {found}/{expected}")
    print(f"解析耗时      : {parse_seconds:.2f}s")
    print(f"比对耗时      : {compare_seconds:.2f}s")
    return {
        "samples": expected,
        "false_positives": false_positives,
        "hits": found,
        "parse_seconds": parse_seconds,
        "compare_seconds": compare_seconds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="run the old block based engine instead of the character stream",
    )
    arguments = parser.parse_args()
    print(f"=== {'旧引擎（块比对）' if arguments.legacy else '流式比对（字符流）'} ===")
    run(legacy=arguments.legacy)


if __name__ == "__main__":
    main()
