# 仓库简介

## GitHub「About」一句话简介

**中文（推荐作为仓库主简介）**

> 多格式文档比对助手：DOC / DOCX / WPS / ET / XLS / XLSX / PDF 同格式两两比对，自动识别新增、删除、修改、移动差异，支持 Word 原貌渲染、PDF 双栏对照与扫描件 OCR。

**English（如果仓库以英文为主）**

> Multi-format document comparison for DOC / DOCX / WPS / ET / XLS / XLSX / PDF — detects added, deleted, modified and moved content, with original-layout rendering, side-by-side PDF view and OCR for scans.

## 更长的介绍

### 中文

多格式文档比对助手把 7 种办公文档格式（DOC / DOCX / WPS / ET / XLS / XLSX / PDF）统一到同一套「文档 / 表格」数据模型上，再由结构化差异引擎逐块比对，因此给出的不是「文本变了多少」这种粗粒度结论，而是**哪一段、哪一行、哪一个单元格**发生了什么变化，并保留原文定位。

- **同格式比对**：两个文件的实际格式必须完全一致，格式不同的组合会被明确拒绝，而不是给出一个含义模糊的跨格式结果。
- **原貌展示**：Word 文档在浏览器里按原始排版渲染，差异高亮落在真正变化的字符上，而不是整段着色。
- **PDF 双栏对照**：左右各开一个 PDF.js 阅读器，直接渲染原始页面并叠加坐标高亮，不做「提取文本重排」这种失真的预览。
- **扫描件 OCR**：没有文本层的 PDF 页可以交给百度 / 腾讯云 / 自建 OCR 服务识别，识别结果带坐标，因此扫描页同样能参与比对与高亮；未配置时明确标注「未参与比对」。
- **可自托管**：Docker Compose 一键部署，数据全部落在本机，不依赖任何外部服务（OCR 是可选的）。

### English

DocCompare normalises 7 office formats (DOC / DOCX / WPS / ET / XLS / XLSX / PDF) into a single document /
spreadsheet model and compares them structurally. The result is not "how much text changed" but **which paragraph,
which row, which cell** changed — with the anchor to the original text kept intact.

- **Same-format only** — the two files must share the same actual format; mismatched combinations are rejected
  explicitly instead of producing a vague cross-format result.
- **Original layout** — Word documents render in the browser with their real formatting, and highlights mark only
  the characters that actually changed.
- **PDF side by side** — two PDF.js readers render the original pages with coordinate highlights instead of a
  distorted text reflow.
- **OCR for scans** — pages without a text layer can be recognised by Baidu, Tencent Cloud or a self-hosted service;
  coordinates come back with the text, so scans take part in the comparison and in the highlighting. Without OCR
  they are clearly reported as "not compared".
- **Self-hostable** — one `docker compose up` and everything stays on your own machine; OCR is optional.

## 建议的 GitHub Topics

```
document-comparison, diff, diff-tool, docx, pdf, ocr, fastapi, react, self-hosted, chinese
```

## 建议的仓库名与描述行

| 项目 | 内容 |
|---|---|
| 仓库名 | `doc_compare`（保持不变即可） |
| 描述行 | 见上方「一句话简介」 |
| 语言标记 | 约 `Python` 76% · `JavaScript` 18%（含 JSX）· `CSS` 6%（按已跟踪代码文件字节数估算，GitHub 会按自己的 linguist 口径略有出入） |
