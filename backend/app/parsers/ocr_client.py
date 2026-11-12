"""OCR 渠道的公共部分：数据结构、异常、渠道接口与注册表。

一个「渠道」把一张图片交给某个服务，换回带坐标的文字行。不同云厂商的
认证方式、请求格式和返回结构都不一样，所以每家一个实现，都实现
:class:`BaseOcrProvider`：

* :class:`app.parsers.ocr_baidu.BaiduProvider`（默认）
* :class:`app.parsers.ocr_tencent.TencentProvider`
* :class:`app.parsers.ocr_custom.CustomProvider`（通用 HTTP 接口）

坐标约定：所有实现返回的坐标都是**图片的像素坐标**，换算成 PDF 点坐标
由 :mod:`app.parsers.ocr_parser` 统一处理。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

#: 每个渠道在界面上的字段定义（由 :mod:`app.api.settings` 交给前端渲染）
SECRET_KIND = "password"


class OcrError(Exception):
    """Base class of every OCR failure."""


class OcrNotConfigured(OcrError):
    """OCR is switched on but the credentials are missing."""


class OcrAuthError(OcrError):
    """The credentials were rejected by the provider."""


class OcrQuotaExceeded(OcrError):
    """The account has no quota left (or the rate limit was hit)."""


class OcrRequestError(OcrError):
    """The request itself failed - network, image too large, ..."""


@dataclass(slots=True)
class OcrChar:
    """One recognised character and its box, in *pixels* of the image."""

    ch: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(slots=True)
class OcrLine:
    """One recognised text line and its box, in *pixels* of the image."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    chars: list[OcrChar] = field(default_factory=list)
    #: 平均置信度（0-1），接口没有返回时为 0.0
    confidence: float = 0.0


@dataclass(slots=True)
class OcrPage:
    """The result of one recognition request."""

    lines: list[OcrLine] = field(default_factory=list)
    #: 段落 -> 行号的映射（接口的 paragraphs_result）
    paragraphs: list[list[int]] = field(default_factory=list)
    log_id: str = ""
    #: 图片方向：0 正向，1 逆时针 90 度，2 180 度，3 逆时针 270 度
    direction: int = 0

    @property
    def confidence(self) -> float:
        """The average confidence of all lines (0.0 when nothing was found)."""
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)


@dataclass(frozen=True)
class OcrProviderField:
    """One field of the configuration of a channel."""

    key: str
    label: str
    #: text / password / number / select / textarea
    kind: str = "text"
    #: a missing value makes the channel unusable
    required: bool = True
    #: the value is never sent back to the browser
    secret: bool = False
    default: str = ""
    placeholder: str = ""
    #: the values of a select
    options: tuple[str, ...] = ()
    hint: str = ""


class BaseOcrProvider:
    """What every channel does."""

    #: the identifier in the settings
    name: str = ""
    #: the name shown in the interface
    display_name: str = ""
    #: what the channel is good at
    description: str = ""
    #: the fields the user has to fill in
    fields: tuple[OcrProviderField, ...] = ()

    def value(self, values: dict[str, str], key: str) -> str:
        """One configured value, with the default of the field as fallback."""
        raw = values.get(key)
        if raw is None or str(raw).strip() == "":
            for field_ in self.fields:
                if field_.key == key:
                    return field_.default
            return ""
        return str(raw).strip()

    def credentials_complete(self, values: dict[str, str]) -> bool:
        """True when the channel has everything it needs."""
        return all(
            self.value(values, field_.key)
            for field_ in self.fields
            if field_.required
        )

    def check(self, values: dict[str, str]) -> None:
        """Verify the credentials.

        Raises an :class:`OcrError` when they are not usable.  The default
        implementation only checks that nothing is missing.
        """
        if not self.credentials_complete(values):
            missing = [
                field_.label
                for field_ in self.fields
                if field_.required and not self.value(values, field_.key)
            ]
            raise OcrNotConfigured(f"缺少配置：{'、'.join(missing)}")

    def recognize(self, image: bytes, values: dict[str, str]) -> OcrPage:
        """Recognise the text of one image."""
        raise NotImplementedError


#: every channel, by name
_PROVIDERS: dict[str, BaseOcrProvider] = {}
_LOCK = threading.Lock()


def register(provider: BaseOcrProvider) -> BaseOcrProvider:
    """Add a channel to the registry."""
    with _LOCK:
        _PROVIDERS[provider.name] = provider
    return provider


def get_provider(name: str) -> BaseOcrProvider | None:
    """The channel with that name, ``None`` when there is none."""
    return _PROVIDERS.get(name)


def all_providers() -> list[BaseOcrProvider]:
    """Every channel, in the order the interface shows them."""
    return list(_PROVIDERS.values())


def provider_names() -> list[str]:
    """The names of every channel."""
    return list(_PROVIDERS)


# The channels register themselves.  The import order decides the order in
# the interface, so the default one comes first.
from .ocr_baidu import BaiduProvider  # noqa: E402
from .ocr_tencent import TencentProvider  # noqa: E402
from .ocr_custom import CustomProvider  # noqa: E402

register(BaiduProvider())
register(TencentProvider())
register(CustomProvider())

#: the channel used when nothing was configured
DEFAULT_PROVIDER = "baidu"
