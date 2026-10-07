"""Copyable job description / posting text (posted manually outside the platform)."""

from __future__ import annotations

from typing import Any


# Build the plain-text job description from a validated profile; empty sections are skipped.
def build_posting_text(profile: dict[str, Any], company_name: str, google_form_link: str = "") -> str:
    requirements = profile["requirements"]
    required = [r["text"] for r in requirements if r["kind"] == "mandatory"]
    preferred = [r["text"] for r in requirements if r["kind"] == "preferred"]
    details = [f"{label}: {value}" for label, value in (("Employment type", profile.get("employment_type")), ("Location", profile.get("location"))) if value]
    lines = [profile["title"], company_name, ""]
    if profile.get("about_company"):
        lines += [f"About {company_name}", profile["about_company"], ""]
    lines += [profile["summary"], ""]
    if details:
        lines += details + [""]
    for heading, items in (("Responsibilities", profile.get("responsibilities", [])), ("Required", required),
                           ("Preferred", preferred), ("Benefits", profile.get("benefits", []))):
        if items:
            lines += [f"{heading}:"] + [f"- {item}" for item in items] + [""]
    apply_text = "Fill the Google form"
    if google_form_link:
        apply_text = f"[{apply_text}]({google_form_link})"
    lines.append(f"How to apply: {apply_text}")
    return "\n".join(lines)
