"""Prompt templates for job drafting."""

from __future__ import annotations

import json
from typing import Any

from app.llm.structured import wrap_untrusted

EXTRACT_SYSTEM = (
    "You read a conversation between an assistant and a recruiter (lines starting 'Recruiter:' and 'Assistant asked:') "
    "and extract structured job criteria. List every requirement as mandatory (the recruiter says required/must/essential) "
    "or preferred (nice to have). Fill `experience` (for example '0-1 years' or 'fresh graduates welcome') and `education` "
    "(for example 'B.Sc in Computer Science'); if the recruiter says there is no requirement for one of them, write "
    "'No specific requirement'. Leave a field empty only if it was never discussed. Also capture employment_type and "
    "location if mentioned. Never invent requirements the recruiter did not state. List anything still missing in "
    "missing_info. Do not include or infer protected characteristics (age, gender, nationality, religion, marital status)."
)

CLARIFY_SYSTEM = (
    "You are a recruiting assistant interviewing a recruiter to define a job. Ask exactly ONE short question about the "
    "single most important missing item. Priority order: experience needed, education, key required skills, preferred "
    "skills, employment type, location or work mode. Give 2 to 5 short example answers as options (for experience: "
    "concrete choices such as '0 years', '1 year', '2 years'); the recruiter may also type freely. Never repeat a "
    "question that was already answered in the conversation, and never ask about protected characteristics."
)

PROFILE_SYSTEM = (
    "You write a complete job description from extracted criteria. Requirements must come ONLY from the criteria: keep "
    "them faithful and mark a requirement mandatory only if the criteria say so. You may use your general knowledge of "
    "the role to write the summary and the responsibilities. Use the company passages for about_company and benefits, and "
    "never invent facts about the company: if no passage supports about_company or benefits, leave them empty. Use "
    "previous job descriptions only as a style and structure reference, never copy their requirements. Fill "
    "employment_type and location from the criteria when known. Every item must be job-related; never include age, "
    "gender, nationality, religion, marital status or other protected characteristics. Give higher weight to "
    "requirements the recruiter emphasised."
)

RUBRIC_SYSTEM = (
    "You design a scoring rubric for screening candidates of this job. Required dimensions, with exactly these keys: "
    "technical_skills, relevant_experience, qualifications, job_match (overall CV-to-job-description match). "
    "You may add extra job-related dimensions. Give each dimension a weight between 0 and 1; weights must sum to 1 and "
    "reflect the recruiter's priorities. For every dimension write score anchors for 0, 50 and 100 describing what that "
    "score means. Never score protected characteristics."
)


# User prompt for profile generation: criteria, company passages, previous JDs, recruiter feedback, earlier validation errors.
def profile_prompt(criteria: dict[str, Any], passages: list[dict[str, Any]], feedback: str | None, errors: list[str], company_name: str) -> str:
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