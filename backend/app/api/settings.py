"""The settings the user changes in the browser.

The OCR channel is configured here instead of in the environment, so that
the deployment does not have to be rebuilt when the credentials change or
when the user switches from one provider to another.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..parsers import ocr_client
from ..parsers.ocr_client import BaseOcrProvider, OcrError, OcrProviderField
from ..services import app_settings
from ..services.app_settings import OcrSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class FieldView(BaseModel):
    """One field of a channel, as the interface renders it."""

    key: str
    label: str
    kind: str = "text"
    required: bool = True
    secret: bool = False
    default: str = ""
    placeholder: str = ""
    options: list[str] = Field(default_factory=list)
    hint: str = ""


class ProviderView(BaseModel):
    """One selectable channel."""

    name: str
    display_name: str
    description: str = ""
    fields: list[FieldView] = Field(default_factory=list)


class OcrSettingsView(BaseModel):
    """What the interface shows.

    A secret value is never sent back: the interface only learns whether one
    is stored.
    """

    provider: str = ""
    enabled: bool = False
    dpi: int = 200
    max_pages: int = 100
    #: ``environment`` - the deployment default, ``database`` - saved by the user
    source: str = "environment"
    providers: list[ProviderView] = Field(default_factory=list)
    #: the values of the selected channel, secrets omitted
    values: dict[str, str] = Field(default_factory=dict)
    #: the secret fields that have a value
    secret_keys_set: dict[str, bool] = Field(default_factory=dict)
    #: the saved configuration passed the connection test
    verified: bool = False


class OcrSettingsUpdate(BaseModel):
    """What the user sends."""

    provider: str = ""
    values: dict[str, str] = Field(default_factory=dict)
    #: the secret fields the user wants to delete
    clear_secrets: list[str] = Field(default_factory=list)
    dpi: int = Field(default=200, ge=50, le=600)
    max_pages: int = Field(default=100, ge=1, le=5000)


class OcrTestResult(BaseModel):
    ok: bool
    message: str


def _field_view(field: OcrProviderField) -> FieldView:
    return FieldView(
        key=field.key,
        label=field.label,
        kind=field.kind,
        required=field.required,
        secret=field.secret,
        default=field.default,
        placeholder=field.placeholder,
        options=list(field.options),
        hint=field.hint,
    )


def _provider_view(provider: BaseOcrProvider) -> ProviderView:
    return ProviderView(
        name=provider.name,
        display_name=provider.display_name,
        description=provider.description,
        fields=[_field_view(field) for field in provider.fields],
    )


def _sanitised_values(provider: BaseOcrProvider, current: OcrSettings) -> dict[str, str]:
    """The values of a channel, with the secrets taken out."""
    result: dict[str, str] = {}
    for field in provider.fields:
        value = current.value(field.key, field.default)
        result[field.key] = "" if field.secret else value
    return result


def _secrets_set(provider: BaseOcrProvider, current: OcrSettings) -> dict[str, bool]:
    return {
        field.key: bool(current.value(field.key))
        for field in provider.fields
        if field.secret
    }


def _view(current: OcrSettings, stored: bool, verified: bool = False) -> OcrSettingsView:
    # Nothing configured yet: the interface still opens on the default
    # channel, so the user only has to fill in the credentials.  A channel
    # the user switched off on purpose stays empty.
    if current.provider:
        selected = current.provider
    elif stored:
        selected = ""
    else:
        selected = app_settings.DEFAULT_PROVIDER
    provider = ocr_client.get_provider(selected)
    # The values are only mixed when the channel has not changed.
    source_values = current if current.provider == selected else OcrSettings(
        provider=selected, dpi=current.dpi, max_pages=current.max_pages
    )
    return OcrSettingsView(
        provider=selected,
        enabled=current.enabled and provider is not None,
        dpi=current.dpi,
        max_pages=current.max_pages,
        source="database" if stored else "environment",
        providers=[_provider_view(item) for item in ocr_client.all_providers()],
        values=_sanitised_values(provider, source_values) if provider else {},
        secret_keys_set=_secrets_set(provider, source_values) if provider else {},
        verified=verified,
    )


@router.get("/ocr", response_model=OcrSettingsView)
def read_ocr_settings(session: Session = Depends(get_session)) -> OcrSettingsView:
    """The OCR settings in use, plus the channels the interface can show."""
    current = app_settings.get()
    verified = bool(
        current.provider
        and app_settings.verified_fingerprint(session)
        == _fingerprint(current.provider, current.values)
    )
    return _view(current, app_settings.stored_in_database(session), verified)


def _resolve(
    payload: OcrSettingsUpdate, current: OcrSettings
) -> tuple[str, dict[str, str]]:
    """The channel and the effective values of a submitted configuration.

    An empty secret field keeps the value that is already stored, so that
    the interface never has to show the secret to keep it.
    """
    provider_name = payload.provider.strip()
    provider = ocr_client.get_provider(provider_name) if provider_name else None
    if provider_name and provider is None:
        raise HTTPException(status_code=400, detail=f"不支持的 OCR 渠道：{provider_name}")

    # The channel can change: a secret of another channel is not reused.
    previous = current.values if current.provider == provider_name else {}
    values: dict[str, str] = {}
    fields = provider.fields if provider else ()
    known = {field.key for field in fields}
    for key, raw in (payload.values or {}).items():
        if key not in known:
            raise HTTPException(status_code=400, detail=f"渠道没有这个配置项：{key}")
        values[key] = str(raw or "").strip()

    if provider is not None:
        for field in fields:
            if field.key in values:
                continue
            values[field.key] = provider.value(previous, field.key)
        for field in fields:
            if not field.secret:
                continue
            if field.key in payload.clear_secrets:
                values[field.key] = ""
            elif not values.get(field.key):
                values[field.key] = provider.value(previous, field.key)
    return provider_name, values


def _fingerprint(provider_name: str, values: dict[str, str]) -> str:
    """A digest of a configuration.

    The secrets are part of the digest but never leave this function, so a
    verified configuration can be recognised without storing its keys.
    """
    canonical = json.dumps(
        {"provider": provider_name, "values": values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.put("/ocr", response_model=OcrSettingsView)
def update_ocr_settings(
    payload: OcrSettingsUpdate, session: Session = Depends(get_session)
) -> OcrSettingsView:
    """Save the OCR settings.

    A configuration has to be verified first: the same channel and the same
    values must have passed ``POST /ocr/test``.  The check lives here so a
    direct API call cannot skip it.
    """
    current = app_settings.get()
    provider_name, values = _resolve(payload, current)
    provider = ocr_client.get_provider(provider_name) if provider_name else None

    if provider is not None:
        if not provider.credentials_complete(values):
            missing = [
                field.label
                for field in provider.fields
                if field.required and not provider.value(values, field.key)
            ]
            raise HTTPException(
                status_code=400, detail=f"缺少配置：{'、'.join(missing)}"
            )
        if app_settings.verified_fingerprint(session) != _fingerprint(
            provider_name, values
        ):
            raise HTTPException(
                status_code=400,
                detail="尚未通过连接测试，请先点击「测试连接」，通过后再保存",
            )

    saved = app_settings.save(
        session,
        OcrSettings(
            provider=provider_name,
            values=values,
            dpi=payload.dpi,
            max_pages=payload.max_pages,
        ),
        _secret_keys(provider),
    )
    return _view(saved, True, provider is not None)


def _secret_keys(provider: BaseOcrProvider | None) -> set[str]:
    """The fields of the channel that hold a credential."""
    if provider is None:
        return set()
    return {field.key for field in provider.fields if field.secret}


@router.post("/ocr/test", response_model=OcrTestResult)
def test_ocr_settings(
    payload: OcrSettingsUpdate | None = None,
    session: Session = Depends(get_session),
) -> OcrTestResult:
    """Check a configuration before it is saved.

    The body is the configuration the user is editing (the same shape as
    ``PUT``); without a body the saved configuration is checked.  It verifies
    the credentials without recognising an image (the channels implement it
    that way), so it does not use any quota.

    A successful test records the digest of the configuration, which is what
    ``PUT`` accepts.
    """
    current = app_settings.get()
    if payload is None:
        provider_name = current.provider
        values = current.values
    else:
        provider_name, values = _resolve(payload, current)

    provider = ocr_client.get_provider(provider_name) if provider_name else None
    if provider is None:
        return OcrTestResult(ok=False, message="OCR 未启用，请先选择渠道并填写凭证")

    try:
        provider.check(values)
    except OcrError as exc:
        app_settings.clear_verification(session)
        return OcrTestResult(ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - never leak a raw exception
        app_settings.clear_verification(session)
        return OcrTestResult(ok=False, message=f"测试失败（{exc.__class__.__name__}）")

    app_settings.mark_verified(session, _fingerprint(provider_name, values))
    return OcrTestResult(
        ok=True, message=f"「{provider.display_name}」的配置可用，已成功连接"
    )
