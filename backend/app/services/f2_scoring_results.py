"""Prepare F2 scoring inputs and turn a graph result into a saved score.

The input hash includes the CV, job, weights, model, scoring algorithm, and
system-prompt version. A change to any of them requires a fresh assessment.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from app.core.config import Settings
from app.db.models import CandidateProfile, CandidateScore, F2ScoringPolicy, JobVersion
from app.graphs.f2_scoring.f2_scoring_graph import SCORING_VERSION
from app.prompts.system import F2_SCORING


def prepare_scoring_input(profile: CandidateProfile, version: JobVersion,
                          policy: F2ScoringPolicy, settings: Settings) -> tuple[str, dict[str, Any]]:
    # Most CVs fit in this bound; smaller requests avoid remote token-window exhaustion.
    cv_text = profile.extracted_text[:20_000]
    digest = hashlib.sha256(json.dumps({"cv": cv_text, "profile": profile.profile,
        "job": version.profile, "weights": policy.weights, "version": SCORING_VERSION,
        "model": settings.llm_model, "prompt_key": F2_SCORING.key,
        "prompt_version": F2_SCORING.version},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    state = {"job_profile": version.profile, "candidate_profile": profile.profile,
             "cv_text": cv_text, "weights": policy.weights, "input_hash": digest}
    return digest, state


def score_record(application_id: uuid.UUID, item_id: uuid.UUID, policy: F2ScoringPolicy,
                 settings: Settings, result: dict[str, Any], input_hash: str) -> CandidateScore:
    return CandidateScore(application_id=application_id, batch_item_id=item_id, policy_id=policy.id,
        policy_version=policy.version, weights=policy.weights.copy(), dimensions=result["dimensions"],
        aggregate_score=result["aggregate_score"], model_name=settings.llm_model,
        scoring_version=SCORING_VERSION, input_hash=input_hash,
        prompt_key=result["prompt_key"], prompt_version=result["prompt_version"])
