from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from healthclaw.core.config import Settings


class CryptoError(Exception):
    pass


def _fernet(settings: Settings) -> Fernet:
    key = settings.fernet_key
    if not key:
        raise CryptoError("FERNET_KEY is not configured")
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"Invalid FERNET_KEY: {exc}") from exc


def encrypt_secret(plaintext: str, settings: Settings) -> str:
    return _fernet(settings).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str, settings: Settings) -> str:
    try:
        return _fernet(settings).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("Failed to decrypt secret") from exc


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")


def generate_url_token(num_bytes: int = 32) -> str:
    return secrets.token_urlsafe(num_bytes)


def hash_token(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
