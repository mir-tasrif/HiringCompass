"""One independently checkpointed F2 assessment per CV batch item."""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import PermanentError
from app.prompts.user_messages import f2_scoring_message
from app.prompts.system import F2_SCORING
from app.graphs.orchestrator import GraphRegistry, GraphSpec
from app.schemas.contracts import WorkKind

DIMENSIONS = ("technical_projects", "relevant_experience", "certifications_soft_skills", "education_qualifications")
SCORING_VERSION = "f2-v2"


class DimensionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    score: int = Field(ge=0, le=100)
    evidence: str
    justification: str
    missing: bool


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimensions: list[DimensionResult]


SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"dimensions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "properties": {"key": {"type": "string"}, "score": {"type": "integer", "minimum": 0, "maximum": 100},
                       "evidence": {"type": "string"}, "justification": {"type": "string"}, "missing": {"type": "boolean"}},
        "required": ["key", "score", "evidence", "justification", "missing"]}}},
    "required": ["dimensions"],
}


class ScoringState(TypedDict, total=False):
    input_hash: str
    job_profile: dict[str, Any]
    candidate_profile: dict[str, Any]
    cv_text: str
    weights: dict[str, int]
    dimensions: list[dict[str, Any]]
    aggregate_score: float
    prompt_key: str
    prompt_version: str


def aggregate(dimensions: list[dict[str, Any]], weights: dict[str, int]) -> float:
    """Keep missing dimensions in the denominator and round only the final score."""
    total = sum(Decimal(weights[entry["key"]]) * Decimal(entry["score"]) for entry in dimensions)
    return float((total / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _evidence_words(value: str) -> list[str]:
    """Treat OCR spacing, punctuation, and Unicode presentation forms alike."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"\w+", normalized, flags=re.UNICODE)


def verified_cv_evidence(cv_text: str, quote: str) -> str | None:
    """Return a source-backed passage, or None when the model's claim is unverified."""
    quote_words = _evidence_words(quote[:600])
    source_words = _evidence_words(cv_text)
    if not quote_words or not source_words:
        return None
    source = " ".join(source_words)
    quoted = " ".join(quote_words)
    if quoted in source:
        return quoted
    if len(quote_words) < 3:
        return None
    best_ratio, best_span = 0.0, ""
    size = len(quote_words)
    quote_set = set(quote_words)
    for window_size in {max(3, size - 2), size, size + 2}:
        for start in range(max(0, len(source_words) - window_size + 1)):
            words = source_words[start:start + window_size]
            if len(quote_set.intersection(words)) < max(2, len(quote_set) // 2):
                continue
            span = " ".join(words)
            ratio = SequenceMatcher(None, quoted, span, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio, best_span = ratio, span
    return best_span if best_ratio >= 0.86 else None


def build_scoring_graph(llm: Any, checkpointer: Any):
    async def assess(state: ScoringState) -> dict[str, Any]:
        prompt = f2_scoring_message(state["job_profile"], state["candidate_profile"], state["cv_text"], DIMENSIONS)
        messages = [{"role": "system", "content": F2_SCORING.text},
                    {"role": "user", "content": prompt}]
        for attempt in range(2):
            raw = await llm.chat(messages, schema=SCHEMA, temperature=0, seed=42)
            try:
                parsed = Assessment.model_validate(json.loads(raw))
                by_key = {entry.key: entry for entry in parsed.dimensions if entry.key in DIMENSIONS}
                if not by_key:
                    raise ValueError("The model returned no recognized scoring dimensions")
                result = []
                unmatched: list[str] = []
                for key in DIMENSIONS:
                    entry = by_key.get(key)
                    if entry is None or entry.missing:
                        if entry is None:
                            unmatched.append(key)
                        result.append({"key": key, "score": 0, "evidence": "",
                                       "justification": "No verifiable CV evidence for this dimension.", "missing": True})
                    else:
                        source_passage = verified_cv_evidence(state["cv_text"], entry.evidence)
                        if source_passage is None:
                            unmatched.append(key)
                            result.append({"key": key, "score": 0, "evidence": "",
                                           "justification": "The AI's claimed evidence could not be verified in the CV. Review the document before scoring this dimension.",
                                           "missing": True})
                        else:
                            result.append({"key": key, "score": entry.score, "evidence": source_passage[:1000],
                                           "justification": entry.justification[:2000], "missing": False})
                if unmatched and attempt == 0:
                    raise ValueError("Unverified evidence for: " + ", ".join(unmatched))
                return {"dimensions": result, "prompt_key": F2_SCORING.key,
                        "prompt_version": F2_SCORING.version}
            except (ValueError, TypeError, ValidationError) as exc:
                if attempt:
                    raise PermanentError("The AI returned invalid F2 scoring evidence after a repair attempt.") from exc
                messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content":
                    "Repair the JSON. Return the four required keys exactly once in order, with exact CV evidence for nonzero scores."}])
        raise PermanentError("No valid F2 assessment was returned.")

    async def calculate(state: ScoringState) -> dict[str, Any]:
        return {"aggregate_score": aggregate(state["dimensions"], state["weights"])}

    graph = StateGraph(ScoringState)
    graph.add_node("assess_evidence", assess)
    graph.add_node("calculate_weighted_score", calculate)
    graph.add_edge(START, "assess_evidence")
    graph.add_edge("assess_evidence", "calculate_weighted_score")
    graph.add_edge("calculate_weighted_score", END)
    return graph.compile(checkpointer=checkpointer)


def register_scoring_graph(registry: GraphRegistry, llm: Any) -> None:
    """Expose F2 through the same graph registry used by job intelligence."""
    registry.register(GraphSpec(WorkKind.SCORING, lambda checkpointer: build_scoring_graph(llm, checkpointer)))
