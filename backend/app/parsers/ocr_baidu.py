"""百度智能云 · 通用文字识别（标准含位置版）。

用「标准含位置版」而不是「标准版」的原因：标准版（``general_basic``）只返回文本
字符串，没有坐标。本项目的 PDF 比对建立在字符坐标流之上，没有坐标就无法在前端
高亮差异位置。标准含位置版价格档位与标准版相同。
"""

from __future__ import annotations

import base64
import threading
import time

import httpx2 as httpx  # 注意：包名是 httpx2，不是 httpx

from .ocr_client import (
    BaseOcrProvider,
    OcrAuthError,
    OcrChar,
    OcrLine,
    OcrPage,
    OcrProviderField,
    OcrQuotaExceeded,
    OcrRequestError,
)

#: access_token 的缓存：api_key -> (token, 过期时刻)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
#: 提前多久认为 token 已过期（秒）
_TOKEN_SAFETY_MARGIN = 60.0
#: 百度错误码：token 无效 / 未授权，刷新 token 后可以重试
_TOKEN_ERROR_CODES = frozenset({19, 20, 21})
#: 百度错误码：配额、频率、欠费
_QUOTA_ERROR_CODES = frozenset({4, 17, 18, 110, 111})

#: 百度支持的语种
LANGUAGES = (
    "CHN_ENG",
    "ENG",
    "JAP",
    "KOR",
    "FRE",
    "SPA",
    "POR",
    "GER",
    "ITA",
    "RUS",
)


def _rect(location: dict | None) -> tuple[float, float, float, float] | None:
    """Read a Baidu ``location`` object into a box."""
    if not isinstance(location, dict):
        return None
    try:
        left = float(location.get("left", 0))
        top = float(location.get("top", 0))
        width = float(location.get("width", 0))
        height = float(location.get("height", 0))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return left, top, left + width, top + height


class BaiduProvider(BaseOcrProvider):
    """Baidu general text recognition (the version with positions)."""

    name = "baidu"
    display_name = "百度智能云"
    description = (
        "通用文字识别（标准含位置版）。逐字返回坐标，高亮最精确；"
        "免费额度用完后按量计费。"
    )
    fields = (
        OcrProviderField(
            key="api_key",
            label="API Key",
            kind="password",
            secret=True,
            placeholder="百度智能云 · 访问控制 · API 密钥管理",
        ),
        OcrProviderField(
            key="secret_key",
            label="Secret Key",
            kind="password",
            secret=True,
            placeholder="百度智能云 · 访问控制 · API 密钥管理",
        ),
        OcrProviderField(
            key="language_type",
            label="识别语种",
            kind="select",
            required=False,
            default="CHN_ENG",
            options=LANGUAGES,
        ),
    )

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._http: httpx.Client | None = None
        self._lock = threading.Lock()

    # -- the http client ---------------------------------------------------

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(transport=self._transport, timeout=30.0)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    # -- the access token --------------------------------------------------

    def access_token(self, values: dict[str, str], force_refresh: bool = False) -> str:
        """Return a valid access token, fetching a new one when needed.

        The token is valid for 30 days; it is cached so that a document with
        100 scanned pages does not fetch 100 tokens.
        """
        api_key = self.value(values, "api_key")
        with self._lock:
            cached = _TOKEN_CACHE.get(api_key)
            if (
                not force_refresh
                and cached is not None
                and cached[1] - _TOKEN_SAFETY_MARGIN > time.time()
            ):
                return cached[0]
            token, expires_in = self._fetch_token(values)
            _TOKEN_CACHE[api_key] = (token, time.time() + float(expires_in))
            return token

    def _fetch_token(self, values: dict[str, str]) -> tuple[str, float]:
        params = {
            "grant_type": "client_credentials",
            "client_id": self.value(values, "api_key"),
            "client_secret": self.value(values, "secret_key"),
        }
        try:
            response = self.http.get(
                "https://aip.baidubce.com/oauth/2.0/token", params=params
            )
        except httpx.HTTPError as exc:
            # The message of the exception contains the url - and the url
            # contains the secret key.  Never let it reach a log or a page.
            raise OcrRequestError(
                f"获取百度 access_token 失败（网络错误：{exc.__class__.__name__}）"
            ) from exc

        if response.status_code in (401, 403):
            raise OcrAuthError("百度拒绝了 API Key / Secret Key，请检查凭证")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OcrRequestError(
                f"获取百度 access_token 失败（HTTP {response.status_code}）"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise OcrRequestError("百度 access_token 接口返回了非 JSON 内容") from exc

        token = payload.get("access_token")
        if not token:
            detail = payload.get("error_description") or payload.get("error") or payload
            raise OcrAuthError(f"百度 access_token 获取失败：{detail}")
        return str(token), float(payload.get("expires_in") or 2592000)

    # -- the recognition ----------------------------------------------------

    def check(self, values: dict[str, str]) -> None:
        super().check(values)
        # Fetching a token is enough and does not use any quota.
        self.access_token(values, force_refresh=True)

    def recognize(self, image: bytes, values: dict[str, str]) -> OcrPage:
        """Recognise the text of one image."""
        if not image:
            raise OcrRequestError("待识别的图片为空")

        payload = {
            "image": base64.b64encode(image).decode("ascii"),
            # small -> the API returns the position of every single character
            "recognize_granularity": "small",
            "probability": "true",
            "paragraph": "true",
            # The coordinates must belong to the image we sent, otherwise the
            # highlights of the front end land in the wrong place.  The
            # direction of the page is therefore not detected here - a
            # rotated scan is recognised poorly, but a misplaced rectangle
            # is much harder to notice than garbled text.
            "detect_direction": "false",
            "language_type": self.value(values, "language_type"),
        }
        return self._request(payload, values, retry_on_token_error=True)

    def _request(
        self,
        payload: dict[str, str],
        values: dict[str, str],
        retry_on_token_error: bool,
    ) -> OcrPage:
        token = self.access_token(values)
        url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/general?access_token={token}"
        try:
            response = self.http.post(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            # The url of the exception carries the access token.
            raise OcrRequestError(
                f"百度 OCR 请求失败（网络错误：{exc.__class__.__name__}）"
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise OcrRequestError(
                f"百度 OCR 返回了非 JSON 内容（HTTP {response.status_code}）"
            ) from exc

        error_code = body.get("error_code")
        if error_code is not None:
            code = int(error_code)
            detail = body.get("error_msg") or body.get("error_description") or ""
            if code in _TOKEN_ERROR_CODES and retry_on_token_error:
                # The token was rejected - get a new one and try once more.
                _TOKEN_CACHE.pop(self.value(values, "api_key"), None)
                return self._request(payload, values, retry_on_token_error=False)
            if code in _QUOTA_ERROR_CODES:
                raise OcrQuotaExceeded(f"百度 OCR 配额不足或频率超限：{detail}（错误码 {code}）")
            raise OcrRequestError(f"百度 OCR 调用失败：{detail}（错误码 {code}）")

        return self._parse(body)

    # -- the response --------------------------------------------------------

    @staticmethod
    def _parse(body: dict) -> OcrPage:
        page = OcrPage(
            log_id=str(body.get("log_id", "")),
            direction=int(body.get("direction") or 0),
        )

        for item in body.get("words_result") or []:
            if not isinstance(item, dict):
                continue
            text = (item.get("words") or "").strip()
            if not text:
                continue
            box = _rect(item.get("location"))
            if box is None:
                continue

            chars: list[OcrChar] = []
            for raw in item.get("chars") or []:
                if not isinstance(raw, dict):
                    continue
                glyph = raw.get("char") or ""
                if not glyph:
                    continue
                char_box = _rect(raw.get("location"))
                if char_box is None:
                    continue
                chars.append(
                    OcrChar(
                        ch=glyph,
                        x0=char_box[0],
                        y0=char_box[1],
                        x1=char_box[2],
                        y1=char_box[3],
                    )
                )

            probability = item.get("probability") or {}
            confidence = 0.0
            if isinstance(probability, dict):
                try:
                    confidence = float(probability.get("average") or 0.0)
                except (TypeError, ValueError):
                    confidence = 0.0

            page.lines.append(
                OcrLine(
                    text=text,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                    chars=chars,
                    confidence=confidence,
                )
            )

        for paragraph in body.get("paragraphs_result") or []:
            if not isinstance(paragraph, dict):
                continue
            indices = paragraph.get("words_result_idx") or []
            page.paragraphs.append([int(index) for index in indices])

        return page
