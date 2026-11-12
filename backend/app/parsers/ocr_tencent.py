"""腾讯云 · 通用文字识别（高精度版）。

接口：``GeneralAccurateOCR``（域 ``ocr.tencentcloudapi.com``，版本 ``2018-11-19``）。
认证用腾讯云的签名方法 v3（TC3-HMAC-SHA256），这里用标准库实现，不引入 SDK。

请求参数 ``IsWords=true`` 时接口会返回每个字的坐标（``Words``），和百度的
``recognize_granularity=small`` 对应，高亮同样能落到字上。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx2 as httpx  # 注意：包名是 httpx2，不是 httpx

from .ocr_client import (
    BaseOcrProvider,
    OcrChar,
    OcrError,
    OcrLine,
    OcrPage,
    OcrProviderField,
    OcrQuotaExceeded,
    OcrRequestError,
)

#: 接口域名
HOST = "ocr.tencentcloudapi.com"
#: 接口版本
VERSION = "2018-11-19"
#: 接口名
ACTION = "GeneralAccurateOCR"
#: 签名里的服务名（必须与域名一致）
SERVICE = "ocr"

#: 腾讯云的错误码：欠费 / 配额 / 频率
_QUOTA_CODES = frozenset({"AuthFailure.SecretIdNotFound", "LimitExceeded"})


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def sign(
    secret_id: str,
    secret_key: str,
    action: str,
    payload: str,
    timestamp: int | None = None,
    host: str = HOST,
    version: str = VERSION,
    region: str = "",
) -> dict[str, str]:
    """The headers of a signed request (腾讯云签名方法 v3).

    The signature covers the body, the host and the name of the action, so
    it has to be computed for every request - there is no token to cache.
    """
    moment = int(time.time()) if timestamp is None else int(timestamp)
    # The date of the credential scope is the UTC date of the timestamp.
    date = time.strftime("%Y-%m-%d", time.gmtime(moment))

    content_type = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
    signed_headers = "content-type;host"

    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            _sha256_hex(payload.encode("utf-8")),
        ]
    )

    credential_scope = f"{date}/{SERVICE}/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(moment),
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )

    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, SERVICE)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(moment),
        "X-TC-Version": version,
    }
    if region:
        headers["X-TC-Region"] = region
    return headers


def _box(polygon: dict | None) -> tuple[float, float, float, float] | None:
    """Read a Tencent ``ItemPolygon`` into a box."""
    if not isinstance(polygon, dict):
        return None
    try:
        x = float(polygon.get("X", 0))
        y = float(polygon.get("Y", 0))
        width = float(polygon.get("Width", 0))
        height = float(polygon.get("Height", 0))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, x + width, y + height


class TencentProvider(BaseOcrProvider):
    """Tencent Cloud general text recognition (accurate version)."""

    name = "tencent"
    display_name = "腾讯云"
    description = (
        "通用文字识别（高精度版）。密集小字、倾斜文本和手写体更准；"
        "开启单字信息后同样能高亮到字。"
    )
    fields = (
        OcrProviderField(
            key="secret_id",
            label="SecretId",
            placeholder="腾讯云 · 访问管理 · API 密钥管理",
        ),
        OcrProviderField(
            key="secret_key",
            label="SecretKey",
            kind="password",
            secret=True,
            placeholder="腾讯云 · 访问管理 · API 密钥管理",
        ),
        OcrProviderField(
            key="region",
            label="地域 Region",
            required=False,
            default="ap-guangzhou",
            hint="接口域名固定为 ocr.tencentcloudapi.com，地域只是公共参数",
        ),
        OcrProviderField(
            key="config_id",
            label="识别模式 ConfigID",
            kind="select",
            required=False,
            default="OCR",
            options=("OCR", "MulOCR"),
            hint="OCR 为通用场景，MulOCR 支持 100+ 语种",
        ),
    )

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._http: httpx.Client | None = None

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(transport=self._transport, timeout=30.0)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    # -- the request --------------------------------------------------------

    def _body(self, image: bytes, values: dict[str, str]) -> str:
        payload = {
            "ImageBase64": base64.b64encode(image).decode("ascii"),
            # the position of every single character
            "IsWords": True,
            "ConfigID": self.value(values, "config_id"),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _headers(self, payload: str, values: dict[str, str]) -> dict[str, str]:
        return sign(
            secret_id=self.value(values, "secret_id"),
            secret_key=self.value(values, "secret_key"),
            action=ACTION,
            payload=payload,
            region=self.value(values, "region"),
        )

    def check(self, values: dict[str, str]) -> None:
        super().check(values)
        # A signed request without an image still verifies the credentials.
        response = self._send({"ImageBase64": ""}, values)
        body = self._body_of(response)
        error = body.get("Response", {}).get("Error")
        if error:
            raise OcrRequestError(
                f"腾讯云拒绝了请求：{error.get('Message', '')}（{error.get('Code', '')}）"
            )

    def recognize(self, image: bytes, values: dict[str, str]) -> OcrPage:
        if not image:
            raise OcrRequestError("待识别的图片为空")
        payload = self._body(image, values)
        response = self._send(json.loads(payload), values)
        body = self._body_of(response)

        error = body.get("Response", {}).get("Error")
        if error:
            code = str(error.get("Code") or "")
            detail = error.get("Message") or ""
            if code in _QUOTA_CODES:
                raise OcrQuotaExceeded(f"腾讯云 OCR 配额不足或频率超限：{detail}")
            raise OcrRequestError(f"腾讯云 OCR 调用失败：{detail}（{code}）")

        data = body.get("Response") or {}
        return self._parse(data)

    def _send(self, payload: dict, values: dict[str, str]) -> httpx.Response:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        headers = self._headers(body, values)
        try:
            return self.http.post(f"https://{HOST}/", content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise OcrRequestError(
                f"腾讯云 OCR 请求失败（网络错误：{exc.__class__.__name__}）"
            ) from exc

    @staticmethod
    def _body_of(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise OcrRequestError(
                f"腾讯云 OCR 返回了非 JSON 内容（HTTP {response.status_code}）"
            ) from exc
        if not isinstance(body, dict):
            raise OcrRequestError("腾讯云 OCR 返回了意料之外的内容")
        return body

    # -- the response --------------------------------------------------------

    @staticmethod
    def _parse(data: dict) -> OcrPage:
        page = OcrPage(log_id=str(data.get("RequestId", "")))

        for item in data.get("TextDetections") or []:
            if not isinstance(item, dict):
                continue
            text = (item.get("DetectedText") or "").strip()
            if not text:
                continue
            box = _box(item.get("ItemPolygon"))
            if box is None:
                polygon = item.get("Polygon") or []
                if len(polygon) >= 2:
                    xs = [float(point.get("X", 0)) for point in polygon]
                    ys = [float(point.get("Y", 0)) for point in polygon]
                    box = (min(xs), min(ys), max(xs), max(ys))
            if box is None:
                continue

            chars: list[OcrChar] = []
            for word in item.get("Words") or []:
                if not isinstance(word, dict):
                    continue
                glyph = word.get("Character") or ""
                if not glyph:
                    continue
                char_box = _box(word.get("Polygon"))
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

            try:
                confidence = float(item.get("Confidence", 0)) / 100.0
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

        return page
