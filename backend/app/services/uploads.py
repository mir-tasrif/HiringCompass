"""CV upload validation and safe storage (N8, N14): type, size, signature and duplicates."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from io import BytesIO

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
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class ValidatedUpload:
    data: bytes
    content_hash: str
    size_bytes: int
    page_count: int
    extension: str
    converted_from_docx: bool = False


def _reject(code: str, message: str, size: int = 0) -> UploadRejected:
    logger.info("upload rejected", extra={"code": code, "size_bytes": size})
    return UploadRejected(code, message)


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    buffer = bytearray()
    while chunk := await file.read(_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise _reject(
                "too_large",
                f"File is larger than the {max_bytes // (1024 * 1024)} MB limit.",
                len(buffer),
            )
    return bytes(buffer)


def _validate_pdf(data: bytes, settings: Settings) -> int:
    if b"%PDF-" not in data[:1024]:
        raise _reject("bad_signature", "This file is not a real PDF document.", len(data))

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise _reject("corrupt", "The PDF could not be opened; it may be damaged.", len(data)) from None

    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise _reject("encrypted", "Password-protected PDFs are not supported.", len(data))
        pages = doc.page_count

    if pages < 1:
        raise _reject("corrupt", "The PDF has no readable pages.", len(data))
    if pages > settings.max_pdf_pages:
        raise _reject(
            "too_many_pages",
            f"The PDF has {pages} pages; the limit is {settings.max_pdf_pages}.",
            len(data),
        )
    return pages


def _validate_docx(data: bytes) -> None:
    if not data.startswith(b"PK"):
        raise _reject("bad_signature", "This file is not a valid DOCX document.", len(data))

    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise _reject("bad_signature", "This file is not a valid DOCX document.", len(data))

            # Reject archives with unreasonable expansion before storing them.
            if len(names) > 1000 or sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
                raise _reject("corrupt", "The DOCX file has an unsupported internal structure.", len(data))
    except UploadRejected:
        raise
    except (zipfile.BadZipFile, OSError, ValueError):
        raise _reject("corrupt", "The DOCX file could not be opened; it may be damaged.", len(data)) from None


def _validate_upload_bytes(
    data: bytes,
    filename: str | None,
    content_type: str | None,
    settings: Settings | None = None,
) -> ValidatedUpload:
    settings = settings or get_settings()
    size = len(data)
    if size == 0:
        raise _reject("empty", "The file is empty.")
    if size > settings.max_upload_bytes:
        raise _reject("too_large", f"File is larger than the {settings.max_upload_mb} MB limit.", size)

    extension = Path(filename or "").suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise _reject("bad_extension", "Only .pdf and .docx files are accepted.", size)

    expected_type = "application/pdf" if extension == ".pdf" else _DOCX_MIME
    accepted_types = set(settings.allowed_upload_type_list)
    if expected_type not in accepted_types:
        raise _reject("bad_type", "This file type is not enabled for uploads.", size)
    if content_type:
        normalized_type = content_type.split(";")[0].strip().lower()
        if normalized_type != expected_type:
            raise _reject("bad_type", "Only PDF and DOCX files are accepted.", size)

    converted_from_docx = extension == ".docx"
    if not converted_from_docx:
        pages = _validate_pdf(data, settings)
    else:
        _validate_docx(data)
        data = _convert_docx_to_pdf(data, settings)
        pages = _validate_pdf(data, settings)

    return ValidatedUpload(
        data=data,
        content_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        page_count=pages,
        extension=".pdf",
        converted_from_docx=converted_from_docx,
    )


def _convert_docx_to_pdf(data: bytes, settings: Settings) -> bytes:
    converter = shutil.which("libreoffice") or shutil.which("soffice")
    if converter is None:
        raise _reject("conversion_unavailable", "DOCX conversion is unavailable. Please try again later.", len(data))

    try:
        with tempfile.TemporaryDirectory(prefix="hc-docx-") as temp_dir:
            root = Path(temp_dir)
            input_path = root / "upload.docx"
            output_path = root / "upload.pdf"
            profile_path = root / "lo-profile"
            input_path.write_bytes(data)
            subprocess.run(
                [converter, f"-env:UserInstallation={profile_path.as_uri()}", "--headless", "--convert-to", "pdf",
                 "--outdir", str(root), str(input_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
            )
            if not output_path.is_file():
                raise RuntimeError("LibreOffice did not create a PDF")
            pdf_data = output_path.read_bytes()
    except (OSError, subprocess.SubprocessError, RuntimeError):
        raise _reject("conversion_failed", "The DOCX file could not be converted to PDF.", len(data)) from None

    if len(pdf_data) > settings.max_upload_bytes:
        raise _reject("converted_too_large", "The converted PDF exceeds the upload size limit.", len(pdf_data))
    return pdf_data


def validate_upload_bytes(
    data: bytes,
    filename: str | None,
    content_type: str | None,
    settings: Settings | None = None,
) -> ValidatedUpload:
    settings = settings or get_settings()
    return _validate_upload_bytes(data, filename, content_type, settings)


def validate_pdf_bytes(
    data: bytes,
    filename: str | None,
    content_type: str | None,
    settings: Settings | None = None,
) -> ValidatedUpload:
    """Backward-compatible PDF-only validator used by existing callers and tests."""
    if filename and Path(filename).suffix.lower() != ".pdf":
        raise _reject("bad_extension", "Only files ending in .pdf are accepted.", len(data))
    settings = settings or get_settings()
    size = len(data)
    if size == 0:
        raise _reject("empty", "The file is empty.")
    if size > settings.max_upload_bytes:
        raise _reject("too_large", f"File is larger than the {settings.max_upload_mb} MB limit.", size)
    if content_type and content_type.split(";")[0].strip().lower() not in settings.allowed_upload_type_list:
        raise _reject("bad_type", "Only PDF files are accepted.", size)
    pages = _validate_pdf(data, settings)
    return ValidatedUpload(data, hashlib.sha256(data).hexdigest(), size, pages, ".pdf")


async def process_upload(file: UploadFile, settings: Settings | None = None) -> ValidatedUpload:
    settings = settings or get_settings()
    data = await read_upload_limited(file, settings.max_upload_bytes)
    return await asyncio.to_thread(_validate_upload_bytes, data, file.filename, file.content_type, settings)


async def ensure_not_duplicate(session: AsyncSession, job_id: uuid.UUID, content_hash: str) -> None:
    stmt = (
        select(Document.id)
        .join(Application, Application.id == Document.application_id)
        .where(Application.job_id == job_id, Application.stage != "rejected", Document.content_hash == content_hash)
        .limit(1)
    )
    if (await session.execute(stmt)).first():
        raise _reject("duplicate", "This CV was already uploaded for this job.")


def store_upload(upload: ValidatedUpload, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    relative = Path("cvs") / upload.content_hash[:2] / f"{uuid.uuid4().hex}{upload.extension}"
    target = settings.file_storage_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(upload.data)
    os.replace(temp, target)
    return relative.as_posix()
