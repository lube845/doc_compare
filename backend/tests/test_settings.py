"""Tests of the settings the user changes in the browser.

The API is tested against the throw-away database of the test run (see
tests/conftest.py); the settings of a deployment are never touched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import AppSetting, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.services import app_settings  # noqa: E402

client = TestClient(app)

BAIDU = {
    "provider": "baidu",
    "values": {
        "api_key": "the-api-key",
        "secret_key": "the-secret-key",
        "language_type": "CHN_ENG",
    },
    "dpi": 200,
    "max_pages": 100,
}


class _FakeSession:
    """Just enough of a session for the settings."""

    def __init__(self) -> None:
        self.rows: dict[str, AppSetting] = {}

    def get(self, model, key):
        return self.rows.get(key)

    def add(self, row) -> None:
        self.rows[row.key] = row

    def delete(self, row) -> None:
        self.rows.pop(row.key, None)

    def commit(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The connection test must never reach a provider in the suite."""
    monkeypatch.setattr(
        "app.parsers.ocr_baidu.BaiduProvider.access_token",
        lambda self, values, force_refresh=False: "a-token",
    )
    # Only the network layer is faked: the completeness check still runs.
    import httpx2 as httpx

    monkeypatch.setattr(
        "app.parsers.ocr_tencent.TencentProvider._send",
        lambda self, payload, values: httpx.Response(
            200, json={"Response": {"RequestId": "fake"}}
        ),
    )
    monkeypatch.setattr(
        "app.parsers.ocr_custom.CustomProvider._send",
        lambda self, image, values: {},
    )


@pytest.fixture()
def fake_session():
    session = _FakeSession()
    app.dependency_overrides[get_session] = lambda: session
    app_settings.reset()
    try:
        yield session
    finally:
        app.dependency_overrides.pop(get_session, None)
        app_settings.reset()


def _test(body: dict | None = None) -> dict:
    response = client.post("/api/settings/ocr/test", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _save(**patch) -> dict:
    """Test the configuration first, then save it - the normal flow."""
    body = {**BAIDU, **patch}
    _test(body)
    response = client.put("/api/settings/ocr", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# reading and writing
# --------------------------------------------------------------------------


def test_the_channels_are_offered_to_the_interface(fake_session):
    body = client.get("/api/settings/ocr").json()

    names = [item["name"] for item in body["providers"]]
    assert names[0] == "baidu"
    assert "tencent" in names and "custom" in names
    for item in body["providers"]:
        assert item["display_name"]
        assert item["fields"]


def test_the_settings_come_from_the_deployment_by_default(fake_session):
    body = client.get("/api/settings/ocr").json()

    assert body["source"] == "environment"
    assert body["enabled"] is False
    assert body["verified"] is False


def test_the_interface_opens_on_the_baidu_channel(fake_session):
    """Nothing configured yet: Baidu is selected, only the keys are missing."""
    body = client.get("/api/settings/ocr").json()

    assert body["provider"] == "baidu"
    assert body["values"]["language_type"] == "CHN_ENG"
    assert body["values"]["api_key"] == ""


def test_a_configuration_cannot_be_saved_before_the_connection_test(fake_session):
    response = client.put("/api/settings/ocr", json=BAIDU)

    assert response.status_code == 400
    assert "测试连接" in response.json()["detail"]
    assert fake_session.rows.get("ocr") is None


def test_a_configuration_can_be_saved_after_the_connection_test(fake_session):
    body = _save()

    assert body["provider"] == "baidu"
    # the credentials are never sent back to the browser
    assert body["values"]["api_key"] == ""
    assert body["values"]["secret_key"] == ""
    assert body["secret_keys_set"]["api_key"] is True
    assert body["secret_keys_set"]["secret_key"] is True
    assert body["enabled"] is True
    assert body["verified"] is True
    assert body["source"] == "database"
    # the stored value is still there
    assert app_settings.get().values["api_key"] == "the-api-key"


def test_a_changed_credential_needs_a_new_test(fake_session):
    _save()
    changed = {**BAIDU, "values": {**BAIDU["values"], "api_key": "another-key"}}

    response = client.put("/api/settings/ocr", json=changed)

    assert response.status_code == 400
    assert "测试连接" in response.json()["detail"]


def test_a_failed_test_does_not_verify_the_configuration(fake_session, monkeypatch):
    from app.parsers.ocr_client import OcrRequestError

    monkeypatch.setattr(
        "app.parsers.ocr_baidu.BaiduProvider.access_token",
        lambda self, values, force_refresh=False: (_ for _ in ()).throw(
            OcrRequestError("百度拒绝了 API Key")
        ),
    )

    result = _test(BAIDU)

    assert result["ok"] is False
    response = client.put("/api/settings/ocr", json=BAIDU)
    assert response.status_code == 400


def test_switching_the_channel_off_needs_no_test(fake_session):
    _save()
    response = client.put(
        "/api/settings/ocr",
        json={"provider": "", "values": {}, "dpi": 200, "max_pages": 100},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == ""
    assert body["enabled"] is False


def test_the_saved_settings_survive_a_restart(fake_session):
    _save()
    app_settings.reset()
    app_settings.load(fake_session)

    body = client.get("/api/settings/ocr").json()

    assert body["values"]["api_key"] == ""
    assert body["enabled"] is True
    assert body["verified"] is True
    assert app_settings.get().values["api_key"] == "the-api-key"


def test_an_empty_secret_keeps_the_stored_one(fake_session):
    _save()
    body = _save(values={"api_key": "another", "secret_key": ""})

    assert body["values"]["api_key"] == ""
    assert body["secret_keys_set"]["api_key"] is True
    assert body["secret_keys_set"]["secret_key"] is True
    assert app_settings.get().values["secret_key"] == "the-secret-key"
    assert app_settings.get().values["api_key"] == "another"


def test_clearing_a_required_secret_makes_the_configuration_unusable(fake_session):
    """A channel cannot be saved without its secret, however it was cleared."""
    _save()
    body = {
        **BAIDU,
        "values": {"api_key": "the-api-key", "secret_key": ""},
        "clear_secrets": ["secret_key"],
    }

    result = _test(body)

    assert result["ok"] is False
    assert client.put("/api/settings/ocr", json=body).status_code == 400


def test_another_channel_does_not_reuse_the_secret(fake_session):
    _save()
    # An empty secret on another channel is not filled with Baidu's one: the
    # configuration is simply incomplete.
    incomplete = {
        "provider": "tencent",
        "values": {"secret_id": "AKID", "secret_key": "", "region": "ap-guangzhou"},
        "dpi": 200,
        "max_pages": 100,
    }
    assert _test(incomplete)["ok"] is False
    assert app_settings.get().values["secret_key"] == "the-secret-key"

    # With its own secret the channel is saved with that value only.
    body = {
        "provider": "tencent",
        "values": {
            "secret_id": "AKID",
            "secret_key": "TENCENT-SK",
            "region": "ap-guangzhou",
        },
        "dpi": 200,
        "max_pages": 100,
    }
    _test(body)
    data = client.put("/api/settings/ocr", json=body).json()

    assert data["provider"] == "tencent"
    assert data["secret_keys_set"]["secret_key"] is True
    assert app_settings.get().values["secret_key"] == "TENCENT-SK"


def test_an_unknown_channel_is_refused(fake_session):
    response = client.post(
        "/api/settings/ocr/test",
        json={"provider": "nope", "values": {}, "dpi": 200, "max_pages": 100},
    )

    assert response.status_code == 400


def test_a_field_of_another_channel_is_refused(fake_session):
    response = client.post(
        "/api/settings/ocr/test",
        json={
            "provider": "baidu",
            "values": {"secret_id": "AKID"},
            "dpi": 200,
            "max_pages": 100,
        },
    )

    assert response.status_code == 400


def test_a_missing_required_field_is_refused_before_the_save(fake_session):
    response = client.put(
        "/api/settings/ocr",
        json={
            "provider": "baidu",
            "values": {"api_key": "k"},
            "dpi": 200,
            "max_pages": 100,
        },
    )

    assert response.status_code == 400
    assert "缺少配置" in response.json()["detail"]


def test_the_page_limit_is_bounded(fake_session):
    response = client.post("/api/settings/ocr/test", json={**BAIDU, "max_pages": 0})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# the connection test
# --------------------------------------------------------------------------


def test_the_test_reports_a_missing_configuration(fake_session):
    body = _test()

    assert body["ok"] is False


def test_the_test_reports_the_error_of_the_channel(fake_session, monkeypatch):
    from app.parsers.ocr_client import OcrRequestError

    monkeypatch.setattr(
        "app.parsers.ocr_baidu.BaiduProvider.access_token",
        lambda self, values, force_refresh=False: (_ for _ in ()).throw(
            OcrRequestError("百度拒绝了 API Key")
        ),
    )

    body = _test(BAIDU)

    assert body["ok"] is False
    assert "百度拒绝了 API Key" in body["message"]


def test_the_test_succeeds_with_valid_credentials(fake_session):
    body = _test(BAIDU)

    assert body["ok"] is True
    assert "百度智能云" in body["message"]
