# 多格式文档比对助手

统一解析层 + 结构化差异引擎 + 三列联动展示。支持 **DOC / DOCX / WPS / ET / XLS / XLSX / PDF**
同格式文件两两比对，自动识别**新增、删除、修改、移动**差异。

> **仅支持同格式比对**：两个文件的实际格式必须完全一致（DOCX↔DOCX、PDF↔PDF、XLSX↔XLSX……），
> 格式不同的组合前端会拦截，后端返回 `400`。

实现依据：[`prd.md`](prd.md)。

## 功能特性

- **多格式解析**：7 种格式统一到同一套「文档 / 表格」数据模型，解析结果落库
- **结构化差异**：段落、标题、列表、表格、工作表、行、列、单元格都有稳定 ID 与原文定位
- **原貌展示**：Word 文档在浏览器里按原始排版渲染，差异高亮落在真实字符上
- **PDF 双栏**：左右各一个 PDF.js 阅读器，直接渲染原始页面并叠加坐标高亮
- **扫描件 OCR**：百度 / 腾讯云 / 通用 HTTP 接口，未配置时明确提示"未参与比对"
- **行内高亮**：只标出真正变化的字符，不整段着色
- **大文件优化**：表格虚拟滚动、差异清单分批渲染、请求可取消、搜索防抖
- **后台任务**：解析与比对都在后台执行，重启后自动恢复中断的任务

## 快速开始

```bash
cp .env.example .env      # 按需修改端口、密码、是否安装 LibreOffice
docker compose up -d --build
```

| 服务 | 地址 |
|---|---|
| 前端界面 | <http://localhost:8080> |
| 后端接口 | <http://localhost:8000> |
| 接口文档 | <http://localhost:8000/docs> |

```bash
docker compose ps          # 查看状态
docker compose logs -f backend
docker compose down        # 停止
docker compose down -v     # 停止并清除数据库与上传文件
```

后端镜像默认安装 **LibreOffice**，DOC / WPS / ET 可以完整还原结构。不需要时可关闭以减小镜像：

```bash
INSTALL_LIBREOFFICE=false docker compose up -d --build
```

> 首次构建含 LibreOffice 的镜像需下载约 700 MB 系统包；关闭后 DOC / WPS / ET 走内置兼容解析，
> 结果会明确标记为 `partial`。修改后必须带 `--build` 重新构建。

数据保存在两个卷里：`pgdata`（PostgreSQL）与 `uploads`（原始文件与解析产物）。
解析结果已落库，**服务重启后无需重新上传**。

## 本地开发

```bash
# 后端（默认 SQLite，无需起数据库）
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install && npm run dev      # http://localhost:5173，/api 已代理到 8000
```

### 测试

```bash
docker compose exec backend pytest tests -q      # 227 passed, 1 skipped
cd backend && python -m pytest tests -q
```

### 命令行比对

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

## 格式支持

| 格式 | 解析方式 | 结构完整度 |
|---|---|---|
| DOCX | python-docx + OOXML | 完整（标题 / 段落 / 表格） |
| XLSX | openpyxl | 完整（多工作表 / 公式 / 显示值 / 数字格式） |
| XLS | xlrd | 完整（公式文本不可得，数值与日期正常） |
| PDF | PyMuPDF | 逻辑段落重建 + 坐标 + 表格；扫描页可用 OCR |
| DOC | LibreOffice 优先；否则原生 piece table | 段落文本完整，样式层级需 LibreOffice |
| WPS | LibreOffice 优先；否则 OLE2 兜底 | 兜底模式为 `partial` |
| ET | LibreOffice 优先；否则 OLE2 兜底 | 兜底模式为 `partial` |

DOC / WPS / ET 的完整结构还原依赖 LibreOffice；未安装时走内置兜底解析并标注 `partial`，
不会静默给出不完整结论。详见 [`docs/compatibility.md`](docs/compatibility.md)。

## 配置项

比对配置在界面「比对配置」面板里调整，随任务持久化，修改后重新比对即生效：

| 分组 | 内容 |
|---|---|
| 全局 | 忽略空格 / 换行 / 页眉页脚 / 标点 / 大小写 / 全角半角等 |
| 文档 | 忽略段落编号变化、是否识别移动段落 |
| 表格 | 比较公式 / 显示值 / 单元格格式 / 行列顺序、忽略空白行列、主键列 |
| PDF | 字符流比对开关、锚点长度、解析后端等 |
| 阈值 | 相似度阈值（0.85 / 0.60）、行匹配阈值（0.75） |

### OCR

扫描件（没有文本层的 PDF 页）可以交给第三方 OCR 识别，**渠道在界面「设置」页切换**，
填写凭证后测试连接通过即可保存，**不需要重新构建镜像**。

| 渠道 | 接口 | 坐标粒度 |
|---|---|---|
| 百度智能云（默认） | 通用文字识别「标准含位置版」 | 逐字 |
| 腾讯云 | 通用文字识别「高精度版」 | 逐字 |
| 通用 HTTP 接口 | 任意自建 / 代理服务 | 取决于对方，缺坐标时按行框估算 |

未配置 OCR 时，扫描页按"未参与比对"处理，上传页与结果页都会明确提示，
不会把扫描页当成内容一致。密钥只保存在服务端（Fernet 加密），界面不回显。

环境变量作为**尚未在界面配置时的默认值**（界面一旦保存过，以界面为准）：

```bash
OCR_PROVIDER=baidu
BAIDU_OCR_API_KEY=你的 API Key
BAIDU_OCR_SECRET_KEY=你的 Secret Key
```

## API

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/files/upload` | POST | 上传文件（立即返回，解析在后台进行） |
| `/api/files/{id}` | GET | 文件详情，含解析状态与进度 |
| `/api/files/{id}/preview` | GET | 文件预览（结构化块 / 工作表） |
| `/api/files/{id}/raw` | GET | 原始文件（供 PDF.js 使用，支持 Range） |
| `/api/files/{id}/render` | GET | Word 文档的 DOCX 容器（供原貌渲染） |
| `/api/comparisons` | GET / POST | 历史记录 / 创建比对任务 |
| `/api/comparisons/{id}` | GET | 任务状态、阶段、进度与统计 |
| `/api/comparisons/{id}/result` | GET | 完整结果 |
| `/api/comparisons/{id}/changes` | GET | 差异列表（支持类型 / 工作表 / 关键词筛选） |
| `/api/comparisons/{id}/changes/{change_id}` | GET | 单条差异详情 |
| `/api/comparisons/{id}/config` | PUT | 修改配置并重新比对 |
| `/api/comparisons/{id}/export` | POST | 导出报告（markdown / html / json） |
| `/api/settings/ocr` | GET / PUT | OCR 设置与凭证 |
| `/api/settings/ocr/test` | POST | 测试连接（不消耗识别额度） |

解析与比对都在后台执行，请求只负责创建任务，大文件不会阻塞浏览器。
进程重启时中断的任务会被自动认领并重新执行。

## 环境变量

应用直接运行时使用 `DOC_COMPARE_*` 前缀：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DOC_COMPARE_DATA_DIR` | `<项目>/data` | 数据与上传目录 |
| `DOC_COMPARE_DATABASE_URL` | SQLite | PostgreSQL：`postgresql+psycopg2://user:pass@host/db` |
| `DOC_COMPARE_MAX_UPLOAD_SIZE` | `104857600` | 单文件大小上限（字节） |
| `DOC_COMPARE_CORS_ORIGINS` | `http://localhost:5173,...` | 允许的前端来源 |
| `DOC_COMPARE_OCR_PROVIDER` | 空（关闭） | OCR 渠道：`baidu` / `tencent` / `custom` |
| `DOC_COMPARE_BAIDU_OCR_*` | 空 | 百度 API Key / Secret Key / 语种 / DPI / 页数上限 |
| `DOC_COMPARE_OCR_ALLOW_PRIVATE_ENDPOINTS` | `0` | 自定义 OCR 接口是否允许指向内网 |

Docker Compose 用简短的 `.env` 变量名（`OCR_PROVIDER`、`MAX_UPLOAD_SIZE` 等），
由 `docker-compose.yml` 映射成上面的全名变量，完整清单见 [`.env.example`](.env.example)。

## 已知限制

- **仅支持同格式比对**：DOCX 与 XLSX、DOCX 与 PDF 等组合无法比对，前端拦截、后端返回 `400`
- **PPT / PPTX 尚未支持**；文档内嵌图片只比对**文件哈希**（新增 / 删除 / 替换），不识别图片里的文字
- **扫描件需自行配置 OCR**，未配置时按 `partial` 处理
- **WPS / ET 需真实样本验证**：解析链路已实现，但仓库不含 WPS 生成样本
- **PDF 表格识别**依赖线条与对齐关系，复杂合并 / 跨页表格会降级为文本比对
- **PDF 字符流不落库**：进程重启后第一次比对会重新解析一次 PDF
- **Word 原貌渲染不是像素级还原**：复杂分栏、文本框、艺术字还原度有限
- **异步执行**用进程内 BackgroundTasks，未引入 Celery / Redis，任务排队能力有限

## 文档

| 文件 | 内容 |
|---|---|
| [`prd.md`](prd.md) | 产品需求文档 V1.0 |
| [`docs/compatibility.md`](docs/compatibility.md) | 格式兼容性验证报告 |
| [`docs/docx_display.md`](docs/docx_display.md) | Word 文档原貌展示实现说明 |
| [`docs/pdf_v2.md`](docs/pdf_v2.md) | PDF 语义文本比对 V2 实现说明 |
| [`docs/pdf_display_impl.md`](docs/pdf_display_impl.md) | PDF 双栏阅读器实现说明 |
| [`docs/compare_current_state.md`](docs/compare_current_state.md) | PDF 比对现状笔记 |
| [`docs/compare_decisions.md`](docs/compare_decisions.md) | 关键决定与理由 |
| [`docs/compare_baseline.md`](docs/compare_baseline.md) | 比对基线与指标 |
