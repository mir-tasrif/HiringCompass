"""LLM output contracts for job drafting (ids and versions are assigned by code, never by the model)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.contracts import RequirementKind, RubricDimension


# One requirement as proposed by the model.
class RequirementDraft(BaseModel):
    text: str = Field(min_length=3)
    kind: RequirementKind
    weight: float = Field(default=1.0, gt=0, le=10)
    min_years: float | None = Field(default=None, ge=0)
    equivalents: list[str] = Field(default_factory=list)


# Structured reading of the recruiter's prompt, including what is still missing.
class ExtractedCriteria(BaseModel):
    title: str = ""
    summary: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[RequirementDraft] = Field(default_factory=list)
    priorities: str = ""
    missing_info: list[str] = Field(default_factory=list)
    sufficient: bool


# Draft job profile before validation and id assignment.
class JobProfileDraft(BaseModel):
    title: str = Field(min_length=3)
    summary: str
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[RequirementDraft] = Field(min_length=1)


# Draft evaluation rubric before weight normalisation and validation.
class RubricDraft(BaseModel):
    dimensions: list[RubricDimension] = Field(min_length=1)