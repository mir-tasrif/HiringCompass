"""Job-relevance guard (N20): detect protected characteristics in criteria text."""

from __future__ import annotations

import re

_PROTECTED = re.compile(
    r"\b(age|aged|gender|male|female|man|woman|nationality|citizen|citizenship|religion|religious|marital|married|single|"
    r"ethnic|ethnicity|race|racial|pregnant|pregnancy|disability|disabled)\b",
    re.IGNORECASE,
)


# Return the distinct protected-characteristic terms found in `text` (lower-cased, sorted).
def find_protected_terms(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _PROTECTED.finditer(text)})