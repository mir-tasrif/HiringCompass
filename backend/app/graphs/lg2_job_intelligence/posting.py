"""Copyable job posting text (posted manually outside the platform)."""

from __future__ import annotations

from typing import Any


# Build plain-text posting copy from a validated job profile.
def build_posting_text(profile: dict[str, Any]) -> str:
    requirements = profile["requirements"]
    required = [r["text"] for r in requirements if r["kind"] == "mandatory"]
    preferred = [r["text"] for r in requirements if r["kind"] == "preferred"]
    lines = [profile["title"], "", profile["summary"], ""]
    for heading, items in (("Responsibilities", profile.get("responsibilities", [])), ("Required", required), ("Preferred", preferred)):
        if items:
            lines += [f"{heading}:"] + [f"- {item}" for item in items] + [""]
    lines.append("How to apply: please send your CV to the contact email provided in this job post.")
    return "\n".join(lines)