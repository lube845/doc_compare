"""通用 HTTP 接口：任何能「收图片、返回文字」的 HTTP 服务都能接进来。

用户在界面上填四样东西：

* **请求**：接口地址、请求头、请求体模板（``{{image}}`` 会被换成 base64 图片）；
* **响应**：文字数组在返回结构里的位置（``items_path``）、每一项里的文字
  （``text_path``）、每一项里的坐标框（``box_map``）。

坐标框用一个 JSON 描述，四条路径分别指向左/上/宽/高：

```json
{"left": "location.left", "top": "location.top",
 "width": "location.width", "height": "location.height"}
```

路径是点号分隔的，数字段表示数组下标。百度的
``words_result[].location.left`` 就是 ``location.left``；腾讯云的
``TextDetections[].ItemPolygon.X`` 就是 ``ItemPolygon.X``。

如果服务只返回文字、不返回坐标，``box_map`` 可以留空——这时文字按返回顺序
在页面上自上而下均匀分布，文本比对照常工作，但高亮位置只是估算。
"""

from __future__ import annotations

import base64
import json
import struct
from urllib.parse import quote

import httpx2 as httpx  # 注意：包名是 httpx2，不是 httpx

from ..services.url_guard import BlockedEndpoint, validate_endpoint
from .ocr_client import (
    BaseOcrProvider,
    OcrChar,
    OcrLine,
    OcrPage,
    OcrProviderField,
    OcrRequestError,
)

#: the biggest response the interface may return
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

#: 请求体模板里的占位符
IMAGE_PLACEHOLDER = "{{image}}"
IMAGE_URLENCODED_PLACEHOLDER = "{{image_urlencode}}"


def _resolve(data, path: str):
    """Follow a dotted path into a JSON structure."""
    current = data
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                raise OcrRequestError(f"路径「{path}」中的「{part}」不是数组下标") from None
            if index < 0 or index >= len(current):
                raise OcrRequestError(f"路径「{path}」超出了返回结构的长度")
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise OcrRequestError(f"返回结构里找不到「{path}」")
            current = current[part]
        else:
            raise OcrRequestError(f"路径「{path}」在返回结构里走不通")
    return current


def _png_size(image: bytes) -> tuple[int, int] | None:
    """The size of a PNG (the renderer always produces PNG)."""
    if len(image) < 24 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    try:
        width, height = struct.unpack(">II", image[16:24])
    except struct.error:
        return None
    return int(width), int(height)


class CustomProvider(BaseOcrProvider):
    """Any HTTP service that takes an image and returns text."""

    name = "custom"
    display_name = "通用 HTTP 接口"
    description = (
        "自建或代理的 OCR 服务：自己填接口地址、请求体和返回结构里文字与坐标的位置。"
    )
    fields = (
        OcrProviderField(
            key="endpoint",
            label="接口地址",
            placeholder="https://example.com/ocr",
            hint="POST 请求，图片以 base64 放在请求体里",
        ),
        OcrProviderField(
            key="content_type",
            label="请求类型",
            kind="select",
            required=False,
            default="json",
            options=("json", "form"),
        ),
        OcrProviderField(
            key="headers",
            label="请求头",
            kind="textarea",
            required=False,
            secret=True,
            placeholder="Authorization: Bearer xxxx",
            hint="每行一个「名称: 值」，可留空。填写后不再回显，留空表示不修改",
        ),
        OcrProviderField(
            key="body",
            label="请求体模板",
            kind="textarea",
            default='{"image": "{{image}}"}',
            hint="{{image}} 是 base64 图片；表单类型可用 {{image_urlencode}}",
        ),
        OcrProviderField(
            key="items_path",
            label="文字数组路径",
            placeholder="words_result",
            hint="返回结构里文字数组的位置，点号分隔",
        ),
        OcrProviderField(
            key="text_path",
            label="文字字段路径",
            placeholder="words",
            hint="数组每一项里文字的位置，相对于该项",
        ),
        OcrProviderField(
            key="box_map",
            label="坐标字段映射",
            kind="textarea",
            required=False,
            placeholder='{"left": "location.left", "top": "location.top", '
            '"width": "location.width", "height": "location.height"}',
            hint="JSON：left/top/width/height 各自指向哪；留空则按顺序估算位置",
        ),
        OcrProviderField(
            key="words_path",
            label="单字数组路径",
            required=False,
            placeholder="chars",
            hint="可选。数组每一项里单字数组的位置",
        ),
        OcrProviderField(
            key="word_text_path",
            label="单字文字路径",
            required=False,
            placeholder="char",
            hint="可选。相对于单个字",
        ),
        OcrProviderField(
            key="word_box_map",
            label="单字坐标映射",
            kind="textarea",
            required=False,
            hint="可选。JSON，格式同「坐标字段映射」",
        ),
    )

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._http: httpx.Client | None = None

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                transport=self._transport, timeout=30.0, follow_redirects=False
            )
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    # -- the request --------------------------------------------------------

    def _headers(self, values: dict[str, str]) -> dict[str, str]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        raw = self.value(values, "headers")
        for line in raw.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                raise OcrRequestError(f"请求头「{line.strip()}」缺少冒号")
            name, _, content = line.partition(":")
            headers[name.strip()] = content.strip()
        return headers

    def _body(self, image: bytes, values: dict[str, str]) -> tuple[str, dict[str, str]]:
        template = self.value(values, "body")
        if not template:
            template = '{"image": "{{image}}"}'
        encoded = base64.b64encode(image).decode("ascii")
        body = template.replace(IMAGE_URLENCODED_PLACEHOLDER, quote(encoded, safe=""))
        body = body.replace(IMAGE_PLACEHOLDER, encoded)

        if self.value(values, "content_type") == "form":
            return body, {"Content-Type": "application/x-www-form-urlencoded"}
        # The template is sent verbatim (whitespace and key order are preserved):
        # some services sign the body, and a re-serialised body would break it.
        try:
            json.loads(body)
        except ValueError as exc:
            raise OcrRequestError(f"请求体不是合法的 JSON：{exc}") from exc
        return body, {"Content-Type": "application/json; charset=utf-8"}

    def check(self, values: dict[str, str]) -> None:
        super().check(values)
        # A request without an image verifies the address and the mapping.
        self._send(b"", values)

    def recognize(self, image: bytes, values: dict[str, str]) -> OcrPage:
        if not image:
            raise OcrRequestError("待识别的图片为空")
        body = self._send(image, values)
        return self._parse(body, image, values)

    def _send(self, image: bytes, values: dict[str, str]) -> dict:
        endpoint = self.value(values, "endpoint")
        try:
            # A test transport never resolves the address, so there is
            # nothing to guard in that case.
            validate_endpoint(endpoint, resolve=self._transport is None)
        except BlockedEndpoint as exc:
            raise OcrRequestError(str(exc)) from exc
        headers = self._headers(values)
        payload, content_headers = self._body(image, values)
        headers.update(content_headers)

        status = 0
        text = ""
        try:
            # The response is read with a limit: a hostile or broken
            # service must not be able to exhaust the memory of the server.
            with self.http.stream(
                "POST", endpoint, content=payload, headers=headers
            ) as response:
                status = response.status_code
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise OcrRequestError(
                            "接口返回的内容过大"
                            f"（超过 {MAX_RESPONSE_BYTES // 1024 // 1024} MB）"
                        )
                    chunks.append(chunk)
                text = b"".join(chunks).decode("utf-8", errors="replace")
        except httpx.HTTPError as exc:
            raise OcrRequestError(
                f"接口请求失败（网络错误：{exc.__class__.__name__}）"
            ) from exc

        if status >= 400:
            raise OcrRequestError(f"接口返回了 HTTP {status}：{text[:200]}")
        try:
            body = json.loads(text)
        except ValueError as exc:
            raise OcrRequestError("接口返回的不是 JSON") from exc
        if not isinstance(body, dict):
            raise OcrRequestError("接口返回的 JSON 不是一个对象")
        return body

    # -- the response --------------------------------------------------------

    def _parse(self, body: dict, image: bytes, values: dict[str, str]) -> OcrPage:
        items_path = self.value(values, "items_path")
        items = _resolve(body, items_path)
        if not isinstance(items, list):
            raise OcrRequestError(f"「{items_path}」不是一个数组")

        box_map = self._read_map(self.value(values, "box_map"))
        word_box_map = self._read_map(self.value(values, "word_box_map"))
        words_path = self.value(values, "words_path")
        word_text_path = self.value(values, "word_text_path")
        text_path = self.value(values, "text_path")

        size = _png_size(image)
        page = OcrPage()
        band_height = 0
        if size and items:
            band_height = max(1.0, size[1] / len(items))

        for index, item in enumerate(items):
            try:
                text = str(_resolve(item, text_path)).strip()
            except OcrRequestError:
                continue
            if not text:
                continue

            box = None
            if box_map:
                box = self._box_of(item, box_map)
            if box is None and size:
                # 没有坐标：按返回顺序在页面上自上而下均匀分布
                top = index * band_height
                box = (0.0, top, float(size[0]), top + band_height)

            chars: list[OcrChar] = []
            if words_path:
                try:
                    words = _resolve(item, words_path)
                except OcrRequestError:
                    words = []
                if isinstance(words, list):
                    for word in words:
                        if word_text_path:
                            try:
                                glyph = str(_resolve(word, word_text_path))
                            except OcrRequestError:
                                continue
                        else:
                            glyph = str(word)
                        if not glyph:
                            continue
                        word_box = self._box_of(word, word_box_map) if word_box_map else None
                        if word_box is None:
                            continue
                        chars.append(
                            OcrChar(
                                ch=glyph,
                                x0=word_box[0],
                                y0=word_box[1],
                                x1=word_box[2],
                                y1=word_box[3],
                            )
                        )

            page.lines.append(
                OcrLine(
                    text=text,
                    x0=box[0] if box else 0.0,
                    y0=box[1] if box else 0.0,
                    x1=box[2] if box else 0.0,
                    y1=box[3] if box else 0.0,
                    chars=chars,
                    confidence=0.0,
                )
            )

        return page

    @staticmethod
    def _read_map(raw: str) -> dict[str, str]:
        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            mapping = json.loads(raw)
        except ValueError as exc:
            raise OcrRequestError(f"坐标映射不是合法的 JSON：{exc}") from exc
        if not isinstance(mapping, dict):
            raise OcrRequestError("坐标映射必须是一个 JSON 对象")
        return {str(key): str(value) for key, value in mapping.items()}

    @staticmethod
    def _box_of(item, box_map: dict[str, str]) -> tuple[float, float, float, float] | None:
        try:
            left = float(_resolve(item, box_map["left"]))
            top = float(_resolve(item, box_map["top"]))
            width = float(_resolve(item, box_map["width"]))
            height = float(_resolve(item, box_map["height"]))
        except (KeyError, OcrRequestError, TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return left, top, left + width, top + height
