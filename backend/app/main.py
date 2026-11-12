"""FastAPI application entry point."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.comparisons import router as comparisons_router
from .api.files import router as files_router
from .api.settings import router as settings_router
from .config import settings
from .db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("doc_compare")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    settings.ensure_directories()
    from .parsers import libreoffice
    from .services import app_settings

    _load_app_settings()

    logger.info(
        "服务启动：数据库 %s；LibreOffice %s；OCR %s",
        settings.database_url,
        "可用" if libreoffice.is_available() else "不可用（DOC/WPS/ET 使用兼容解析）",
        "已启用" if app_settings.get().enabled else "未启用",
    )
    _start_recovery()
    yield


def _load_app_settings() -> None:
    """Read the settings the user changed in the browser."""
    from .db import SessionLocal
    from .services import app_settings

    session = SessionLocal()
    try:
        app_settings.load(session)
    except Exception:  # noqa: BLE001 - the defaults are used instead
        logger.exception("读取应用设置失败，使用环境变量默认值")
    finally:
        session.close()


def _start_recovery() -> None:
    """Re-run the tasks that a previous process left behind.

    Background tasks live inside the process, so a restart silently drops
    them while their database state still says "running".  The recovery runs
    in its own thread and does not delay the startup.
    """
    from .services.tasks import recover_interrupted_work

    def run() -> None:
        try:
            recover_interrupted_work()
        except Exception:  # noqa: BLE001 - recovery must never stop the app
            logger.exception("中断任务恢复失败")

    threading.Thread(target=run, name="task-recovery", daemon=True).start()


app = FastAPI(
    lifespan=lifespan,
    title="多格式文档比对助手",
    description=(
        "支持 DOC / DOCX / WPS / ET / XLS / XLSX / PDF 的两两比对，"
        "自动识别新增、删除、修改与移动差异。"
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(files_router)
app.include_router(comparisons_router)
app.include_router(settings_router)


@app.get("/api/health")
def health() -> dict:
    from .parsers import libreoffice

    return {
        "status": "ok",
        "version": app.version,
        "libreoffice": libreoffice.is_available(),
        "allowed_extensions": sorted(settings.allowed_extensions),
    }
