"""Maps application errors to safe HTTP responses (N12)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AuthError, ForbiddenError, UploadRejected

# HTTP status per upload rejection code; anything else is 422.
_UPLOAD_STATUS = {"too_large": 413, "bad_type": 415, "bad_extension": 415, "duplicate": 409}


# Register all custom exception handlers on the app.
def register_error_handlers(app: FastAPI) -> None:
    # Turn UploadRejected into a JSON response with a user-friendly message and a stable code.
    @app.exception_handler(UploadRejected)
    async def upload_rejected_handler(_: Request, exc: UploadRejected) -> JSONResponse:
        return JSONResponse(status_code=_UPLOAD_STATUS.get(exc.code, 422), content={"detail": exc.message, "code": exc.code})

    # Missing or invalid credentials: 401 with a Bearer challenge.
    @app.exception_handler(AuthError)
    async def auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc), "code": "auth_failed"}, headers={"WWW-Authenticate": "Bearer"})

    # Wrong role: 403.
    @app.exception_handler(ForbiddenError)
    async def forbidden_handler(_: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc), "code": "forbidden"})