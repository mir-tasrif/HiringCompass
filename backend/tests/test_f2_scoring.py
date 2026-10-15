"""Deterministic F2 aggregation and legacy job-rubric mapping."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import Settings
from app.graphs.f2_scoring.f2_scoring_graph import DIMENSIONS, aggregate
from app.services.f2_policy import validate_weights, weights_from_rubric
from app.services.f2_scoring_results import prepare_scoring_input, score_record
from app.prompts.system import SystemPrompt


def test_weighted_aggregate_keeps_missing_dimension_in_denominator():
    weights = dict(zip(DIMENSIONS, (40, 30, 10, 20)))
    dimensions = [{"key": key, "score": score} for key, score in zip(DIMENSIONS, (90, 80, 0, 75))]
    assert aggregate(dimensions, weights) == 75.0


def test_legacy_rubric_maps_to_four_scoring_dimensions():
    rubric = {"dimensions": [
        {"key": "technical_skills", "weight": 0.40},
        {"key": "relevant_experience", "weight": 0.30},
        {"key": "qualifications", "weight": 0.20},
        {"key": "job_match", "weight": 0.10},
    ]}
    result = weights_from_rubric(rubric)
    assert set(result) == set(DIMENSIONS)
    assert sum(result.values()) == 100
    assert result["certifications_soft_skills"] >= 10


def test_policy_rejects_invalid_weight_total():
    try:
        validate_weights(dict(zip(DIMENSIONS, (40, 30, 20, 20))))
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid 110% weights were accepted")


def test_f2_result_builder_tracks_prompt_version():
    settings = Settings(_env_file=None)
    profile = SimpleNamespace(extracted_text="Python project", profile={"skills": ["Python"]})
    version = SimpleNamespace(profile={"title": "Developer"})
    policy = SimpleNamespace(id=uuid.uuid4(), version=1, weights=dict(zip(DIMENSIONS, (40, 30, 10, 20))))
    digest, state = prepare_scoring_input(profile, version, policy, settings)
    assert state["input_hash"] == digest
    with patch("app.services.f2_scoring_results.F2_SCORING", SystemPrompt("f2.scoring", "2", "Updated")):
        revised_digest, _ = prepare_scoring_input(profile, version, policy, settings)
    assert revised_digest != digest
    row = score_record(uuid.uuid4(), uuid.uuid4(), policy, settings,
        {"dimensions": [], "aggregate_score": 0.0, "prompt_key": "f2.scoring", "prompt_version": "1"}, digest)
    assert (row.prompt_key, row.prompt_version, row.input_hash) == ("f2.scoring", "1", digest)
