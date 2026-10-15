"""CV text extraction and structured profile parsing."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pymupdf
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings
from app.core.errors import PermanentError
from app.prompts.user_messages import candidate_profile_message
from app.prompts.system import CV_PROFILE


class _Education(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualification: str
    institution: str
    year: str | None


class _Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    employer: str
    duration: str | None
    evidence: str


class _Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    technologies: list[str]


class _CandidateProfileData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_name: str | None
    education: list[_Education]
    experience: list[_Experience]
    skills: list[str]
    certifications: list[str]
    projects: list[_Project]


class CvExtractionFailure(PermanentError):
    """The document opened but yielded no usable text, including after OCR."""


_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)


def extract_candidate_email(text: str) -> str | None:
    """Read a contact address directly from CV text; do not ask an LLM to invent one."""
    for match in _EMAIL_PATTERN.finditer(text[:30_000]):
        address = match.group(0).strip(".,;:()[]<>").lower()
        if len(address) <= 255:
            return address
    return None


def _safe_path(file_storage_dir: Path, relative_path: str) -> Path:
    root = file_storage_dir.resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CvExtractionFailure("The stored CV path is invalid.") from exc
    if not path.is_file():
        raise CvExtractionFailure("The stored CV file is missing.")
    return path


def extract_pdf_text(path: Path, settings: Settings) -> tuple[str, str]:
    """Extract native text, using English Tesseract OCR only for pages without usable text."""
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise CvExtractionFailure("The PDF could not be opened for text extraction.") from exc
    page_texts: list[str] = []
    used_ocr = False
    ocr_failures: list[int] = []
    with document:
        if document.needs_pass or document.is_encrypted:
            raise CvExtractionFailure("The PDF is password protected and could not be read.")
        for page_number, page in enumerate(document, 1):
            text = page.get_text("text", sort=True).strip()
            if len(re.sub(r"\s", "", text)) < 20:
                if not settings.ocr_enabled:
                    ocr_failures.append(page_number)
                    continue
                used_ocr = True
                with tempfile.TemporaryDirectory(prefix="hc-cv-ocr-") as temp:
                    image = Path(temp) / "page.png"
                    scale = min(2.5, 4500 / max(page.rect.width, page.rect.height))
                    try:
                        page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(image)
                    except Exception:
                        ocr_failures.append(page_number)
                        continue
                    try:
                        result = subprocess.run(
                            [settings.tesseract_cmd, str(image), "stdout", "-l", "eng"],
                            capture_output=True, text=True, encoding="utf-8", timeout=45, check=True,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        ocr_failures.append(page_number)
                        continue
                    text = result.stdout.strip()
                if len(re.sub(r"\s", "", text)) < 20:
                    ocr_failures.append(page_number)
                    continue
            page_texts.append(f"[Page {page_number}]\n{text}")
    combined = "\n\n".join(page_texts).strip()
    if len(re.sub(r"\s", "", combined)) < 40:
        if ocr_failures:
            raise CvExtractionFailure(f"No readable text could be extracted, including English OCR (pages: {', '.join(map(str, ocr_failures))}).")
        raise CvExtractionFailure("The CV contains no usable text after extraction and OCR.")
    return combined, "ocr" if used_ocr else "native"


_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "candidate_name": {"type": ["string", "null"]},
        "education": {"type": "array", "items": {"type": "object", "properties": {
            "qualification": {"type": "string"}, "institution": {"type": "string"}, "year": {"type": ["string", "null"]},
        }, "required": ["qualification", "institution", "year"], "additionalProperties": False}},
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            "role": {"type": "string"}, "employer": {"type": "string"}, "duration": {"type": ["string", "null"]}, "evidence": {"type": "string"},
        }, "required": ["role", "employer", "duration", "evidence"], "additionalProperties": False}},
        "skills": {"type": "array", "items": {"type": "string"}},
        "certifications": {"type": "array", "items": {"type": "string"}},
        "projects": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"}, "technologies": {"type": "array", "items": {"type": "string"}},
        }, "required": ["name", "description", "technologies"], "additionalProperties": False}},
    },
    "required": ["candidate_name", "education", "experience", "skills", "certifications", "projects"],
}


async def parse_candidate_profile(llm: Any, text: str) -> dict[str, Any]:
    """Extract requested CV facts; the document is untrusted data, never instructions."""
    prompt = candidate_profile_message(text)
    messages = [{"role": "system", "content": CV_PROFILE.text},
                {"role": "user", "content": prompt}]
    for attempt in range(2):
        raw = await llm.chat(messages, schema=_PROFILE_SCHEMA)
        try:
            profile = _CandidateProfileData.model_validate(json.loads(raw)).model_dump(mode="json")
            profile["email"] = extract_candidate_email(text)
            return profile
        except (TypeError, json.JSONDecodeError, ValidationError) as exc:
            if attempt:
                raise PermanentError("The AI returned an invalid structured candidate profile after a repair attempt.") from exc
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Repair the response to match the requested JSON schema exactly. Return only a JSON object; use empty arrays and null for missing facts."},
            ]
    raise PermanentError("The AI returned an invalid structured candidate profile.")

