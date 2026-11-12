"""Tests of the OCR channels and of the scanned page handling.

Every channel is tested against a mocked HTTP transport, so the suite never
talks to a real provider.  The parser is tested with a fake channel that
hands back a canned recognition result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx2 as httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.parsers import ocr_client, ocr_parser  # noqa: E402
from app.parsers.ocr_baidu import BaiduProvider  # noqa: E402
from app.parsers.ocr_client import (  # noqa: E402
    OcrChar,
    OcrLine,
    OcrNotConfigured,
    OcrPage,
    OcrQuotaExceeded,
    OcrRequestError,
)
from app.parsers.ocr_custom import CustomProvider  # noqa: E402
from app.parsers.ocr_parser import ocr_page_to_lines  # noqa: E402
from app.parsers.ocr_tencent import HOST, sign  # noqa: E402
from app.parsers.pdf_parser import PdfParser  # noqa: E402
from app.services import app_settings  # noqa: E402
from app.services.app_settings import OcrSettings  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

BAIDU_VALUES = {
    "api_key": "the-api-key",
    "secret_key": "the-secret-key",
    "language_type": "CHN_ENG",
}


def _ocr_settings(**kwargs) -> OcrSettings:
    """The settings the parsers read."""
    values = dict(provider="baidu", values=BAIDU_VALUES, dpi=200, max_pages=100)
    values.update(kwargs)
    return OcrSettings(**values)


def _baidu_response(text: str = "合同编号：HT-2024-001") -> dict:
    """A response of the API, with the position of every character."""
    width = 20
    return {
        "log_id": 12345,
        "words_result_num": 1,
        "words_result": [
            {
                "words": text,
                "location": {"left": 100, "top": 200, "width": width * len(text), "height": 30},
                "chars": [
                    {
                        "char": glyph,
                        "location": {
                            "left": 100 + width * index,
                            "top": 200,
                            "width": width,
                            "height": 30,
                        },
                    }
                    for index, glyph in enumerate(text)
                ],
                "probability": {"average": 0.96, "variance": 0.01, "min": 0.9},
            }
        ],
    }


class _FakeTransport:
    """Records the requests and answers with canned responses."""

    def __init__(self, recognise_responses: list, token_response: dict | None = None):
        self.recognise_responses = list(recognise_responses)
        self.token_response = token_response or {
            "access_token": "token",
            "expires_in": 2592000,
        }
        self.token_calls = 0
        self.recognise_calls = 0
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if "oauth" in str(request.url):
            self.token_calls += 1
            return httpx.Response(
                200,
                json={**self.token_response, "access_token": f"token-{self.token_calls}"},
            )
        self.recognise_calls += 1
        body = (
            self.recognise_responses.pop(0)
            if self.recognise_responses
            else _baidu_response()
        )
        return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def _clean_token_cache():
    baidu_module = sys.modules["app.parsers.ocr_baidu"]
    baidu_module._TOKEN_CACHE.clear()
    yield
    baidu_module._TOKEN_CACHE.clear()


def _baidu(transport) -> BaiduProvider:
    return BaiduProvider(transport=httpx.MockTransport(transport))


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------


def test_the_default_channel_is_baidu_and_all_channels_are_known():
    assert ocr_client.provider_names()[0] == "baidu"
    assert set(ocr_client.provider_names()) == {"baidu", "tencent", "custom"}
    assert ocr_client.DEFAULT_PROVIDER == "baidu"
    for provider in ocr_client.all_providers():
        assert provider.display_name


def test_a_channel_reports_its_fields_for_the_interface():
    provider = ocr_client.get_provider("baidu")
    keys = [field.key for field in provider.fields]
    assert "api_key" in keys and "secret_key" in keys
    secret = [field for field in provider.fields if field.key == "secret_key"][0]
    assert secret.secret is True


# --------------------------------------------------------------------------
# baidu
# --------------------------------------------------------------------------


def test_the_baidu_token_is_fetched_once_and_then_reused():
    transport = _FakeTransport([_baidu_response(), _baidu_response()])
    client = _baidu(transport)

    client.recognize(b"png-bytes", BAIDU_VALUES)
    client.recognize(b"png-bytes", BAIDU_VALUES)

    assert transport.token_calls == 1
    assert transport.recognise_calls == 2


def test_a_rejected_baidu_token_is_refreshed_and_the_call_is_retried():
    transport = _FakeTransport(
        [{"error_code": 19, "error_msg": "Invalid Access Token"}, _baidu_response()]
    )
    client = _baidu(transport)

    page = client.recognize(b"png-bytes", BAIDU_VALUES)

    assert page.lines[0].text == "合同编号：HT-2024-001"
    assert transport.recognise_calls == 2
    assert transport.token_calls == 2


def test_a_baidu_quota_error_is_reported_as_such():
    transport = _FakeTransport([{"error_code": 110, "error_msg": "服务未开通"}])
    client = _baidu(transport)

    with pytest.raises(OcrQuotaExceeded):
        client.recognize(b"png-bytes", BAIDU_VALUES)


def test_a_baidu_error_keeps_the_message_of_the_api():
    transport = _FakeTransport([{"error_code": 201, "error_msg": "图像格式错误"}])
    client = _baidu(transport)

    with pytest.raises(OcrRequestError) as error:
        client.recognize(b"png-bytes", BAIDU_VALUES)

    assert "图像格式错误" in str(error.value)


def test_the_baidu_response_is_read_into_lines_and_characters():
    transport = _FakeTransport([_baidu_response()])
    client = _baidu(transport)

    page = client.recognize(b"png-bytes", BAIDU_VALUES)

    assert page.log_id == "12345"
    assert len(page.lines) == 1
    line = page.lines[0]
    assert line.text == "合同编号：HT-2024-001"
    assert len(line.chars) == len(line.text)
    assert line.chars[0].ch == "合"
    assert line.chars[0].x0 == 100
    assert line.confidence == pytest.approx(0.96)


def test_a_baidu_auth_error_does_not_leak_the_secret():
    secret = "SUPERSECRET"
    provider = BaiduProvider(
        transport=httpx.MockTransport(lambda request: httpx.Response(401))
    )

    with pytest.raises(ocr_client.OcrAuthError) as error:
        provider.check(
            {"api_key": "k", "secret_key": secret, "language_type": "CHN_ENG"}
        )

    assert secret not in str(error.value)


# --------------------------------------------------------------------------
# tencent
# --------------------------------------------------------------------------


def test_the_tencent_signature_follows_the_documented_example():
    """The example of the Tencent signature v3 documentation."""
    headers = sign(
        secret_id="AKIDEXAMPLE",
        secret_key="SECRETKEY",
        action="DescribeInstances",
        payload='{"Limit": 1}',
        timestamp=1551113065,
    )
    assert headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=AKIDEXAMPLE/2019-")
    assert "SignedHeaders=content-type;host" in headers["Authorization"]
    assert headers["X-TC-Timestamp"] == "1551113065"
    assert headers["Host"] == HOST
    # The date of the credential scope is the UTC date of the timestamp.
    assert "2019-02-25/ocr/tc3_request" in headers["Authorization"]


def test_the_tencent_signature_changes_with_the_body():
    first = sign("id", "key", "GeneralAccurateOCR", '{"a":1}', timestamp=1700000000)
    second = sign("id", "key", "GeneralAccurateOCR", '{"a":2}', timestamp=1700000000)
    assert first["Authorization"] != second["Authorization"]


TENCENT_VALUES = {
    "secret_id": "AKID",
    "secret_key": "SK",
    "region": "ap-guangzhou",
    "config_id": "OCR",
}


def _tencent(transport) -> ocr_client.BaseOcrProvider:
    provider = ocr_client.get_provider("tencent")
    provider._http = httpx.Client(transport=httpx.MockTransport(transport))
    return provider


def test_the_tencent_response_is_read_into_lines_and_words():
    body = {
        "Response": {
            "RequestId": "req-1",
            "TextDetections": [
                {
                    "DetectedText": "合同编号",
                    "Confidence": 98,
                    "ItemPolygon": {"X": 10, "Y": 20, "Width": 80, "Height": 30},
                    "Words": [
                        {
                            "Character": "合",
                            "Polygon": {"X": 10, "Y": 20, "Width": 20, "Height": 30},
                        },
                        {
                            "Character": "同",
                            "Polygon": {"X": 30, "Y": 20, "Width": 20, "Height": 30},
                        },
                    ],
                }
            ],
        }
    }
    provider = _tencent(lambda request: httpx.Response(200, json=body))

    page = provider.recognize(b"png", TENCENT_VALUES)

    line = page.lines[0]
    assert line.text == "合同编号"
    assert line.x0 == 10 and line.x1 == 90
    assert line.confidence == pytest.approx(0.98)
    assert [char.ch for char in line.chars] == ["合", "同"]


def test_a_tencent_error_is_reported_with_the_message():
    provider = _tencent(
        lambda request: httpx.Response(
            200, json={"Response": {"Error": {"Code": "AuthFailure.SignatureFailure", "Message": "签名错误"}}}
        )
    )

    with pytest.raises(OcrRequestError) as error:
        provider.recognize(b"png", TENCENT_VALUES)

    assert "签名错误" in str(error.value)


def test_the_tencent_request_is_signed():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"Response": {"TextDetections": []}})

    provider = _tencent(handler)
    provider.recognize(b"png", TENCENT_VALUES)

    assert seen["headers"]["authorization"].startswith("TC3-HMAC-SHA256")
    assert seen["headers"]["x-tc-action"] == "GeneralAccurateOCR"
    assert seen["headers"]["x-tc-version"] == "2018-11-19"
    assert '"IsWords":true' in seen["body"]


# --------------------------------------------------------------------------
# the generic HTTP channel
# --------------------------------------------------------------------------

CUSTOM_VALUES = {
    "endpoint": "https://example.test/ocr",
    "content_type": "json",
    "headers": "Authorization: Bearer token",
    "body": '{"image": "{{image}}"}',
    "items_path": "words_result",
    "text_path": "words",
    "box_map": '{"left": "location.left", "top": "location.top", '
    '"width": "location.width", "height": "location.height"}',
}


def _custom(handler) -> CustomProvider:
    return CustomProvider(transport=httpx.MockTransport(handler))


def test_the_generic_channel_maps_the_response_with_paths():
    body = {
        "words_result": [
            {
                "words": "第一行",
                "location": {"left": 5, "top": 6, "width": 60, "height": 20},
            }
        ]
    }
    provider = _custom(lambda request: httpx.Response(200, json=body))

    page = provider.recognize(b"png", CUSTOM_VALUES)

    assert page.lines[0].text == "第一行"
    assert (page.lines[0].x0, page.lines[0].y0) == (5, 6)


def test_the_generic_channel_without_boxes_spreads_the_lines_over_the_page():
    provider = _custom(
        lambda request: httpx.Response(200, json={"words_result": [{"words": "A"}, {"words": "B"}]})
    )

    page = provider.recognize(_png_header(800, 1000), {**CUSTOM_VALUES, "box_map": ""})

    assert len(page.lines) == 2
    assert page.lines[0].y0 == 0
    assert page.lines[1].y0 == 500


def test_the_generic_channel_sends_the_configured_headers():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"words_result": []})

    provider = _custom(handler)
    provider.recognize(b"png", CUSTOM_VALUES)

    assert seen["headers"]["authorization"] == "Bearer token"
    assert "image" in seen["body"]


def test_the_generic_channel_reports_a_missing_path():
    provider = _custom(lambda request: httpx.Response(200, json={"other": []}))

    with pytest.raises(OcrRequestError) as error:
        provider.recognize(b"png", CUSTOM_VALUES)

    assert "words_result" in str(error.value)


def test_the_generic_channel_reports_an_http_error():
    provider = _custom(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(OcrRequestError) as error:
        provider.recognize(b"png", CUSTOM_VALUES)

    assert "500" in str(error.value)


def _png_header(width: int, height: int) -> bytes:
    """A PNG header with a known size (enough for the position estimate)."""
    import struct

    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


# --------------------------------------------------------------------------
# the coordinates
# --------------------------------------------------------------------------


def test_the_pixel_coordinates_become_pdf_points():
    page = OcrPage(
        lines=[
            OcrLine(
                text="AB",
                x0=100,
                y0=200,
                x1=140,
                y1=230,
                chars=[
                    OcrChar("A", 100, 200, 120, 230),
                    OcrChar("B", 120, 200, 140, 230),
                ],
                confidence=0.9,
            )
        ]
    )

    lines = ocr_page_to_lines(page, 1, scale_x=0.5, scale_y=0.25, page_width=595)

    line = lines[0]
    assert line.text == "AB"
    assert line.chars[0][0] == pytest.approx(50.0)
    assert line.chars[0][1] == pytest.approx(50.0)
    assert line.bbox[0] == pytest.approx(50.0)
    assert line.bbox[3] == pytest.approx(57.5)


def test_a_line_without_character_positions_still_fills_the_stream():
    """A channel that returns word-level boxes only: the characters spread."""
    page = OcrPage(lines=[OcrLine(text="abcd", x0=0, y0=0, x1=100, y1=20)])

    lines = ocr_page_to_lines(page, 1, scale_x=1.0, scale_y=1.0, page_width=595)

    assert len(lines[0].chars) == 4
    assert "".join(char[4] for char in lines[0].chars) == "abcd"


def test_a_rotated_page_is_mapped_back_to_the_unrotated_space(tmp_path: Path):
    import fitz

    path = tmp_path / "rotated.pdf"
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 120), "Hello", fontsize=14, fontname="china-s")
    page.set_rotation(90)
    document.save(str(path))
    document.close()

    document = fitz.open(str(path))
    page = document[0]
    _, scale_x, scale_y = ocr_parser.render_page(page)

    rotated = fitz.Point(72, 105) * page.rotation_matrix
    pixel_x = rotated.x / scale_x
    pixel_y = rotated.y / scale_y

    class _Provider:
        def recognize(self, image: bytes, values) -> OcrPage:
            return OcrPage(
                lines=[
                    OcrLine(
                        text="H",
                        x0=pixel_x - 5,
                        y0=pixel_y - 5,
                        x1=pixel_x + 5,
                        y1=pixel_y + 5,
                        chars=[OcrChar("H", pixel_x - 5, pixel_y - 5, pixel_x + 5, pixel_y + 5)],
                        confidence=0.9,
                    )
                ]
            )

    result = ocr_parser.ocr_page_lines(page, 1, client=_Provider(), values={})

    x0, y0, x1, y1, _glyph = result.lines[0].chars[0]
    assert (x0 + x1) / 2 == pytest.approx(72.0, abs=1.0)
    assert (y0 + y1) / 2 == pytest.approx(105.0, abs=1.0)
    document.close()


# --------------------------------------------------------------------------
# the parser
# --------------------------------------------------------------------------


def _build_scanned_pdf(path: Path, texts: list[str]) -> Path:
    """A PDF whose pages are images: there is no text layer at all."""
    import fitz

    document = fitz.open()
    for text in texts:
        source = fitz.open()
        picture = source.new_page(width=595, height=842)
        picture.insert_text((72, 120), text, fontsize=14, fontname="china-s")
        pixmap = picture.get_pixmap(matrix=fitz.Matrix(2, 2))
        source.close()

        page = document.new_page(width=595, height=842)
        page.insert_image(page.rect, pixmap=pixmap)
    document.save(str(path))
    document.close()
    return path


class _FakeProvider:
    """A channel that recognises the same text on every page."""

    def __init__(self, text: str = "合同编号：HT-2024-001"):
        self.text = text

    def recognize(self, image: bytes, values) -> OcrPage:
        return OcrPage(
            lines=[
                OcrLine(
                    text=self.text,
                    x0=100,
                    y0=200,
                    x1=100 + 20 * len(self.text),
                    y1=230,
                    chars=[
                        OcrChar(glyph, 100 + 20 * index, 200, 120 + 20 * index, 230)
                        for index, glyph in enumerate(self.text)
                    ],
                    confidence=0.95,
                )
            ],
            log_id="fake",
        )


def test_a_scanned_page_is_recognised_and_takes_part_in_the_comparison(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(app_settings, "get", lambda: _ocr_settings())
    monkeypatch.setattr(ocr_parser, "get_provider", lambda name: _FakeProvider())
    path = _build_scanned_pdf(tmp_path / "scan.pdf", ["合同编号：HT-2024-001"])

    result = PdfParser().parse(path)

    assert result.parse_status == "success"
    page = result.parsed_pdf.pages[0]
    assert page.source == "ocr"
    assert "ocr" in page.flags
    assert page.char_count == len("合同编号：HT-2024-001")
    assert result.metadata["ocr_pages"] == [1]


def test_a_page_that_cannot_be_recognised_is_left_out(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_settings, "get", lambda: _ocr_settings())

    class _Broken:
        def recognize(self, image, values):
            raise OcrRequestError("调用失败：图像格式错误（错误码 201）")

    monkeypatch.setattr(ocr_parser, "get_provider", lambda name: _Broken())
    path = _build_scanned_pdf(tmp_path / "scan.pdf", ["合同编号：HT-2024-001"])

    result = PdfParser().parse(path)

    assert result.parse_status == "unsupported_ocr"
    assert result.metadata["ocr_errors"]


def test_without_a_channel_a_scan_is_still_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        app_settings, "get", lambda: _ocr_settings(provider="")
    )
    path = _build_scanned_pdf(tmp_path / "scan.pdf", ["合同编号：HT-2024-001"])

    result = PdfParser().parse(path)

    assert result.parse_status == "unsupported_ocr"
    assert result.metadata.get("ocr_pages") is None


def test_a_scan_without_ocr_is_reported_with_a_clear_reminder(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_settings, "get", lambda: _ocr_settings(provider=""))
    path = _build_scanned_pdf(tmp_path / "scan.pdf", ["合同编号：HT-2024-001"])

    result = PdfParser().parse(path)

    assert result.parse_status == "unsupported_ocr"
    assert "未配置 OCR" in (result.message or "")
    assert "设置" in (result.message or "")
    assert result.metadata["scanned"] is True
    assert result.metadata["ocr_configured"] is False


def test_a_partial_scan_without_ocr_is_mentioned_in_the_notices(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_settings, "get", lambda: _ocr_settings(provider=""))

    import fitz

    path = tmp_path / "mixed.pdf"
    document = fitz.open()
    text_page = document.new_page(width=595, height=842)
    text_page.insert_text((72, 120), "正常文本页面", fontsize=14, fontname="china-s")

    source = fitz.open()
    picture = source.new_page(width=595, height=842)
    picture.insert_text((72, 120), "扫描内容", fontsize=14, fontname="china-s")
    pixmap = picture.get_pixmap(matrix=fitz.Matrix(2, 2))
    source.close()
    scan_page = document.new_page(width=595, height=842)
    scan_page.insert_image(scan_page.rect, pixmap=pixmap)
    document.save(str(path))
    document.close()

    result = PdfParser().parse(path)

    assert result.parse_status == "partial"
    assert any("未配置 OCR" in warning for warning in result.parsed_pdf.warnings)


def test_the_page_limit_stops_the_recognition(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        app_settings, "get", lambda: _ocr_settings(max_pages=1)
    )
    monkeypatch.setattr(ocr_parser, "get_provider", lambda name: _FakeProvider())
    path = _build_scanned_pdf(tmp_path / "scan.pdf", ["one", "two"])

    result = PdfParser().parse(path)

    assert result.parse_status == "partial"
    assert result.metadata["ocr_pages"] == [1]
    assert "1 页" in (result.message or "")


def test_the_recognition_is_not_paid_for_twice(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_settings, "get", lambda: _ocr_settings())
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    calls = {"count": 0}

    class _Counting(_FakeProvider):
        def recognize(self, image, values):
            calls["count"] += 1
            return super().recognize(image, values)

    monkeypatch.setattr(ocr_parser, "get_provider", lambda name: _Counting())
    path = _build_scanned_pdf(tmp_path / "scan.pdf", ["scan"])

    first = PdfParser().parse(path)
    second = PdfParser().parse(path)

    assert first.parse_status == "success"
    assert second.parse_status == "success"
    assert calls["count"] == 1


def test_a_cache_of_another_channel_is_not_reused(tmp_path: Path, monkeypatch):
    """Switching the channel must re-recognise: the cache belongs to one."""
    from app.parsers import ocr_cache

    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    path = _build_scanned_pdf(tmp_path / "scan.pdf", ["scan"])
    page = {"confidence": 0.9, "lines": [{"text": "x", "bbox": [0, 0, 1, 1], "size": 10,
                                          "chars": [[0, 0, 1, 1, "x"]]}]}

    ocr_cache.store(path, "baidu", {1: page})

    assert ocr_cache.load(path, "baidu") == {1: page}
    assert ocr_cache.load(path, "tencent") == {}


def test_two_scanned_documents_are_compared_and_the_change_is_located(
    tmp_path: Path, monkeypatch
):
    from app.compare.pdf_compare import compare_pdfs
    from app.schemas import CompareConfig

    monkeypatch.setattr(app_settings, "get", lambda: _ocr_settings())
    texts = ["合同编号：HT-2024-001", "合同编号：HT-2024-002"]
    calls = {"count": 0}

    class _Provider(_FakeProvider):
        def recognize(self, image, values):
            text = texts[min(calls["count"], len(texts) - 1)]
            calls["count"] += 1
            return _FakeProvider(text).recognize(image, values)

    monkeypatch.setattr(ocr_parser, "get_provider", lambda name: _Provider())
    path_a = _build_scanned_pdf(tmp_path / "a.pdf", ["scan"])
    path_b = _build_scanned_pdf(tmp_path / "b.pdf", ["scan"])

    parsed_a = PdfParser().parse(path_a)
    parsed_b = PdfParser().parse(path_b)
    changes, _notices = compare_pdfs(parsed_a, parsed_b, CompareConfig())

    assert changes
    change = changes[0]
    assert change.primary_type == "modified"
    assert "001" in change.document_a.text
    assert "002" in change.document_b.text
    rects = change.document_a.location.extra.get("rects")
    assert rects and rects[0][0] > 0


def test_a_page_without_a_channel_reports_the_reason(monkeypatch):
    monkeypatch.setattr(ocr_parser, "get_provider", lambda name: None)
    monkeypatch.setattr(
        app_settings,
        "get",
        lambda: _ocr_settings(provider="custom"),
    )

    with pytest.raises(OcrNotConfigured):
        ocr_parser.ocr_page_lines(object(), 1, values={})
