"""The settings the user changes in the browser.

The OCR channel and its credentials are not part of the code any more: the
user picks a channel in the interface and enters its credentials there, and
they are stored in the database.  The environment variables stay the default
of a fresh installation - as long as the user never saves anything, the
deployment behaves exactly as before.

The values are cached in the process because they are read once per scanned
page; the cache is refreshed when the user saves them.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..db import AppSetting, _now
from . import secret_box

logger = logging.getLogger("doc_compare.app_settings")

#: the key of the OCR settings in the ``app_settings`` table
OCR_KEY = "ocr"
#: the key of the digest of the configuration that passed the connection test
VERIFIED_KEY = "ocr_verified"
#: the channel used when nothing was configured
DEFAULT_PROVIDER = "baidu"


@dataclass
class OcrSettings:
    """How the OCR is reached."""

    #: the name of the channel, empty when the OCR is switched off
    provider: str = ""
    #: the values the channel needs (credentials, endpoint, mapping, ...)
    values: dict[str, str] = field(default_factory=dict)
    #: the resolution the scanned pages are rendered with
    dpi: int = 200
    #: how many pages of one document are recognised at most
    max_pages: int = 100

    @property
    def enabled(self) -> bool:
        """True when a scan may be sent to a provider."""
        return bool(self.provider)

    def value(self, key: str, default: str = "") -> str:
        return str(self.values.get(key) or default)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "values": dict(self.values),
            "dpi": self.dpi,
            "max_pages": self.max_pages,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "OcrSettings":
        values = payload.get("values")
        if not isinstance(values, dict):
            values = {}
        try:
            dpi = int(payload.get("dpi", 200))
        except (TypeError, ValueError):
            dpi = 200
        try:
            max_pages = int(payload.get("max_pages", 100))
        except (TypeError, ValueError):
            max_pages = 100
        return cls(
            provider=str(payload.get("provider") or ""),
            values={str(key): str(value) for key, value in values.items()},
            dpi=dpi,
            max_pages=max_pages,
        )


_LOCK = threading.Lock()
_CACHE: OcrSettings | None = None


def from_environment() -> OcrSettings:
    """The settings of the deployment (the default of the interface)."""
    provider = settings.ocr_provider
    values: dict[str, str] = {}
    if provider == "baidu":
        values = {
            "api_key": settings.baidu_ocr_api_key,
            "secret_key": settings.baidu_ocr_secret_key,
            "language_type": settings.baidu_ocr_language_type,
        }
        if not (values["api_key"] and values["secret_key"]):
            provider = ""
    return OcrSettings(
        provider=provider,
        values=values,
        dpi=settings.baidu_ocr_dpi,
        max_pages=settings.baidu_ocr_max_pages,
    )


def load(session: Session) -> OcrSettings:
    """Read the settings from the database and cache them.

    The environment is the fallback: a database that does not know the OCR
    settings yet (a fresh installation) behaves like the deployment says.
    """
    global _CACHE
    values = from_environment()
    row = session.get(AppSetting, OCR_KEY)
    if row is not None and isinstance(row.value, dict):
        values = OcrSettings.from_dict(_decrypt_values(row.value))
    with _LOCK:
        _CACHE = values
    return values


def get() -> OcrSettings:
    """The settings in use."""
    if _CACHE is None:
        return from_environment()
    return _CACHE


def save(
    session: Session,
    values: OcrSettings,
    secret_keys: set[str] | None = None,
) -> OcrSettings:
    """Store the settings and make them the ones in use.

    The values whose key is in ``secret_keys`` are encrypted before they are
    written; they are decrypted again when they are read.
    """
    global _CACHE
    row = session.get(AppSetting, OCR_KEY)
    payload = values.to_dict()
    payload["values"] = _encrypt_values(payload.get("values") or {}, secret_keys)
    if row is None:
        session.add(AppSetting(key=OCR_KEY, value=payload))
    else:
        row.value = payload
        row.updated_at = _now()
    session.commit()
    with _LOCK:
        _CACHE = values
    return values


def _encrypt_values(values: dict, secret_keys: set[str] | None) -> dict:
    """Encrypt the values that hold a credential."""
    if not secret_keys:
        return dict(values)
    result: dict[str, str] = {}
    for key, value in values.items():
        text = str(value)
        if key in secret_keys and text:
            result[key] = secret_box.encrypt(text)
        else:
            result[key] = text
    return result


def _decrypt_values(payload: dict) -> dict:
    """Decrypt the values that were encrypted when they were stored."""
    values = payload.get("values")
    if not isinstance(values, dict):
        return payload
    result: dict[str, str] = {}
    for key, value in values.items():
        text = str(value)
        if secret_box.is_encrypted(text):
            try:
                text = secret_box.decrypt(text)
            except secret_box.SecretBoxError:
                logger.error("无法解密已保存的凭证：%s", key)
                text = ""
        result[key] = text
    payload = dict(payload)
    payload["values"] = result
    return payload


def stored_in_database(session: Session) -> bool:
    """True when the user saved the settings in the interface."""
    return session.get(AppSetting, OCR_KEY) is not None


def verified_fingerprint(session: Session) -> str:
    """The digest of the configuration that last passed the connection test."""
    row = session.get(AppSetting, VERIFIED_KEY)
    if row is None or not isinstance(row.value, dict):
        return ""
    return str(row.value.get("fingerprint") or "")


def mark_verified(session: Session, fingerprint: str) -> None:
    """Remember the configuration that passed the connection test."""
    row = session.get(AppSetting, VERIFIED_KEY)
    payload = {"fingerprint": fingerprint}
    if row is None:
        session.add(AppSetting(key=VERIFIED_KEY, value=payload))
    else:
        row.value = payload
        row.updated_at = _now()
    session.commit()


def clear_verification(session: Session) -> None:
    """Forget the verified configuration (a test failed)."""
    row = session.get(AppSetting, VERIFIED_KEY)
    if row is not None:
        session.delete(row)
        session.commit()


def reset() -> None:
    """Drop the cache (used by the tests)."""
    global _CACHE
    with _LOCK:
        _CACHE = None
