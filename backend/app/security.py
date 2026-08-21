"""Encryption and redaction helpers for provider credentials."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    """Return a stable cipher derived from an explicit deployment secret when set.

    RADIO_CATCH_ENCRYPTION_KEY may be a Fernet key.  For simpler deployments a
    RADIO_CATCH_SECRET_KEY is accepted and deterministically expanded. The final
    fallback only supports local development and should be replaced in production.
    """
    raw_key = os.getenv("RADIO_CATCH_ENCRYPTION_KEY")
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("RADIO_CATCH_ENCRYPTION_KEY is not a valid Fernet key") from exc
    seed = os.getenv("RADIO_CATCH_SECRET_KEY", "radio-catch-local-development-secret")
    derived = base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt_api_key(api_key: str) -> str:
    if not api_key.strip():
        raise ValueError("API key must not be empty")
    return _fernet().encrypt(api_key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt the saved API key; check the encryption secret") from exc


def mask_api_key(api_key: str) -> str:
    """Never return a full credential, including for short test credentials."""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * max(4, len(api_key) - 8)}{api_key[-4:]}"
