"""Deterministic F2 aggregation and legacy job-rubric mapping."""

from app.graphs.f2_scoring.graph import DIMENSIONS, aggregate
from app.services.f2_policy import validate_weights, weights_from_rubric


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
