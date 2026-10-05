"""Upload validation tests (WBS 2.1.3): every rejection path, safe storage, duplicates, HTTP mapping."""

import asyncio
import os
import stat
import uuid

import psycopg
import pymupdf
import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.errors import register_error_handlers
from app.core.config import Settings
from app.core.errors import UploadRejected
from app.db.models import Application, Candidate, Document, Job, User
from app.services.uploads import ensure_not_duplicate, process_upload, store_upload, validate_pdf_bytes


# Settings pointing at a temp storage dir with small limits.
@pytest.fixture
def settings(tmp_path):
    return Settings(file_storage_dir=tmp_path / "s", export_dir=tmp_path / "s/e", quarantine_dir=tmp_path / "s/q",
                    log_dir=tmp_path / "l", error_dir=tmp_path / "er", max_upload_mb=1, max_pdf_pages=2)


# Build an in-memory PDF with the given number of text pages.
def make_pdf(pages: int = 1, **save_kwargs) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Sample CV page {i}")
    return doc.tobytes(**save_kwargs)


# Assert that validation raises UploadRejected with the expected code.
def rejected(code: str, *args, settings) -> None:
    with pytest.raises(UploadRejected) as exc:
        validate_pdf_bytes(*args, settings=settings)
    assert exc.value.code == code


# A normal PDF passes and reports hash, size and pages.
def test_valid_pdf(settings):
    data = make_pdf(2)
    ok = validate_pdf_bytes(data, "cv.pdf", "application/pdf", settings)
    assert ok.page_count == 2 and ok.size_bytes == len(data) and len(ok.content_hash) == 64


# Every invalid input is rejected with a specific code.
def test_rejections(settings):
    rejected("empty", b"", "cv.pdf", "application/pdf", settings=settings)
    rejected("too_large", b"%PDF-" + b"0" * (1024 * 1024), "cv.pdf", "application/pdf", settings=settings)
    rejected("bad_type", make_pdf(), "cv.pdf", "image/png", settings=settings)
    rejected("bad_extension", make_pdf(), "cv.exe", "application/pdf", settings=settings)
    rejected("bad_signature", b"MZ\x90\x00 not a pdf at all", "cv.pdf", "application/pdf", settings=settings)
    rejected("corrupt", b"%PDF-1.4 this is garbage", "cv.pdf", "application/pdf", settings=settings)
    rejected("too_many_pages", make_pdf(3), "cv.pdf", "application/pdf", settings=settings)
    locked = make_pdf(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="a", owner_pw="b")
    rejected("encrypted", locked, "cv.pdf", "application/pdf", settings=settings)


# Stored files get a random name, never the original, with owner-only permissions.
def test_store_upload(settings):
    ok = validate_pdf_bytes(make_pdf(), "Jane_Doe_CV.pdf", "application/pdf", settings)
    relative = store_upload(ok, settings)
    path = settings.file_storage_dir / relative
    assert path.read_bytes() == ok.data and "Jane" not in relative
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# Duplicate content is rejected within one job but allowed in another (needs migrated Postgres).
def test_duplicate_detection():
    url = os.getenv("DATABASE_URL", "postgresql+psycopg://hc_user:change_me@localhost:5432/hiringcompass")
    try:
        psycopg.connect(url.replace("+psycopg", ""), connect_timeout=3).close()
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    # Insert fixtures, run the checks, and always clean up.
    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            user = User(email=f"{uuid.uuid4()}@test.local", password_hash="x", role="interviewer")
            s.add(user)
            await s.flush()
            job_a, job_b = Job(title="A", owner_id=user.id), Job(title="B", owner_id=user.id)
            cand = Candidate(full_name="Test Person")
            s.add_all([job_a, job_b, cand])
            await s.flush()
            app_row = Application(candidate_id=cand.id, job_id=job_a.id, job_version=1)
            s.add(app_row)
            await s.flush()
            s.add(Document(application_id=app_row.id, file_path="cvs/x.pdf", content_hash="h" * 64, size_bytes=10))
            await s.commit()
            try:
                with pytest.raises(UploadRejected) as exc:
                    await ensure_not_duplicate(s, job_a.id, "h" * 64)
                assert exc.value.code == "duplicate"
                await ensure_not_duplicate(s, job_b.id, "h" * 64)
                await ensure_not_duplicate(s, job_a.id, "z" * 64)
            finally:
                await s.delete(cand)
                await s.delete(job_a)
                await s.delete(job_b)
                await s.delete(user)
                await s.commit()
        await engine.dispose()

    asyncio.run(scenario())


# HTTP layer: success, oversize (413), wrong type (415), corrupt (422) with safe JSON bodies.
def test_http_mapping(settings):
    app = FastAPI()
    register_error_handlers(app)

    # Test-only route using the same pipeline the CV batch endpoint will use.
    @app.post("/validate")
    async def validate(file: UploadFile):
        up = await process_upload(file, settings)
        return {"pages": up.page_count}

    client = TestClient(app)
    ok = client.post("/validate", files={"file": ("cv.pdf", make_pdf(1), "application/pdf")})
    assert ok.status_code == 200 and ok.json() == {"pages": 1}
    big = client.post("/validate", files={"file": ("cv.pdf", b"%PDF-" + b"0" * (2 * 1024 * 1024), "application/pdf")})
    assert big.status_code == 413 and big.json()["code"] == "too_large"
    wrong = client.post("/validate", files={"file": ("cv.png", make_pdf(1), "image/png")})
    assert wrong.status_code == 415
    bad = client.post("/validate", files={"file": ("cv.pdf", b"%PDF-1.4 garbage", "application/pdf")})
    assert bad.status_code == 422 and "Traceback" not in bad.text