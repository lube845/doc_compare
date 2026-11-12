"""Difference report export (PRD 13.4 / chapter 6 of the plan).

AI summarisation is optional; the report must still be produced when it is
unavailable (PRD chapter 18, phase six: "AI 失败不影响基础比对").
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from ..schemas import ChangeEvent, normalise_mode

_TYPE_LABELS = {
    "added": "新增",
    "deleted": "删除",
    "modified": "修改",
    "moved": "移动",
}


@dataclass
class Report:
    title: str
    file_a: str
    file_b: str
    mode: str
    stats: dict
    changes: list[ChangeEvent]
    notices: list[str]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# 文档比对差异报告：{self.title}")
        lines.append("")
        lines.append(f"- 文件 A：{self.file_a}")
        lines.append(f"- 文件 B：{self.file_b}")
        lines.append(f"- 比对模式：{self.mode}")
        stats = self.stats or {}
        lines.append(
            "- 差异统计："
            f"共 {stats.get('total', len(self.changes))} 处"
            f"（新增 {stats.get('added', 0)}、删除 {stats.get('deleted', 0)}、"
            f"修改 {stats.get('modified', 0)}、移动 {stats.get('moved', 0)}）"
        )
        if self.notices:
            lines.append("")
            lines.append("## 说明")
            for notice in self.notices:
                lines.append(f"- {notice}")
        lines.append("")
        lines.append("## 差异明细")
        lines.append("")
        for index, change in enumerate(self.changes, start=1):
            label = _TYPE_LABELS.get(change.primary_type, change.primary_type)
            lines.append(f"### 差异 {index:03d}｜{label}")
            if change.document_a.location.section:
                lines.append(f"- 位置：{change.document_a.location.section}")
            if change.document_b.location.sheet_name:
                lines.append(
                    f"- 工作表：{change.document_b.location.sheet_name}"
                )
            lines.append(f"- 说明：{change.summary}")
            if change.document_a.text:
                lines.append(f"- 文件 A：{_flatten(change.document_a.text)}")
            if change.document_b.text:
                lines.append(f"- 文件 B：{_flatten(change.document_b.text)}")
            lines.append("")
        return "\n".join(lines)

    def to_html(self) -> str:
        def escape(value: str) -> str:
            return html.escape(value or "")

        rows = []
        for index, change in enumerate(self.changes, start=1):
            label = _TYPE_LABELS.get(change.primary_type, change.primary_type)
            rows.append(
                "<tr>"
                f"<td>{index}</td>"
                f"<td>{escape(label)}</td>"
                f"<td>{escape(change.summary)}</td>"
                f"<td>{escape(_flatten(change.document_a.text))}</td>"
                f"<td>{escape(_flatten(change.document_b.text))}</td>"
                "</tr>"
            )
        stats = self.stats or {}
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>文档比对差异报告</title>
<style>
 body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
        margin: 32px; color: #1f2933; }}
 h1 {{ font-size: 22px; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
 th, td {{ border: 1px solid #d7dde5; padding: 6px 8px; text-align: left;
           vertical-align: top; }}
 th {{ background: #f4f6f9; }}
 .meta {{ color: #5a6675; font-size: 13px; line-height: 1.8; }}
</style>
</head>
<body>
<h1>文档比对差异报告</h1>
<div class="meta">
  <div>文件 A：{escape(self.file_a)}</div>
  <div>文件 B：{escape(self.file_b)}</div>
  <div>比对模式：{escape(self.mode)}</div>
  <div>差异统计：共 {stats.get('total', len(self.changes))} 处
    （新增 {stats.get('added', 0)}、删除 {stats.get('deleted', 0)}、
     修改 {stats.get('modified', 0)}、移动 {stats.get('moved', 0)}）</div>
</div>
<table>
<thead><tr><th>#</th><th>类型</th><th>说明</th><th>文件 A</th><th>文件 B</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>"""


def _flatten(text: str) -> str:
    return " ".join((text or "").split())


def build_report(
    comparison,
    file_a_name: str,
    file_b_name: str,
    changes: list[ChangeEvent],
) -> Report:
    return Report(
        title=f"{file_a_name} ↔ {file_b_name}",
        file_a=file_a_name,
        file_b=file_b_name,
        mode=normalise_mode(comparison.mode),
        stats=dict(comparison.stats or {}),
        changes=changes,
        notices=list(comparison.notices or []),
    )
