"""FastAPI entrypoint: logging, config, health, auth, durable checkpointer, error-intercepting middleware."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.errors import register_error_handlers
from app.api.routers.auth import router as auth_router
from app.core.config import get_settings
from app.core.logging import bind_context, get_logger, log_exception, setup_logging
from app.db.session import SessionLocal, check_db
from app.graphs.common.checkpointer import open_checkpointer
from app.llm.client import get_llm_client
from app.services.users import ensure_seed_interviewer

settings = get_settings()
logger = get_logger("app.main")


# Startup/shutdown hook: logging, seed interviewer, durable checkpointer (graphs are wired as routers arrive).
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging("api", settings)
    logger.info("api starting", extra={"env": settings.app_env})
    async with SessionLocal() as session:
        await ensure_seed_interviewer(session, settings)
    async with open_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer
        yield
    logger.info("api stopping")


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
register_error_handlers(app)

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


app.include_router(auth_router)


# Liveness endpoint used by Docker healthchecks.
@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "env": settings.app_env}


# Readiness endpoint: confirms the database connection works.
@app.get("/health/db", tags=["system"])
async def health_db() -> JSONResponse:
    ok = await check_db()
    return JSONResponse(status_code=200 if ok else 503, content={"db": "ok" if ok else "unavailable"})


# Readiness endpoint: confirms the chat provider and embedding model are usable (None = provider can't list models).
@app.get("/health/llm", tags=["system"])
async def health_llm() -> JSONResponse:
    try:
        info = await get_llm_client().health()
    except Exception:
        return JSONResponse(status_code=503, content={"llm": "unavailable"})
    ready = info["llm_model"] is not False and info["embedding_model"]
    return JSONResponse(status_code=200 if ready else 503, content={"llm": "ok" if ready else "models_missing", **info})