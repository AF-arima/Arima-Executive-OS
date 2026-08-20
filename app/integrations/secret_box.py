"""Server-side envelope encryption for provider credentials.

The deployment supplies the key; the database stores only AES-GCM ciphertext.
This module intentionally fails closed when the key is absent or malformed.
"""
from __future__ import annotations

import base64
import binascii
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings


class CredentialEncryptionError(RuntimeError):
    pass


def _key() -> bytes:
    raw = get_settings().integration_encryption_key
    value = raw.get_secret_value().strip() if raw is not None else ""
    if not value:
        raise CredentialEncryptionError("integration encryption key is unavailable")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        decoded = b""
    if len(decoded) not in {16, 24, 32}:
        try:
            decoded = bytes.fromhex(value)
        except ValueError:
            decoded = b""
    if len(decoded) not in {16, 24, 32}:
        raise CredentialEncryptionError("integration encryption key must be base64 or hex AES key")
    return decoded


def encrypt_json(value: dict[str, object], *, purpose: str) -> str:
    nonce = os.urandom(12)
    plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext, purpose.encode())
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_json(value: str, *, purpose: str) -> dict[str, object]:
    try:
        packed = base64.urlsafe_b64decode(value.encode())
        plaintext = AESGCM(_key()).decrypt(packed[:12], packed[12:], purpose.encode())
        decoded = json.loads(plaintext)
    except Exception as error:
        raise CredentialEncryptionError("encrypted credential could not be decrypted") from error
    if not isinstance(decoded, dict):
        raise CredentialEncryptionError("encrypted credential payload is invalid")
    return decoded
