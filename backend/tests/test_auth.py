"""Auth tests: hashing, tokens, login/me endpoints, role separation, seeding."""

import asyncio
import os
import uuid
from datetime import timedelta

import psycopg
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_interviewer
from app.api.errors import register_error_handlers
from app.api.routers.auth import router as auth_router
from app.core.config import Settings
from app.core.errors import AuthError
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.db.models import User
from app.db.session import get_session
from app.services.users import ensure_seed_interviewer

DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://hc_user:change_me@localhost:5432/hiringcompass")


# Hashes verify only the right password and are salted.
def test_password_hashing():
    first, second = hash_password("s3cret"), hash_password("s3cret")
    assert first != second and verify_password("s3cret", first) and not verify_password("wrong", first)
    assert not verify_password("s3cret", "garbage") and not verify_password("s3cret", "md5$1$2$3$4$5")


# Tokens carry the claims, expire, and reject tampering.
def test_tokens():
    uid = uuid.uuid4()
    claims = decode_access_token(create_access_token(uid, "interviewer"))
    assert claims["sub"] == str(uid) and claims["role"] == "interviewer"
    with pytest.raises(AuthError):
        decode_access_token(create_access_token(uid, "interviewer", expires_delta=timedelta(seconds=-5)))
    token = create_access_token(uid, "interviewer")
    with pytest.raises(AuthError):
        decode_access_token(token[:-3] + ("abc" if not token.endswith("abc") else "xyz"))


# Login, session restore, bad credentials, missing token and role separation over HTTP; plus seeding.
def test_auth_flow_over_http():
    try:
        psycopg.connect(DB_URL.replace("+psycopg", ""), connect_timeout=3).close()
    except Exception:
        pytest.skip("PostgreSQL not reachable")
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    interviewer_email, candidate_email = f"{uuid.uuid4().hex}@test.local", f"{uuid.uuid4().hex}@test.local"

    # Create one interviewer and one candidate account.
    async def make_users():
        async with factory() as s:
            s.add_all([User(email=interviewer_email, password_hash=hash_password("pw-interviewer"), role="interviewer"),
                       User(email=candidate_email, password_hash=hash_password("pw-candidate"), role="candidate")])
            await s.commit()

    # Remove the test accounts again.
    async def drop_users():
        async with factory() as s:
            await s.execute(delete(User).where(User.email.in_([interviewer_email, candidate_email])))
            await s.commit()

    # Override the DB session dependency with the test engine.
    async def test_session():
        async with factory() as s:
            yield s

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_router)
    app.dependency_overrides[get_session] = test_session

    # Interviewer-only route used to prove role separation.
    @app.get("/secret")
    async def secret(_=Depends(get_current_interviewer)):
        return {"ok": True}

    asyncio.run(make_users())
    try:
        client = TestClient(app)
        login = client.post("/auth/login", json={"email": interviewer_email.upper(), "password": "pw-interviewer"})
        assert login.status_code == 200 and login.json()["user"]["role"] == "interviewer" and "password" not in login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/auth/me", headers=headers).json()["email"] == interviewer_email
        assert client.get("/secret", headers=headers).status_code == 200
        bad = client.post("/auth/login", json={"email": interviewer_email, "password": "nope"})
        unknown = client.post("/auth/login", json={"email": "nobody@test.local", "password": "nope"})
        assert bad.status_code == unknown.status_code == 401 and bad.json()["detail"] == unknown.json()["detail"]
        assert client.get("/auth/me").status_code == 401 and client.get("/secret", headers={"Authorization": "Bearer junk"}).status_code == 401
        cand = client.post("/auth/login", json={"email": candidate_email, "password": "pw-candidate"}).json()["access_token"]
        assert client.get("/secret", headers={"Authorization": f"Bearer {cand}"}).status_code == 403

        # Seeding twice yields one account.
        async def seed_twice():
            settings = Settings(seed_interviewer_email=f"seed-{uuid.uuid4().hex}@test.local", seed_interviewer_password="seed-pw")
            async with factory() as s:
                a, b = await ensure_seed_interviewer(s, settings), await ensure_seed_interviewer(s, settings)
                assert a.id == b.id and verify_password("seed-pw", a.password_hash)
                await s.delete(a)
                await s.commit()

        asyncio.run(seed_twice())
    finally:
        asyncio.run(drop_users())
        asyncio.run(engine.dispose())