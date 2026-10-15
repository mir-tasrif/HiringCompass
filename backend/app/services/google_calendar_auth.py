"""Google Calendar OAuth authorization, encrypted tokens, and refresh lifecycle."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import GoogleCalendarConnection

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

def _client_config(settings: Settings) -> dict[str, str]:
    path: Path = settings.google_calendar_client_secrets_file
    if not path.is_file():
        raise RuntimeError(f"Google OAuth client JSON was not found at {path}.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        web = payload["web"]
        return {key: str(web[key]) for key in ("client_id", "client_secret", "auth_uri", "token_uri")}
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("The Google OAuth JSON must be a Web application client file.") from None


def _fernet(settings: Settings) -> Fernet:
    if not settings.google_calendar_token_encryption_key:
        raise RuntimeError("Set GOOGLE_CALENDAR_TOKEN_ENCRYPTION_KEY before connecting Google Calendar.")
    try:
        return Fernet(settings.google_calendar_token_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise RuntimeError("GOOGLE_CALENDAR_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_oauth_state(owner_id: str, settings: Settings) -> str:
    body = _b64(json.dumps({"owner": owner_id, "expires": int(time.time()) + 600,
                            "nonce": secrets.token_urlsafe(18)}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(settings.jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_oauth_state(state: str, settings: Settings) -> str:
    try:
        body, signature = state.split(".", 1)
        expected = _b64(hmac.new(settings.jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        values = json.loads(_unb64(body))
        if int(values["expires"]) < time.time():
            raise ValueError
        return str(values["owner"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("Google authorization expired. Start the connection again.") from None


def authorization_url(owner_id: str, settings: Settings) -> str:
    client = _client_config(settings)
    query = urlencode({"client_id": client["client_id"], "redirect_uri": settings.google_calendar_redirect_uri,
        "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": make_oauth_state(owner_id, settings)})
    return f"{client['auth_uri']}?{query}"


async def complete_oauth(session: AsyncSession, code: str, state: str, settings: Settings) -> None:
    owner_id = uuid.UUID(verify_oauth_state(state, settings))
    client = _client_config(settings)
    async with httpx.AsyncClient(timeout=25) as http:
        response = await http.post(client["token_uri"], data={"code": code, "client_id": client["client_id"],
            "client_secret": client["client_secret"], "redirect_uri": settings.google_calendar_redirect_uri,
            "grant_type": "authorization_code"})
        response.raise_for_status()
        token = response.json()
        token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
        info_response = await http.get(USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {token['access_token']}"})
        if info_response.is_success:
            account_email = str(info_response.json().get("email") or "") or None
        else:
            account_email = None
    if not token.get("refresh_token"):
        old = await session.scalar(select(GoogleCalendarConnection).where(
            GoogleCalendarConnection.owner_id == owner_id).with_for_update())
        if old:
            prior = decrypt_token(old.encrypted_token, settings)
            token["refresh_token"] = prior.get("refresh_token")
    if not token.get("refresh_token"):
        raise RuntimeError("Google did not return a refresh token. Disconnect the app in Google Account permissions and reconnect.")
    encrypted = _fernet(settings).encrypt(json.dumps(token).encode()).decode("ascii")
    record = await session.scalar(select(GoogleCalendarConnection).where(
        GoogleCalendarConnection.owner_id == owner_id).with_for_update())
    if record is None:
        record = GoogleCalendarConnection(owner_id=owner_id, encrypted_token=encrypted,
                                          account_email=account_email, scopes=SCOPES)
        session.add(record)
    else:
        record.encrypted_token, record.account_email, record.scopes = encrypted, account_email, SCOPES
        record.connected_at = datetime.now(timezone.utc)
    await session.commit()


def decrypt_token(value: str, settings: Settings) -> dict[str, Any]:
    try:
        return json.loads(_fernet(settings).decrypt(value.encode()).decode())
    except (InvalidToken, ValueError, json.JSONDecodeError):
        raise RuntimeError("The saved Google Calendar authorization could not be decrypted. Check the encryption key.") from None


async def _access_token(session: AsyncSession, owner_id: Any, settings: Settings) -> tuple[str, GoogleCalendarConnection]:
    connection = await session.scalar(select(GoogleCalendarConnection).where(
        GoogleCalendarConnection.owner_id == owner_id).with_for_update())
    if connection is None:
        raise RuntimeError("Connect Google Calendar from the Interviews page first.")
    token = decrypt_token(connection.encrypted_token, settings)
    expiry = token.get("expires_at", 0)
    if float(expiry or 0) <= time.time() + 60:
        client = _client_config(settings)
        async with httpx.AsyncClient(timeout=25) as http:
            response = await http.post(client["token_uri"], data={"client_id": client["client_id"],
                "client_secret": client["client_secret"], "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token"})
            response.raise_for_status()
            refreshed = response.json()
        token.update(refreshed)
        token["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600))
        connection.encrypted_token = _fernet(settings).encrypt(json.dumps(token).encode()).decode("ascii")
        await session.commit()
    return str(token["access_token"]), connection


