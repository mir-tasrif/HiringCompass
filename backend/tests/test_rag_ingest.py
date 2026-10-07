"""Knowledge PDF extraction, OCR and invalid-document handling."""

import pymupdf
import pytest

from app.core.config import Settings
from app.core.errors import PermanentError
from app.rag.ingest import read_knowledge_file


def test_pdf_keeps_page_references(tmp_path):
    path = tmp_path / "company.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Company develops Python applications.")
        doc.new_page().insert_text((72, 72), "Employees receive health insurance.")
        doc.save(path)
    text = read_knowledge_file(path, Settings())
    assert "[Page 1]" in text and "[Page 2]" in text
    assert "Python applications" in text and "health insurance" in text


def test_scanned_company_pdf_uses_ocr(tmp_path):
    path = tmp_path / "scanned.pdf"
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((72, 100), "Company builds software products", fontsize=24)
        image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    with pymupdf.open() as scanned:
        page = scanned.new_page()
        page.insert_image(page.rect, stream=image)
        scanned.save(path)
    assert "software products" in read_knowledge_file(path, Settings()).lower()
    with pytest.raises(PermanentError, match="OCR"):
        read_knowledge_file(path, Settings(ocr_enabled=False))


def test_unreadable_knowledge_pdf_is_rejected(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 broken")
    with pytest.raises(PermanentError, match="could not be opened"):
        read_knowledge_file(path, Settings())
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Private company information")
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="password", owner_pw="owner")
    with pytest.raises(PermanentError, match="Password-protected"):
        read_knowledge_file(path, Settings())
