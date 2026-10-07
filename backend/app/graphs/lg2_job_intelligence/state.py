"""LG2 state: IDs, small typed outputs and execution metadata only."""

from __future__ import annotations

from typing import Any, Literal

from app.graphs.common.base_state import RunMeta


# State of one job-drafting run (thread_id = job draft).
class JobIntelState(RunMeta, total=False):
    job_id: str
    draft_version: int
    recruiter_prompt: str
    criteria: dict[str, Any]
    criteria_sufficient: bool
    clarification_question: str | None
    clarification_options: list[str]
    clarification_field: str | None
    force_proceed: bool
    policy_passages: list[dict[str, Any]]
    job_profile: dict[str, Any] | None
    rubric: dict[str, Any] | None
    validation_errors: list[str]
    retry_count: int
    feedback: str | None
    approval_id: str | None
    decision: Literal["approve", "edit", "reject"] | None
    posting_text: str | None
    jd_indexed: bool
    active_version: int | None
    status: str