"""Password hashing (scrypt, stdlib) and JWT access tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import Settings, get_settings
from app.core.errors import AuthError

_N, _R, _P = 2**14, 8, 1


# Hash a password with a random salt; the result embeds the parameters for later verification.
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "$".join(["scrypt", str(_N), str(_R), str(_P), base64.b64encode(salt).decode(), base64.b64encode(digest).decode()])


# Check a password against a stored hash in constant time; malformed hashes never verify.
def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


# Issue a signed access token for a user id and role.
def create_access_token(user_id: uuid.UUID, role: str, settings: Settings | None = None, expires_delta: timedelta | None = None) -> str:
    settings = settings or get_settings()
    expires = expires_delta if expires_delta is not None else timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "role": role, "exp": datetime.now(timezone.utc) + expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


# Verify signature and expiry and return the claims; any problem is a generic AuthError.
def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise AuthError("Your session is invalid or has expired. Please sign in again.") from None