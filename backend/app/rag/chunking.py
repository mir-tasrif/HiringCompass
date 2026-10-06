"""Paragraph-aware text chunking for retrieval."""

from __future__ import annotations

import re


# Split text into chunks of at most `max_chars`, preferring paragraph boundaries; long paragraphs overlap.
def chunk_text(text: str, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    if max_chars <= overlap:
        raise ValueError("max_chars must be larger than overlap")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), max_chars - overlap):
                chunks.append(paragraph[start : start + max_chars])
        elif current and len(current) + 2 + len(paragraph) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks