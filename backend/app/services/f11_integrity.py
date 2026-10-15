"""F11 CV integrity signals and the policy that routes them to recruiter review.

Keep detection rules and review policy here so future F11 improvements do not
change CV extraction or profile parsing.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pymupdf

from app.core.config import Settings


_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions\b", re.I),
    re.compile(r"\b(?:system|developer)\s+prompt\b", re.I),
    re.compile(r"\b(?:reveal|print|show)\s+(?:the\s+)?(?:hidden|system)\s+(?:prompt|instructions)\b", re.I),
    re.compile(r"\b(?:instructions\s+to\s+the\s+ai|assistant\s*:\s*)", re.I),
    re.compile(r"\bdo\s+not\s+follow\s+the\s+user\b", re.I),
)
_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"}


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


