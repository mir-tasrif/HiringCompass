"""F5 graph checks transcript provenance, follow-up grouping, gaps, and provisional scoring."""

import asyncio
import json
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from langgraph.checkpoint.memory import MemorySaver

from app.graphs.f5_interview_analysis.graph import build_f5_graph


class FakeLLM:
    async def chat(self, messages, *, schema=None, temperature=None, seed=None):
        if schema.get("title") == "Segments":
            return json.dumps({"segments": [
                {"question_turns": [0], "answer_turns": [1], "relation": "question", "evaluable": True, "requirement_indexes": [0]},
                {"question_turns": [2], "answer_turns": [3], "relation": "follow_up", "evaluable": True, "requirement_indexes": [0]},
                {"question_turns": [4], "answer_turns": [], "relation": "question", "evaluable": False, "requirement_indexes": []},
            ]})
        return json.dumps({"relevance": 80, "technical_accuracy": 80, "completeness": 80, "articulation": 80,
            "justification": "The answer explains a Python list.", "answer_evidence": "A list can change",
            "reference_quotes": [{"id": "abc", "quote": "Python lists are mutable ordered sequences."}]})


class FakeStore:
    def __init__(self, title):
        self.title = title

    async def search(self, session, *, namespace, query, top_k):
        return [SimpleNamespace(chunk_id="abc", title=self.title, source_uri="file:reference.md", version="1",
                                content="Python lists are mutable ordered sequences.", score=0.9)] if self.title else []


@asynccontextmanager
async def fake_session():
    yield object()


class TestF5Graph(unittest.TestCase):
    def _run(self, title):
        transcript = [{"id": str(uuid.uuid4()), "role": role, "text": text} for role, text in [
            ("interviewer", "What is a Python list?"), ("candidate", "A list can change."),
            ("interviewer", "Can you clarify?"), ("candidate", "A list can change after creation."),
            ("interviewer", "How are you today?")]]
        graph = build_f5_graph(FakeLLM(), FakeStore(title), fake_session, "technical_docs", MemorySaver())
        return asyncio.run(graph.ainvoke({"transcript": transcript, "requirements": [
            {"text": "Python", "kind": "mandatory"}, {"text": "SQL", "kind": "preferred"}]},
            {"configurable": {"thread_id": f"f5-test-{uuid.uuid4()}"}}))

    def test_approved_reference_and_coverage(self):
        result = self._run("Recruiter approved Python guide")
        self.assertEqual(result["overall_score"], 80)
        self.assertFalse(result["provisional"])
        self.assertEqual([row["status"] for row in result["coverage"]], ["explored", "not_explored"])
        self.assertEqual(result["evaluations"][1]["relation"], "follow_up")
        self.assertIsNone(result["evaluations"][2]["scores"])
        self.assertEqual(len(result["evaluations"][0]["answer_turn_ids"]), 1)

    def test_no_approved_reference_keeps_overall_provisional(self):
        result = self._run("F5 Python Basics Sample")
        self.assertEqual(result["overall_score"], 80)
        self.assertTrue(result["provisional"])
