#!/usr/bin/env python3
"""Probe the OCR provider before it is wired into the comparison.

The script sends a file to the configured OCR provider and prints what came
back: the recognised text, the position of every line and how long the call
took.  It is the fastest way to find out whether the provider recognises the
scans well enough and what it costs.

Usage::

    python scripts/ocr_probe.py scan.pdf
    python scripts/ocr_probe.py scan.pdf --pages 1,2 --lines
    python scripts/ocr_probe.py page.png --json

The channel and its credentials come from the settings saved in the
interface; the environment variables are the fallback::

    DOC_COMPARE_OCR_PROVIDER=baidu
    DOC_COMPARE_BAIDU_OCR_API_KEY=...
    DOC_COMPARE_BAIDU_OCR_SECRET_KEY=...
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# 兼容两种布局：仓库根目录（scripts/ocr_probe.py）与容器内（/app/scripts/ocr_probe.py）
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE.parent / "backend", _HERE.parent, Path("/app")):
    if (_candidate / "app" / "parsers" / "ocr_client.py").exists():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from app.parsers.ocr_client import OcrError, get_provider  # noqa: E402
from app.parsers.ocr_parser import ocr_page_lines  # noqa: E402
from app.services import app_settings  # noqa: E402

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    try:
        import fitz  # type: ignore
    except ImportError:
        fitz = None  # type: ignore


def _parse_pages(value: str | None) -> list[int] | None:
    if not value:
        return None
    numbers: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            numbers.append(int(part))
    return numbers or None


def _recognise_image(provider, values, path: Path) -> dict:
    """Recognise a single image file."""
    started = time.monotonic()
    image = path.read_bytes()
    result = provider.recognize(image, values)
    elapsed = time.monotonic() - started
    return {
        "file": str(path),
        "page": 0,
        "lines": [
            {
                "text": line.text,
                "confidence": round(line.confidence, 4),
                "chars": len(line.chars),
            }
            for line in result.lines
        ],
        "text": "\n".join(line.text for line in result.lines),
        "confidence": round(result.confidence, 4),
        "log_id": result.log_id,
        "seconds": round(elapsed, 2),
    }


def _recognise_pdf(provider, values, path: Path, pages: list[int] | None, dpi: int | None) -> list[dict]:
    """Recognise the pages of a PDF.

    The pages go through the same code as the comparison, so the reported
    coordinates are the PDF point coordinates the comparison works with.
    """
    if fitz is None:
        raise SystemExit("未安装 PyMuPDF，无法读取 PDF")

    document = fitz.open(str(path))
    results: list[dict] = []
    try:
        for index in range(document.page_count):
            number = index + 1
            if pages and number not in pages:
                continue
            page = document[index]
            started = time.monotonic()
            recognised = ocr_page_lines(
                page, number, client=provider, values=values, dpi=dpi
            )
            elapsed = time.monotonic() - started
            results.append(
                {
                    "file": str(path),
                    "page": number,
                    "lines": [
                        {
                            "text": line.text,
                            "chars": len(line.chars),
                            "bbox": [round(value, 1) for value in line.bbox],
                        }
                        for line in recognised.lines
                    ],
                    "text": "\n".join(line.text for line in recognised.lines),
                    "confidence": round(recognised.confidence, 4),
                    "log_id": recognised.log_id,
                    "seconds": round(elapsed, 2),
                }
            )
    finally:
        document.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 探测工具")
    parser.add_argument("file", help="要识别的 PDF 或图片")
    parser.add_argument("--pages", help="只识别这几页，例如 1,3,5")
    parser.add_argument("--dpi", type=int, help="渲染分辨率，默认取配置值")
    parser.add_argument("--lines", action="store_true", help="打印每一行及其坐标")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    current = app_settings.get()
    if not current.enabled:
        print(
            "OCR 未启用。请在界面「设置」页选择渠道并填写凭证，"
            "或设置 DOC_COMPARE_OCR_PROVIDER 等环境变量。",
            file=sys.stderr,
        )
        return 2

    path = Path(args.file)
    if not path.exists():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 2

    provider = get_provider(current.provider)
    if provider is None:  # pragma: no cover - a saved name always resolves
        print(f"未知的 OCR 渠道：{current.provider}", file=sys.stderr)
        return 2

    try:
        if path.suffix.lower() == ".pdf":
            results = _recognise_pdf(
                provider, current.values, path, _parse_pages(args.pages), args.dpi
            )
        else:
            results = [_recognise_image(provider, current.values, path)]
    except OcrError as exc:
        print(f"识别失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    for result in results:
        print(f"=== {result['file']} · 第 {result['page']} 页 ===")
        print(
            f"行数 {len(result['lines'])} · 平均置信度 {result['confidence']}"
            f" · 耗时 {result['seconds']}s · log_id {result['log_id']}"
        )
        if args.lines:
            for line in result["lines"]:
                confidence = line.get("confidence")
                prefix = f"[{confidence:.2f}] " if confidence is not None else ""
                print(f"  {prefix}{line['text']}")
                if "bbox" in line:
                    print(f"        {line['bbox']}")
        else:
            print(result["text"])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
