"""Encryption of the secrets that are stored in the database.

The credentials of the OCR channel live in the database.  They are
encrypted with a key that lives next to the data and nowhere else: the key
file is created once with owner-only permissions.  A dump of the database
therefore does not contain the credentials - the key has to be stolen as
well.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

#: the prefix of an encrypted value
PREFIX = "enc:v1:"

_LOCK = threading.Lock()
_KEY: bytes | None = None


class SecretBoxError(Exception):
    """The value could not be encrypted or decrypted."""


def key_path() -> Path:
    """The file that holds the key of this installation."""
    return settings.project_dir / "secret.key"


def _load_key() -> bytes:
    """The key of this installation - it is created when it is missing."""
    global _KEY
    if _KEY is not None:
        return _KEY
    with _LOCK:
        if _KEY is not None:
            return _KEY
        path = key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # O_EXCL: two processes that start at the same time do not
            # overwrite each other's key.
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(handle, "wb") as stream:
                stream.write(Fernet.generate_key())
        _KEY = path.read_bytes().strip()
        return _KEY


def is_encrypted(value: str | None) -> bool:
    """True when the value was encrypted by this module."""
    return bool(value) and value.startswith(PREFIX)


def encrypt(value: str) -> str:
    """Encrypt a value; an empty value stays empty."""
    if not value:
        return value
    token = Fernet(_load_key()).encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt a value; a value that is not encrypted stays as it is."""
    if not is_encrypted(value):
        return value or ""
    token = value[len(PREFIX) :]  # type: ignore[index]
    try:
        return Fernet(_load_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretBoxError(
            "无法解密已保存的凭证：密钥文件与数据库不匹配"
        ) from exc
