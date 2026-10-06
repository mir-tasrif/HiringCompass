"""Prompt templates for job drafting."""

from __future__ import annotations

import json
from typing import Any

from app.llm.structured import wrap_untrusted

EXTRACT_SYSTEM = (
    "You read a recruiter's description of a job and extract structured criteria. "
    "List every requirement as mandatory (the recruiter says required/must/essential) or preferred (nice to have). "
    "Never invent requirements the recruiter did not state. If the title or key requirements are missing, "
    "set sufficient=false and list what is missing in missing_info. Do not include or infer protected characteristics "
    "(age, gender, nationality, religion, marital status)."
)

PROFILE_SYSTEM = (
    "You write a job profile from extracted criteria. Keep requirements faithful to the criteria; mark a requirement "
    "mandatory only if the criteria say so. Use the company policy passages when relevant (for example experience "
    "equivalences) but never invent company rules. Every requirement must be job-related; never include age, gender, "
    "nationality, religion, marital status or other protected characteristics. Give higher weight to requirements the "
    "recruiter emphasised."
)

RUBRIC_SYSTEM = (
    "You design a scoring rubric for screening candidates of this job. Required dimensions, with exactly these keys: "
    "technical_skills, relevant_experience, qualifications, job_match (overall CV-to-job-description match). "
    "You may add extra job-related dimensions. Give each dimension a weight between 0 and 1; weights must sum to 1 and "
    "reflect the recruiter's priorities. For every dimension write score anchors for 0, 50 and 100 describing what that "
    "score means. Never score protected characteristics."
)


# User prompt for profile generation: criteria + policy passages + recruiter feedback + earlier validation errors.
def profile_prompt(criteria: dict[str, Any], passages: list[dict[str, Any]], feedback: str | None, errors: list[str]) -> str:
    parts = ["Extracted criteria (JSON):", json.dumps(criteria, ensure_ascii=False)]
    if passages:
        policy = "\n\n".join(f"[{p['title']}] {p['content']}" for p in passages)
        parts += ["Company hiring policy passages:", wrap_untrusted("company_policy", policy)]
    if feedback:
        parts += ["The recruiter asked for these changes to the previous draft:", feedback]
    if errors:
        parts += ["Fix these problems found in your previous draft:", "\n".join(f"- {e}" for e in errors)]
    return "\n\n".join(parts)


# User prompt for rubric generation.
def rubric_prompt(profile: dict[str, Any], priorities: str, errors: list[str]) -> str:
    parts = ["Job profile (JSON):", json.dumps(profile, ensure_ascii=False), f"Recruiter priorities: {priorities or 'none stated'}"]
    if errors:
        parts += ["Fix these problems found in your previous draft:", "\n".join(f"- {e}" for e in errors)]
    return "\n\n".join(parts)