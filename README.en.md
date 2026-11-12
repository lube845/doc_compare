# DocCompare — Multi-Format Document Comparison

A unified parsing layer, a structural diff engine and a three-pane linked view. Compares two files of the
**same format** — **DOC / DOCX / WPS / ET / XLS / XLSX / PDF** — and reports **additions, deletions, modifications
and moves**, keeping the original location of every change.

[中文文档](README.md)

> **Same-format comparison only.** The two files must have the identical actual format (DOCX↔DOCX, PDF↔PDF,
> XLSX↔XLSX, …). Any other combination is rejected by the frontend and rejected by the API with `400`.

## Features

- **Multi-format parsing** — 7 formats normalised into one document / spreadsheet data model; parse results are persisted
- **Structural diff** — paragraphs, headings, lists, tables, sheets, rows, columns and cells all get stable IDs and anchors
- **Original-layout rendering** — Word documents render in the browser with their real formatting; highlights land on the actual characters
- **PDF side-by-side** — two PDF.js readers show the original pages with coordinate-based highlights overlaid
- **OCR for scans** — Baidu / Tencent Cloud / generic HTTP; without it, scanned pages are explicitly reported as "not compared"
- **Inline highlights** — only the characters that actually changed are marked, never the whole paragraph
- **Large-file friendly** — virtualised spreadsheet rows, batched diff lists, cancellable requests, debounced search
- **Background tasks** — parsing and comparison run in the background and are recovered automatically after a restart

## Quick start

```bash
cp .env.example .env      # adjust ports, password and whether to install LibreOffice
docker compose up -d --build
```

| Service | URL |
|---|---|
| Web UI | <http://localhost:8080> |
| API | <http://localhost:8000> |
| API docs | <http://localhost:8000/docs> |

```bash
docker compose ps          # status
docker compose logs -f backend
docker compose down        # stop
docker compose down -v     # stop and wipe the database and uploaded files
```

The backend image ships **LibreOffice** by default, so DOC / WPS / ET are fully reconstructed. Turn it off for a
smaller image:

```bash
INSTALL_LIBREOFFICE=false docker compose up -d --build
```

> The first build downloads roughly 700 MB of system packages. With LibreOffice disabled, DOC / WPS / ET fall back to
> the built-in parsers and the result is clearly marked as `partial`. Always rebuild with `--build` after changing
> this flag.

Data lives in two volumes: `pgdata` (PostgreSQL) and `uploads` (original files and parse artefacts). Parse results
are stored in the database, so **nothing has to be re-uploaded after a restart**.

## Local development

```bash
# backend (SQLite by default, no database server needed)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000

# frontend
cd frontend
npm install && npm run dev      # http://localhost:5173, /api is proxied to 8000
```

### Tests

```bash
docker compose exec backend pytest tests -q      # 227 passed, 1 skipped
cd backend && python -m pytest tests -q
```

### Command line

```bash
python scripts/compare.py a.docx b.docx
python scripts/compare.py a.xlsx b.xlsx --json
```

```
比对模式：document_structure
差异统计：共 1 处（新增 0、删除 0、修改 1、移动 0）

差异 001｜修改｜数值变化：100 → 120
  A：合同金额为100万元
  B：合同金额为120万元
```

## Supported formats

| Format | Parser | Structural coverage |
|---|---|---|
| DOCX | python-docx + OOXML | Full (headings / paragraphs / tables) |
| XLSX | openpyxl | Full (multiple sheets / formulas / display values / number formats) |
| XLS | xlrd | Full (formula text unavailable; values and dates fine) |
| PDF | PyMuPDF | Logical paragraph reconstruction + coordinates + tables; scanned pages via OCR |
| DOC | LibreOffice first, native piece table otherwise | Paragraph text complete; style hierarchy needs LibreOffice |
| WPS | LibreOffice first, OLE2 fallback otherwise | Fallback mode is `partial` |
| ET | LibreOffice first, OLE2 fallback otherwise | Fallback mode is `partial` |

Full structural reconstruction of DOC / WPS / ET depends on LibreOffice. Without it the built-in parsers are used
and the result is marked `partial`, so an incomplete conclusion is never reported silently. See
[`docs/compatibility.md`](docs/compatibility.md).

## Configuration

Comparison settings live in the **比对配置** (comparison settings) panel of the UI. They are stored with the task,
so changing them and re-running the comparison reproduces the result without re-uploading anything:

| Group | Options |
|---|---|
| Global | Ignore whitespace / line breaks / headers & footers / punctuation / case / full-width |
| Document | Ignore paragraph numbering, detect moved paragraphs |
| Spreadsheet | Compare formulas / display values / cell formats / row order, ignore blank rows, primary key columns |
| PDF | Character-stream comparison switch, anchor length, parser backend |
| Thresholds | Similarity thresholds (0.85 / 0.60), row match threshold (0.75) |

### OCR

Scanned pages (PDF pages without a text layer) can be sent to a third-party OCR service. The **channel is chosen in
the settings page of the UI** — fill in the credentials, pass the connection test and the settings are stored.
**No image rebuild is required.**

| Channel | API | Coordinate granularity |
|---|---|---|
| Baidu Cloud (default) | General text recognition "with position" | Per character |
| Tencent Cloud | General accurate OCR | Per character |
| Generic HTTP | Any self-hosted / proxy service | Depends on the service; falls back to line boxes |

Without OCR configured, scanned pages are treated as "not compared" — both the upload page and the result page say
so explicitly, so a scan is never mistaken for matching content. Credentials are stored server-side only (Fernet
encrypted) and are never echoed back to the UI.

Environment variables act as **defaults for the case where nothing has been configured in the UI** (once the UI has
saved a configuration, the UI wins):

```bash
OCR_PROVIDER=baidu
BAIDU_OCR_API_KEY=your API Key
BAIDU_OCR_SECRET_KEY=your Secret Key
```

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/files/upload` | POST | Upload a file (returns immediately, parsing runs in the background) |
| `/api/files/{id}` | GET | File details including parse status and progress |
| `/api/files/{id}/preview` | GET | File preview (structured blocks / sheets) |
| `/api/files/{id}/raw` | GET | Original file (used by PDF.js, supports Range requests) |
| `/api/files/{id}/render` | GET | DOCX container of a Word file (for original-layout rendering) |
| `/api/comparisons` | GET / POST | History / create a comparison task |
| `/api/comparisons/{id}` | GET | Task status, stage, progress and statistics |
| `/api/comparisons/{id}/result` | GET | Full result |
| `/api/comparisons/{id}/changes` | GET | List of changes (filter by type / sheet / keyword) |
| `/api/comparisons/{id}/changes/{change_id}` | GET | Details of a single change |
| `/api/comparisons/{id}/config` | PUT | Update the configuration and re-run the comparison |
| `/api/comparisons/{id}/export` | POST | Export the report (markdown / html / json) |
| `/api/settings/ocr` | GET / PUT | OCR settings and credentials |
| `/api/settings/ocr/test` | POST | Connection test (does not consume recognition quota) |

Parsing and comparison both run in the background — the request only creates the task, so large files never block
the browser. Tasks interrupted by a process restart are claimed and re-run automatically.

## Environment variables

When running the backend directly, settings use the `DOC_COMPARE_*` prefix:

| Variable | Default | Description |
|---|---|---|
| `DOC_COMPARE_DATA_DIR` | `<project>/data` | Data and upload directory |
| `DOC_COMPARE_DATABASE_URL` | SQLite | PostgreSQL: `postgresql+psycopg2://user:pass@host/db` |
| `DOC_COMPARE_MAX_UPLOAD_SIZE` | `104857600` | Maximum upload size in bytes |
| `DOC_COMPARE_CORS_ORIGINS` | `http://localhost:5173,...` | Allowed front-end origins |
| `DOC_COMPARE_OCR_PROVIDER` | empty (off) | OCR channel: `baidu` / `tencent` / `custom` |
| `DOC_COMPARE_BAIDU_OCR_*` | empty | Baidu API key / secret key / language / DPI / page limit |
| `DOC_COMPARE_OCR_ALLOW_PRIVATE_ENDPOINTS` | `0` | Allow a custom OCR endpoint on a private network |

Docker Compose uses shorter `.env` names (`OCR_PROVIDER`, `MAX_UPLOAD_SIZE`, …) which `docker-compose.yml` maps onto
the full names above. See [`.env.example`](.env.example) for the complete list.

## Known limitations

- **Same-format comparison only** — DOCX vs XLSX, DOCX vs PDF and friends are rejected by the UI and answered with `400` by the API
- **PPT / PPTX are not supported**; embedded images are compared by **file hash** only (added / removed / replaced), their content is never read
- **Scans need OCR configured** — without it they are marked `partial`
- **WPS / ET need real samples** — the parsing path exists, but the repository ships no WPS-generated files
- **PDF table detection** relies on lines and alignment; complex merged or cross-page tables fall back to text comparison
- **PDF character streams are not persisted** — the first comparison after a restart re-parses the PDF
- **Word rendering is not pixel-perfect** — complex columns, text boxes and WordArt are approximated
- **Background work runs in-process** (FastAPI BackgroundTasks), without Celery / Redis, so task queuing is limited

## Documentation

| File | Content |
|---|---|
| [`prd.md`](prd.md) | Product requirements (V1.0) |
| [`docs/compatibility.md`](docs/compatibility.md) | Format compatibility report |
| [`docs/docx_display.md`](docs/docx_display.md) | Original-layout rendering for Word documents |
| [`docs/pdf_v2.md`](docs/pdf_v2.md) | PDF semantic text comparison (V2) |
| [`docs/pdf_display_impl.md`](docs/pdf_display_impl.md) | PDF side-by-side reader implementation |
| [`docs/compare_current_state.md`](docs/compare_current_state.md) | Notes on the current comparison state |
| [`docs/compare_decisions.md`](docs/compare_decisions.md) | Key decisions and their reasons |
| [`docs/compare_baseline.md`](docs/compare_baseline.md) | Comparison baseline and metrics |

## License

This project is licensed under the [Apache License 2.0](LICENSE).
