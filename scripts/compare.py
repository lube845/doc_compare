#!/usr/bin/env python3
"""Command line comparison tool.

Only files of the same format can be compared.

Usage::

    python scripts/compare.py file_a.docx file_b.docx
    python scripts/compare.py a.xlsx b.xlsx --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 兼容两种布局：仓库根目录（scripts/compare.py）与容器内（/app/scripts/compare.py）
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE.parent / "backend", _HERE.parent):
    if (_candidate / "app" / "main.py").exists():
        sys.path.insert(0, str(_candidate))
        break

from app.domain.formats import format_label, same_format  # noqa: E402
from app.engines.dispatcher import compare  # noqa: E402
from app.parsers.registry import parse_file  # noqa: E402
from app.schemas import CompareConfig  # noqa: E402

TYPE_LABELS = {"added": "新增", "deleted": "删除", "modified": "修改", "moved": "移动"}


def main() -> int:
    parser = argparse.ArgumentParser(description="多格式文档比对助手 · 命令行工具")
    parser.add_argument("file_a")
    parser.add_argument("file_b")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--ignore-punctuation", action="store_true")
    args = parser.parse_args()

    config = CompareConfig(
        ignore_case=args.ignore_case,
        ignore_punctuation=args.ignore_punctuation,
    )

    path_a = Path(args.file_a)
    path_b = Path(args.file_b)
    for path in (path_a, path_b):
        if not path.exists():
            print(f"文件不存在：{path}", file=sys.stderr)
            return 2

    result_a = parse_file(path_a, path_a.name, config)
    result_b = parse_file(path_b, path_b.name, config)

    for label, result in (("文件 A", result_a), ("文件 B", result_b)):
        if result.parse_status not in {"success", "partial"}:
            print(f"{label} 解析失败：{result.message}", file=sys.stderr)
            return 1

    # Only same-format pairs can be compared.
    if not same_format(result_a.file_type, result_b.file_type):
        print(
            "仅支持同格式比对：文件 A 为 {a}，文件 B 为 {b}".format(
                a=format_label(result_a.file_type),
                b=format_label(result_b.file_type),
            ),
            file=sys.stderr,
        )
        return 2

    mode, changes, notices = compare(result_a, result_b, config)

    stats = {"total": len(changes)}
    for change in changes:
        stats[change.primary_type] = stats.get(change.primary_type, 0) + 1

    if args.json:
        print(
            json.dumps(
                {
                    "mode": mode,
                    "stats": stats,
                    "notices": notices,
                    "changes": [change.model_dump() for change in changes],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"比对模式：{mode}")
    print(
        "差异统计：共 {total} 处（新增 {added}、删除 {deleted}、修改 {modified}、移动 {moved}）".format(
            total=stats.get("total", 0),
            added=stats.get("added", 0),
            deleted=stats.get("deleted", 0),
            modified=stats.get("modified", 0),
            moved=stats.get("moved", 0),
        )
    )
    for notice in notices:
        print(f"提示：{notice}")
    print()
    for index, change in enumerate(changes, start=1):
        label = TYPE_LABELS.get(change.primary_type, change.primary_type)
        print(f"差异 {index:03d}｜{label}｜{change.summary}")
        if change.document_a.text:
            print(f"  A：{change.document_a.text.strip()[:120]}")
        if change.document_b.text:
            print(f"  B：{change.document_b.text.strip()[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
