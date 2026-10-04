"""FastAPI entrypoint (Phase 2 skeleton): logging, config, health, error-intercepting middleware."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import bind_context, get_logger, log_exception, setup_logging

settings = get_settings()
logger = get_logger("app.main")


# Startup/shutdown hook: configure logging and runtime directories (graphs are wired in Phase 4).
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging("api", settings)
    logger.info("api starting", extra={"env": settings.app_env})
    yield
    logger.info("api stopping")


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware: tag each request, log it, and convert unhandled errors into a sanitized 500 (N12).
@app.middleware("http")
async def request_guard(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    with bind_context(request_id=request_id):
        try:
            response = await call_next(request)
            logger.info("request", extra={"method": request.method, "path": request.url.path, "status": response.status_code})
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            log_exception(logger, "unhandled error", exc, method=request.method, path=request.url.path)
            return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": request_id})


# Liveness endpoint used by Docker healthchecks.
@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "env": settings.app_env}