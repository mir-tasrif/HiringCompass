"""Job-specific F2 weights and immutable policy versions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import F2ScoringPolicy, Job, JobVersion
from app.graphs.f2_scoring.f2_scoring_graph import DIMENSIONS

DEFAULT_WEIGHTS = {"technical_projects": 40, "relevant_experience": 30,
                   "certifications_soft_skills": 10, "education_qualifications": 20}


def validate_weights(weights: dict[str, int]) -> dict[str, int]:
    if set(weights) != set(DIMENSIONS) or any(type(value) is not int or value < 0 or value > 100 for value in weights.values()):
        raise ValueError("Provide all four scoring weights as whole percentages from 0 to 100.")
    if sum(weights.values()) != 100:
        raise ValueError("The four scoring weights must add up to 100%.")
    return weights


def weights_from_rubric(rubric: dict[str, Any] | None) -> dict[str, int]:
    """Map existing JD rubric dimensions into the four F2 dimensions."""
    if not rubric or not isinstance(rubric.get("dimensions"), list):
        return DEFAULT_WEIGHTS.copy()
    mapped = {key: 0.0 for key in DIMENSIONS}
    aliases = {"technical_skills": "technical_projects", "projects": "technical_projects",
               "relevant_experience": "relevant_experience", "experience": "relevant_experience",
               "certifications": "certifications_soft_skills", "soft_skills": "certifications_soft_skills",
               "qualifications": "education_qualifications", "education": "education_qualifications"}
    for dimension in rubric["dimensions"]:
        key = aliases.get(str(dimension.get("key", "")))
        if key:
            mapped[key] += max(0.0, float(dimension.get("weight", 0)))
    if not any(mapped.values()):
        return DEFAULT_WEIGHTS.copy()
    # Keep a meaningful certifications/soft-skills dimension when older rubrics omitted it.
    mapped["certifications_soft_skills"] = max(mapped["certifications_soft_skills"], 0.10)
    total = sum(mapped.values())
    raw = {key: mapped[key] * 100 / total for key in DIMENSIONS}
    weights = {key: int(raw[key]) for key in DIMENSIONS}
    remainder = 100 - sum(weights.values())
    for key in sorted(DIMENSIONS, key=lambda item: (-(raw[item] - weights[item]), item))[:remainder]:
        weights[key] += 1
    return validate_weights(weights)


async def latest_policy(session: AsyncSession, job_id: uuid.UUID) -> F2ScoringPolicy | None:
    return await session.scalar(select(F2ScoringPolicy).where(F2ScoringPolicy.job_id == job_id)
                                .order_by(F2ScoringPolicy.version.desc()).limit(1))


async def suggested_weights(session: AsyncSession, job: Job) -> tuple[dict[str, int], int | None]:
    policy = await latest_policy(session, job.id)
    if policy:
        return policy.weights, policy.version
    version = await session.scalar(select(JobVersion).where(JobVersion.job_id == job.id,
        JobVersion.version == job.active_version))
    return weights_from_rubric(version.rubric if version else None), None


async def choose_policy(session: AsyncSession, job: Job, user_id: uuid.UUID,
                        weights: dict[str, int]) -> F2ScoringPolicy:
    weights = validate_weights(weights)
    previous = await latest_policy(session, job.id)
    if previous and previous.weights == weights:
        return previous
    policy = F2ScoringPolicy(job_id=job.id, version=(previous.version + 1 if previous else 1),
                             weights=weights.copy(), created_by=user_id)
    session.add(policy)
    await session.flush()
    return policy
