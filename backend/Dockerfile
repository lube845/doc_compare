# syntax=docker/dockerfile:1
#
# 多格式文档比对助手 · 后端镜像
#
# 构建：
#   docker build -f backend/Dockerfile -t doc-compare-backend ./backend
#
# 构建（含 LibreOffice，用于 DOC / WPS / ET 的完整结构还原）：
#   docker build -f backend/Dockerfile --build-arg INSTALL_LIBREOFFICE=true \
#       -t doc-compare-backend ./backend

FROM python:3.12-slim AS base

# 中文文档转换需要中文字体
ARG INSTALL_LIBREOFFICE=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统依赖：
#   fonts-wqy-zenhei  -> 中文字体（LibreOffice 转换 DOC / WPS / ET 时需要）
#   libreoffice-*     -> DOC / WPS / ET 的完整结构还原（可选）
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-wqy-zenhei \
        curl \
    && if [ "$INSTALL_LIBREOFFICE" = "true" ]; then \
        apt-get install -y --no-install-recommends \
            libreoffice-writer libreoffice-calc libreoffice-core \
        && rm -rf /var/lib/apt/lists/*; \
    else \
        rm -rf /var/lib/apt/lists/*; \
    fi

COPY backend/requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY backend/app ./app
COPY backend/tests ./tests
COPY scripts ./scripts

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /app /data
USER appuser

ENV DOC_COMPARE_DATA_DIR=/data \
    DOC_COMPARE_DATABASE_URL=sqlite:////data/doc_compare.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
