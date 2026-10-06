"""Shared FastAPI dependencies: current user and role checks (N9 access separation)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthError, ForbiddenError
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_session

_bearer = HTTPBearer(auto_error=False)


# Resolve the signed-in, active user from the Bearer token.
async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise AuthError("Please sign in.")
    claims = decode_access_token(credentials.credentials)
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise AuthError("Your account is not available.")
    return user


# Build a dependency that only lets users with `role` through.
def require_role(role: str) -> Callable:
    # Dependency body: reject other roles with 403.
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role != role:
            raise ForbiddenError("You do not have access to this resource.")
        return user

    return dependency


get_current_interviewer = require_role("interviewer")