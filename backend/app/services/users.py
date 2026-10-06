"""User accounts: authentication and the seeded interviewer."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.db.models import User

logger = get_logger("services.users")

# Verified when the email is unknown, so response time does not reveal which emails exist.
_DUMMY_HASH = hash_password("not-a-real-password")


# Return the active user for valid credentials; every failure raises the same generic error.
async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not (user and valid and user.is_active):
        raise AuthError("Incorrect email or password.")
    return user


# Create the demo interviewer from environment settings on first start (idempotent).
async def ensure_seed_interviewer(session: AsyncSession, settings: Settings | None = None) -> User:
    settings = settings or get_settings()
    email = settings.seed_interviewer_email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing:
        return existing
    user = User(email=email, password_hash=hash_password(settings.seed_interviewer_password), role="interviewer")
    session.add(user)
    await session.commit()
    logger.info("seed interviewer created")
    return user