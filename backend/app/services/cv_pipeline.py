"""CV text extraction, document-level integrity signals, and structured profile parsing."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import pymupdf
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings
from app.core.errors import PermanentError
from app.llm.structured import UNTRUSTED_NOTICE, wrap_untrusted


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


_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions\b", re.I),
    re.compile(r"\b(?:system|developer)\s+prompt\b", re.I),
    re.compile(r"\b(?:reveal|print|show)\s+(?:the\s+)?(?:hidden|system)\s+(?:prompt|instructions)\b", re.I),
    re.compile(r"\b(?:instructions\s+to\s+the\s+ai|assistant\s*:\s*)", re.I),
    re.compile(r"\bdo\s+not\s+follow\s+the\s+user\b", re.I),
)
_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"}


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


def inspect_integrity(path: Path, extracted_text: str, settings: Settings) -> list[dict[str, Any]]:
    """Return explainable formatting, hidden-text, Unicode, and injection-pattern signals."""
    signals: list[dict[str, Any]] = []
    try:
        document = pymupdf.open(path)
    except Exception:
        return [{"code": "pdf_inspection_failed", "severity": "high", "evidence": "PDF formatting could not be inspected."}]
    with document:
        for page_number, page in enumerate(document, 1):
            blocks = page.get_text("dict").get("blocks", [])
            for block in blocks:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = str(span.get("text", ""))
                        if not text.strip():
                            continue
                        size = float(span.get("size", 0) or 0)
                        color = int(span.get("color", 0) or 0)
                        if size and size < settings.integrity_min_font_pt:
                            signals.append({"code": "tiny_text", "severity": "medium", "page": page_number,
                                            "evidence": f"Text uses {size:.1f} pt font: {text[:100]}"})
                        if color >= 0xF0F0F0:
                            signals.append({"code": "white_text", "severity": "high", "page": page_number,
                                            "evidence": f"Near-white text on page {page_number}: {text[:100]}"})
                        bounds = span.get("bbox")
                        if bounds and (bounds[0] < page.rect.x0 - 2 or bounds[1] < page.rect.y0 - 2 or
                                       bounds[2] > page.rect.x1 + 2 or bounds[3] > page.rect.y1 + 2):
                            signals.append({"code": "off_page_text", "severity": "high", "page": page_number,
                                            "evidence": f"Text extends outside the visible page area: {text[:100]}"})
            try:
                for span in page.get_texttrace():
                    if span.get("type") == 3 or float(span.get("opacity", 1) or 0) <= 0.05:
                        chars = "".join(chr(ch[0]) for ch in span.get("chars", []) if ch and ch[0] >= 32)
                        if chars.strip():
                            signals.append({"code": "invisible_text", "severity": "high", "page": page_number,
                                            "evidence": f"Invisible PDF text: {chars[:100]}"})
            except Exception:
                # PyMuPDF builds without texttrace still receive the other document-level checks.
                pass

    zero_width = [f"U+{ord(char):04X}" for char in extracted_text if char in _ZERO_WIDTH]
    if zero_width:
        signals.append({"code": "zero_width_unicode", "severity": "medium",
                        "evidence": f"Found {len(zero_width)} zero-width or soft-hyphen characters ({', '.join(sorted(set(zero_width)))})"})
    format_chars = [f"U+{ord(char):04X}" for char in extracted_text if unicodedata.category(char) == "Cf" and char not in _ZERO_WIDTH]
    if format_chars:
        signals.append({"code": "unusual_format_unicode", "severity": "medium",
                        "evidence": f"Found unusual Unicode formatting characters ({', '.join(sorted(set(format_chars))[:8])})"})
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(extracted_text)
        if match:
            excerpt = re.sub(r"\s+", " ", extracted_text[max(0, match.start() - 50):match.end() + 100])
            signals.append({"code": "instruction_like_text", "severity": "high", "evidence": excerpt[:220]})
    return signals


def integrity_requires_review(signals: list[dict[str, Any]]) -> bool:
    """Route any high-severity or multiple independent medium signals to recruiter review."""
    suspicious_codes = {"tiny_text", "white_text", "invisible_text", "instruction_like_text"}
    return any(signal.get("severity") == "high" or signal.get("code") in suspicious_codes for signal in signals) or len(signals) >= 2


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
    safe_text = text[:100_000]
    prompt = (
        "Extract only facts explicitly supported by the CV into the requested JSON schema. Use empty arrays for absent sections, "
        "null for an unknown name/year/duration, and preserve concise evidence. Do not infer qualifications.\n\n" +
        wrap_untrusted("candidate_cv", safe_text)
    )
    raw = await llm.chat([{"role": "system", "content": "You extract structured candidate facts accurately and conservatively. " + UNTRUSTED_NOTICE},
                          {"role": "user", "content": prompt}], schema=_PROFILE_SCHEMA)
    try:
        profile = _CandidateProfileData.model_validate(json.loads(raw)).model_dump(mode="json")
    except (TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise PermanentError("The AI returned an invalid structured candidate profile.") from exc
    return profile

