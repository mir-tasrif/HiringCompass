"""Prompt templates for job drafting."""

from __future__ import annotations

import json
from typing import Any

from app.llm.structured import wrap_untrusted

from app.prompts.system import EXTRACT_SYSTEM, CLARIFY_SYSTEM, PROFILE_SYSTEM, EDIT_PLAN_SYSTEM, RUBRIC_SYSTEM


# User prompt for profile generation: criteria, company passages, previous JDs, recruiter feedback, earlier validation errors.
def profile_prompt(criteria: dict[str, Any], passages: list[dict[str, Any]], feedback: str | None, errors: list[str], company_name: str,
                   base_profile: dict[str, Any] | None = None, edit_plan: dict[str, Any] | None = None) -> str:
    parts = [f"The hiring company is {company_name}.", "Extracted criteria (JSON):", json.dumps(criteria, ensure_ascii=False)]
    company = [p for p in passages if p.get("kind") != "previous_jd"]
    previous = [p for p in passages if p.get("kind") == "previous_jd"]
    if company:
        parts += ["Company information and hiring policy passages:",
                  wrap_untrusted("company_policy", "\n\n".join(f"[{p['title']}] {p['content']}" for p in company))]
    if previous:
        parts += ["Previously approved job descriptions of this company (style reference only):",
                  wrap_untrusted("previous_job_descriptions", "\n\n".join(f"[{p['title']}] {p['content']}" for p in previous))]
    if feedback:
        parts += ["The recruiter asked for these changes to the previous draft:", feedback]
    if base_profile:
        parts += ["Previous approved-for-review JD draft (preserve unchanged content):", json.dumps(base_profile, ensure_ascii=False)]
    if edit_plan:
        parts += ["Exact edit patch (do not add or remove other requirements):", json.dumps(edit_plan, ensure_ascii=False)]
    if errors:
        parts += ["Fix these problems found in your previous draft:", "\n".join(f"- {e}" for e in errors)]
    return "\n\n".join(parts)


# User prompt for rubric generation.
def rubric_prompt(profile: dict[str, Any], priorities: str, errors: list[str]) -> str:
    parts = ["Job profile (JSON):", json.dumps(profile, ensure_ascii=False), f"Recruiter priorities: {priorities or 'none stated'}"]
    if errors:
        parts += ["Fix these problems found in your previous draft:", "\n".join(f"- {e}" for e in errors)]
    return "\n\n".join(parts)



# User prompt for the follow-up question: what is known, what is missing, and the conversation so far.
def clarify_prompt(criteria: dict[str, Any], missing: list[str], transcript: str) -> str:
    return "\n\n".join([
        "Criteria understood so far (JSON):", json.dumps(criteria, ensure_ascii=False),
        "Still missing: " + "; ".join(missing),
        "Conversation so far:", transcript,
    ])
