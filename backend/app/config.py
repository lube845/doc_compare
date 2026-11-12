"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent


class Settings:
    """Runtime settings - every value can be overridden through the env."""

    def __init__(self) -> None:
        self.project_dir: Path = Path(
            os.environ.get("DOC_COMPARE_DATA_DIR", PROJECT_DIR / "data")
        )
        self.upload_dir: Path = self.project_dir / "uploads"
        self.database_url: str = os.environ.get(
            "DOC_COMPARE_DATABASE_URL",
            f"sqlite:///{self.project_dir / 'doc_compare.db'}",
        )
        self.max_upload_size: int = int(
            os.environ.get("DOC_COMPARE_MAX_UPLOAD_SIZE", 100 * 1024 * 1024)
        )
        self.allowed_extensions: set[str] = {
            ".doc",
            ".docx",
            ".wps",
            ".et",
            ".xls",
            ".xlsx",
            ".pdf",
        }
        self.cors_origins: list[str] = os.environ.get(
            "DOC_COMPARE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        self.keep_files: bool = os.environ.get("DOC_COMPARE_KEEP_FILES", "1") == "1"

        # -- OCR（扫描件识别）------------------------------------------------
        # 这里只是「尚未在界面配置时的默认值」，界面保存后以数据库为准。
        # 留空即关闭：不配置时扫描件仍然按 unsupported_ocr 拒绝比对。
        #: 默认渠道，``baidu`` / ``tencent`` / ``custom``
        self.ocr_provider: str = os.environ.get("DOC_COMPARE_OCR_PROVIDER", "").strip()
        #: 百度的默认凭证
        self.baidu_ocr_api_key: str = os.environ.get(
            "DOC_COMPARE_BAIDU_OCR_API_KEY", ""
        ).strip()
        self.baidu_ocr_secret_key: str = os.environ.get(
            "DOC_COMPARE_BAIDU_OCR_SECRET_KEY", ""
        ).strip()
        #: 识别语种，默认中英文混合
        self.baidu_ocr_language_type: str = os.environ.get(
            "DOC_COMPARE_BAIDU_OCR_LANGUAGE_TYPE", "CHN_ENG"
        )
        #: 扫描页渲染成位图的分辨率，越高越准也越慢
        self.baidu_ocr_dpi: int = int(
            os.environ.get("DOC_COMPARE_BAIDU_OCR_DPI", "200")
        )
        #: 单次请求的超时时间（秒）
        self.baidu_ocr_timeout: float = float(
            os.environ.get("DOC_COMPARE_BAIDU_OCR_TIMEOUT", "30")
        )
        #: 单个文件最多识别多少页，超出的页不识别（控制费用）
        self.baidu_ocr_max_pages: int = int(
            os.environ.get("DOC_COMPARE_BAIDU_OCR_MAX_PAGES", "100")
        )
        #: 允许「通用 HTTP 接口」指向内网地址（自建 OCR 服务）。
        #: 默认关闭：接口地址只允许公网地址，避免服务端被利用去访问
        #: 云元数据或内网管理端口。
        self.ocr_allow_private_endpoints: bool = (
            os.environ.get("DOC_COMPARE_OCR_ALLOW_PRIVATE_ENDPOINTS", "0") == "1"
        )

    def ensure_directories(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
