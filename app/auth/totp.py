"""Small dependency-free TOTP implementation with encrypted secret storage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings


class TOTPError(RuntimeError):
    pass


def _key() -> bytes:
    secret = get_settings().security_token_secret.get_secret_value().encode()
    return hashlib.sha256(b"arima-mfa-secret-v1:" + secret).digest()


def generate_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def encrypt_secret(secret: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, secret.encode(), b"arima-mfa")
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_secret(value: str) -> str:
    try:
        packed = base64.urlsafe_b64decode(value.encode())
        return AESGCM(_key()).decrypt(packed[:12], packed[12:], b"arima-mfa").decode()
    except Exception as error:
        raise TOTPError("MFA secret is unavailable") from error


def current_step(now: int | None = None) -> int:
    return int((time.time() if now is None else now) // 30)


def code_for(secret: str, step: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify_code(secret: str, code: str, *, now: int | None = None, last_step: int | None = None) -> int | None:
    normalized = code.strip()
    if len(normalized) != 6 or not normalized.isdigit():
        return None
    step = current_step(now)
    for candidate in (step - 1, step, step + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_for(secret, candidate), normalized):
            return candidate
    return None
