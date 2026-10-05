"""CV upload validation and safe storage (N8, N14): type, size, signature, readability, pages, duplicates."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import UploadRejected
from app.core.logging import get_logger
from app.db.models import Application, Document

logger = get_logger("services.uploads")

_CHUNK = 64 * 1024


# Facts about a file that passed validation.
@dataclass(frozen=True)
class ValidatedUpload:
    data: bytes
    content_hash: str
    size_bytes: int
    page_count: int


# Log a rejection with only its code and size (file names can contain personal data) and raise it.
def _reject(code: str, message: str, size: int = 0) -> UploadRejected:
    logger.info("upload rejected", extra={"code": code, "size_bytes": size})
    return UploadRejected(code, message)


# Read an uploaded file in chunks, aborting as soon as it exceeds the size limit.
async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    buffer = bytearray()
    while chunk := await file.read(_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise _reject("too_large", f"File is larger than the {max_bytes // (1024 * 1024)} MB limit.", len(buffer))
    return bytes(buffer)


# Validate raw bytes as an acceptable PDF CV; raises UploadRejected with a user-friendly message.
def validate_pdf_bytes(data: bytes, filename: str | None, content_type: str | None, settings: Settings | None = None) -> ValidatedUpload:
    settings = settings or get_settings()
    size = len(data)
    if size == 0:
        raise _reject("empty", "The file is empty.")
    if size > settings.max_upload_bytes:
        raise _reject("too_large", f"File is larger than the {settings.max_upload_mb} MB limit.", size)
    if content_type and content_type.split(";")[0].strip().lower() not in settings.allowed_upload_type_list:
        raise _reject("bad_type", "Only PDF files are accepted.", size)
    if filename and not filename.lower().endswith(".pdf"):
        raise _reject("bad_extension", "Only files ending in .pdf are accepted.", size)
    if b"%PDF-" not in data[:1024]:
        raise _reject("bad_signature", "This file is not a real PDF document.", size)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise _reject("corrupt", "The PDF could not be opened; it may be damaged.", size) from None
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise _reject("encrypted", "Password-protected PDFs are not supported.", size)
        pages = doc.page_count
    if pages < 1:
        raise _reject("corrupt", "The PDF has no readable pages.", size)
    if pages > settings.max_pdf_pages:
        raise _reject("too_many_pages", f"The PDF has {pages} pages; the limit is {settings.max_pdf_pages}.", size)
    return ValidatedUpload(data=data, content_hash=hashlib.sha256(data).hexdigest(), size_bytes=size, page_count=pages)


# Read and validate an UploadFile without blocking the event loop during PDF parsing.
async def process_upload(file: UploadFile, settings: Settings | None = None) -> ValidatedUpload:
    settings = settings or get_settings()
    data = await read_upload_limited(file, settings.max_upload_bytes)
    return await asyncio.to_thread(validate_pdf_bytes, data, file.filename, file.content_type, settings)


# Reject a file whose content already exists in this job (same SHA-256).
async def ensure_not_duplicate(session: AsyncSession, job_id: uuid.UUID, content_hash: str) -> None:
    stmt = (
        select(Document.id)
        .join(Application, Application.id == Document.application_id)
        .where(Application.job_id == job_id, Document.content_hash == content_hash)
        .limit(1)
    )
    if (await session.execute(stmt)).first():
        raise _reject("duplicate", "This CV was already uploaded for this job.")


# Write the file under a random name (never the user's file name) with owner-only permissions.
# Returns the path relative to FILE_STORAGE_DIR, which is what the documents table stores.
def store_upload(upload: ValidatedUpload, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    relative = Path("cvs") / upload.content_hash[:2] / f"{uuid.uuid4().hex}.pdf"
    target = settings.file_storage_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(upload.data)
    os.replace(temp, target)
    return relative.as_posix()