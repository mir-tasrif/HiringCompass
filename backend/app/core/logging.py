"""Persistent structured logging: INFO/WARNING -> logs/, ERROR+ (exceptions) -> errors/ (N22, N12)."""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import logging
import re
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Callable

from app.core.config import Settings, get_settings

# Per-request / per-run context attached to every log line.
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("log_context", default={})

# Patterns masked so logs never hold personal data or secrets.
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), "[jwt]"),
    (re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[=:]\s*\S+"), r"\1=[redacted]"),
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[number]"),
]

# Attributes that exist on every LogRecord and are not custom fields.
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


# Mask emails, tokens, secrets and phone-like numbers in free text.
def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


# Emit each record as one JSON line with context, extras and redacted text.
class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    # Build the JSON payload for a single record.
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": redact(record.getMessage()),
            **_context.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = redact("".join(traceback.format_exception(*record.exc_info)))
        return json.dumps(payload, default=str, ensure_ascii=False)


# Passes only records below ERROR so logs/ never duplicates errors/.
class _BelowErrorFilter(logging.Filter):
    # Accept INFO and WARNING records.
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


# Build a rotating file handler with the shared JSON formatter.
def _file_handler(path: str, level: int, service: str, settings: Settings) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path, maxBytes=settings.log_rotate_mb * 1024 * 1024, backupCount=settings.log_backup_count, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter(service))
    return handler


# Configure root logging once per process; call at API / worker startup.
def setup_logging(service: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    settings.ensure_directories()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(JsonFormatter(service))

    info_file = _file_handler(str(settings.log_dir / f"{service}.log"), logging.INFO, service, settings)
    info_file.addFilter(_BelowErrorFilter())
    error_file = _file_handler(str(settings.error_dir / f"{service}.error.log"), logging.ERROR, service, settings)

    for handler in (console, info_file, error_file):
        root.addHandler(handler)

    # Quiet noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# Named logger accessor used across modules.
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Temporarily attach IDs (request_id, run_id, thread_id...) to all logs in scope.
@contextmanager
def bind_context(**fields: Any) -> Iterator[None]:
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


# Record an exception with its traceback to errors/ and return a short category label.
def log_exception(logger: logging.Logger, message: str, exc: BaseException, **fields: Any) -> str:
    category = type(exc).__name__
    logger.error(message, exc_info=(type(exc), exc, exc.__traceback__), extra={"error_category": category, **fields})
    return category


# Node/middleware-level guard: logs any exception to errors/ with the node name, then re-raises.
def log_errors(node_name: str | None = None) -> Callable:
    def decorator(func: Callable) -> Callable:
        name = node_name or func.__name__
        logger = get_logger(f"node.{name}")

        if inspect.iscoroutinefunction(func):

            # Async wrapper capturing failures of async nodes.
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    log_exception(logger, f"node '{name}' failed", exc, node=name)
                    raise

            return async_wrapper

        # Sync wrapper capturing failures of sync nodes.
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                log_exception(logger, f"node '{name}' failed", exc, node=name)
                raise

        return sync_wrapper

    return decorator