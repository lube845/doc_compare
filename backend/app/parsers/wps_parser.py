"""WPS 文字 (``.wps``) parser.

Resolution order:

1. LibreOffice conversion -> DOCX parser (full structure).
2. OOXML container -> DOCX parser.
3. OLE2 container -> best effort text recovery (``partial`` status).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from ..domain.models import (
    Block,
    BlockType,
    ContentType,
    Document,
    ParseResult,
)
from ..domain.normalize import normalise
from ..schemas import CompareConfig
from . import libreoffice
from .base import BaseParser, ParseFailed, ProgressCallback
from .cfb import CompoundFile, CFBError
from .heuristic import join_runs, readable_runs


class WpsParser(BaseParser):
    file_type = "wps"
    display_name = "WPS 文字"
    content_type = ContentType.DOCUMENT.value
    known_gaps = ("精确样式层级", "页眉页脚定位", "图片内容识别（仅比对图片是否相同）")

    def parse(
        self,
        file_path: Path,
        options: CompareConfig | None = None,
        progress: ProgressCallback | None = None,
    ) -> ParseResult:
        config = options or CompareConfig()

        if libreoffice.is_available():
            converted = libreoffice.convert_to_ooxml(file_path, "wps")
            if converted is not None:
                from .docx_parser import DocxParser

                result = DocxParser().parse(converted, config, progress)
                result.file_type = self.file_type
                result.parser = "wps_parser"
                result.uncovered = list(self.known_gaps)
                return result

        if zipfile.is_zipfile(file_path):
            from .docx_parser import DocxParser

            result = DocxParser().parse(file_path, config, progress)
            result.file_type = self.file_type
            result.parser = "wps_parser"
            result.uncovered = list(self.known_gaps)
            return result

        return self._parse_ole(file_path, config)

    # -- fallback ----------------------------------------------------------
    def _parse_ole(self, file_path: Path, config: CompareConfig) -> ParseResult:
        try:
            data = file_path.read_bytes()
            cfb = CompoundFile(data)
        except (CFBError, OSError) as exc:
            raise ParseFailed(f"无法读取 WPS 文件：{exc}") from exc

        candidates: list[str] = []
        for name, blob in cfb.iter_streams():
            if len(blob) < 32:
                continue
            runs = readable_runs(blob)
            if runs:
                candidates.extend(runs)

        text = join_runs(candidates[:400])
        if not text:
            raise ParseFailed(
                "WPS 文件解析失败：未能提取到文本内容，建议安装 LibreOffice 以获得完整解析"
            )

        blocks: list[Block] = []
        for order, line in enumerate(text.splitlines()):
            content = line.strip()
            if not content:
                continue
            blocks.append(
                Block(
                    block_id=f"b_{order}",
                    type=BlockType.PARAGRAPH,
                    order=order,
                    original_text=content,
                    normalized_text=normalise(content, config),
                )
            )

        document = Document(
            document_id=f"doc_{file_path.stem}",
            blocks=blocks,
            metadata={"parser": "heuristic-ole"},
        )
        return ParseResult(
            file_type=self.file_type,
            content_type=ContentType.DOCUMENT,
            parser="wps_parser",
            parse_status="partial",
            message="WPS 文件通过兼容模式解析，标题层级与表格结构可能不完整",
            metadata={"block_count": len(blocks)},
            document=document,
            uncovered=list(self.known_gaps),
        )
