"""Authentication endpoints: login and current user."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import create_access_token
from app.db.models import User
from app.db.session import get_session
from app.services.users import authenticate

router = APIRouter(prefix="/auth", tags=["auth"])


# Login request body.
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


# Public view of a user (never includes the password hash).
class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str


# Login response: bearer token plus the user.
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# Exchange email + password for an access token.
@router.post("/login", response_model=TokenOut)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenOut:
    user = await authenticate(session, body.email, body.password)
    return TokenOut(access_token=create_access_token(user.id, user.role), user=UserOut(id=user.id, email=user.email, role=user.role))


# Return the signed-in user (used by the frontend to restore a session).
@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, role=user.role)