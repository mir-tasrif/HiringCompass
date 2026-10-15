"""Checkpointed F5 graph; every conclusion retains transcript and reference provenance."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.llm.structured import generate_structured, wrap_untrusted
from app.prompts.system import F5_EVALUATE, F5_SEGMENT


class Segment(BaseModel):
    question_turns: list[int]
    answer_turns: list[int]
    relation: str = "question"
    parent_question_turn: int | None = None
    evaluable: bool = True
    requirement_indexes: list[int] = Field(default_factory=list)


class Segments(BaseModel):
    segments: list[Segment]


class AnswerScores(BaseModel):
    relevance: int = Field(ge=0, le=100)
    technical_accuracy: int = Field(ge=0, le=100)
    completeness: int = Field(ge=0, le=100)
    articulation: int = Field(ge=0, le=100)
    justification: str
    answer_evidence: str
    reference_quotes: list[dict[str, str]] = Field(default_factory=list)


class F5State(TypedDict, total=False):
    transcript: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    segment_overrides: list[dict[str, Any]]
    segments: list[dict[str, Any]]
    evaluations: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    overall_score: float | None
    provisional: bool
    prompt_versions: dict[str, str]


def _valid_indexes(indexes: list[int], transcript: list[dict[str, Any]], role: str) -> list[int]:
    return list(dict.fromkeys(i for i in indexes if 0 <= i < len(transcript) and transcript[i]["role"] == role))


def build_f5_graph(llm: Any, store: Any, session_factory: Any, namespace: str, checkpointer: Any = None) -> Any:
    """Construct an independent graph; the durable worker invokes it per F5 run."""
    async def segment(state: F5State) -> dict[str, Any]:
        transcript = state["transcript"]
        if state.get("segment_overrides"):
            batches = [(0, Segments.model_validate({"segments": state["segment_overrides"]}))]
        else:
            batches = []
            # Overlapping windows keep long interviews within the model's context while preserving boundary Q&A.
            for start in range(0, len(transcript), 50):
                if start and start + 10 >= len(transcript):
                    break
                window = transcript[start:start + 60]
                raw = await generate_structured(llm, Segments, system=F5_SEGMENT.text,
                    user=wrap_untrusted("interview_turns", json.dumps([
                        {"index": i, "role": t["role"], "text": t["text"]} for i, t in enumerate(window)
                    ], ensure_ascii=False)) + "\n" + wrap_untrusted("job_requirements", json.dumps([
                        {"index": i, "text": r.get("text", "")} for i, r in enumerate(state["requirements"])
                    ], ensure_ascii=False)))
                batches.append((start, raw))
        by_question = {}
        for offset, batch in batches:
            window_size = len(transcript) if state.get("segment_overrides") else min(60, len(transcript) - offset)
            for item in batch.segments:
                questions = _valid_indexes([i + offset for i in item.question_turns if 0 <= i < window_size], transcript, "interviewer")
                answers = _valid_indexes([i + offset for i in item.answer_turns if 0 <= i < window_size], transcript, "candidate")
                if not questions:
                    continue
                entry = {"question_turns": questions, "answer_turns": answers,
                    "relation": item.relation if item.relation in {"question", "follow_up", "clarification", "multi_part"} else "question",
                    "parent_question_turn": item.parent_question_turn + offset if item.parent_question_turn is not None and
                        0 <= item.parent_question_turn < window_size else None,
                    "evaluable": item.evaluable and bool(answers),
                    "requirement_indexes": sorted({i for i in item.requirement_indexes if 0 <= i < len(state["requirements"])})}
                key = tuple(questions)
                if key not in by_question or len(answers) > len(by_question[key]["answer_turns"]):
                    by_question[key] = entry
        cleaned = sorted(by_question.values(), key=lambda entry: entry["question_turns"][0])
        return {"segments": cleaned, "prompt_versions": {"segment": F5_SEGMENT.version, "evaluate": F5_EVALUATE.version}}

    async def evaluate(state: F5State) -> dict[str, Any]:
        transcript, requirements = state["transcript"], state["requirements"]
        evaluations = []
        for segment in state["segments"]:
            question = " ".join(transcript[i]["text"] for i in segment["question_turns"])
            answer = " ".join(transcript[i]["text"] for i in segment["answer_turns"])
            entry = {**segment, "question": question, "answer": answer,
                     "question_turn_ids": [transcript[i]["id"] for i in segment["question_turns"]],
                     "answer_turn_ids": [transcript[i]["id"] for i in segment["answer_turns"]],
                     "question_started_at": transcript[segment["question_turns"][0]].get("started_at"),
                     "answer_started_at": transcript[segment["answer_turns"][0]].get("started_at") if segment["answer_turns"] else None}
            if not segment["evaluable"]:
                evaluations.append({**entry, "scores": None, "reason": "Small talk or no candidate answer."})
                continue
            try:
                async with session_factory() as db:
                    chunks = await store.search(db, namespace=namespace, query=question + " " + answer[:600], top_k=4)
            except Exception:
                chunks = []
            references = [{"id": str(c.chunk_id), "title": c.title, "uri": c.source_uri,
                           "version": c.version, "text": c.content[:1500]} for c in chunks if c.score >= 0.35]
            result = await generate_structured(llm, AnswerScores, system=F5_EVALUATE.text,
                user=wrap_untrusted("question", question) + "\n" + wrap_untrusted("candidate_answer", answer)
                + "\n" + wrap_untrusted("relevant_job_requirements", json.dumps([
                    requirements[i] for i in segment["requirement_indexes"]], ensure_ascii=False))
                + "\n" + wrap_untrusted("approved_references", json.dumps(references, ensure_ascii=False)))
            valid_references = [ref for ref in references if any(quote.get("id") == ref["id"] and
                len(quote.get("quote", "").strip()) >= 12 and quote["quote"].strip() in ref["text"]
                for quote in result.reference_quotes)]
            # The generated starter is useful for demonstrations but is not recruiter-approved authority.
            verified = any(not ref["title"].lower().startswith("f5 python basics sample") for ref in valid_references)
            scores = {key: getattr(result, key) for key in ("relevance", "technical_accuracy", "completeness", "articulation")}
            evaluations.append({**entry, "scores": scores, "overall_score": round(sum(scores.values()) / 4, 2),
                "provisional": not verified, "justification": result.justification,
                "answer_evidence": result.answer_evidence if result.answer_evidence.strip() in answer else answer[:300],
                "references": valid_references})
        return {"evaluations": evaluations}

    def coverage(state: F5State) -> dict[str, Any]:
        results = state["evaluations"]
        coverage_rows = []
        for index, requirement in enumerate(state["requirements"]):
            linked = [i for i, item in enumerate(results) if index in item["requirement_indexes"]]
            answered = [i for i in linked if results[i]["evaluable"]]
            if not linked:
                status = "not_explored"
            elif not answered:
                status = "asked_unanswered"
            elif any(results[i].get("scores", {}).get("completeness", 0) >= 70 for i in answered):
                status = "explored"
            else:
                status = "partly_explored"
            coverage_rows.append({"requirement": requirement.get("text", ""), "kind": requirement.get("kind", "preferred"),
                                  "status": status, "segment_indexes": linked})
        scored = [item for item in results if item.get("scores")]
        return {"coverage": coverage_rows,
                "overall_score": round(sum(item["overall_score"] for item in scored) / len(scored), 2) if scored else None,
                "provisional": any(item.get("provisional") for item in scored)}

    graph = StateGraph(F5State)
    graph.add_node("segment", segment)
    graph.add_node("evaluate", evaluate)
    graph.add_node("map_coverage", coverage)
    graph.add_edge(START, "segment")
    graph.add_edge("segment", "evaluate")
    graph.add_edge("evaluate", "map_coverage")
    graph.add_edge("map_coverage", END)
    return graph.compile(checkpointer=checkpointer)
