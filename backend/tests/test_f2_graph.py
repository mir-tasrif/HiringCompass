"""F2 graph evidence grounding and deterministic aggregate."""

import asyncio
import json
import unittest

from langgraph.checkpoint.memory import MemorySaver

from app.graphs.f2_scoring.graph import DIMENSIONS, build_scoring_graph, verified_cv_evidence


class FakeLLM:
    async def chat(self, messages, *, schema=None, temperature=None, seed=None):
        assert schema and temperature == 0 and seed == 42
        return json.dumps({"dimensions": [
            {"key": DIMENSIONS[0], "score": 90, "evidence": "Python API project", "justification": "Direct project evidence.", "missing": False},
            {"key": DIMENSIONS[1], "score": 80, "evidence": "two years of work", "justification": "Relevant experience.", "missing": False},
            {"key": DIMENSIONS[2], "score": 0, "evidence": "", "justification": "No evidence.", "missing": True},
            {"key": DIMENSIONS[3], "score": 75, "evidence": "BSc Computer Science", "justification": "Relevant degree.", "missing": False},
        ]})


class UnverifiedEvidenceLLM:
    async def chat(self, messages, *, schema=None, temperature=None, seed=None):
        return json.dumps({"dimensions": [
            {"key": key, "score": 90, "evidence": "Ten years leading Java teams",
             "justification": "Claimed experience.", "missing": False} for key in DIMENSIONS
        ]})


class TestF2Graph(unittest.TestCase):
    def test_ocr_spacing_and_punctuation_do_not_reject_real_evidence(self):
        self.assertIsNotNone(verified_cv_evidence("Built Python-based APIs\nfor customer projects.",
                                                  "Built Python based APIs for customer projects"))

    def test_unrelated_claim_is_not_accepted_as_cv_evidence(self):
        self.assertIsNone(verified_cv_evidence("Python and SQL projects", "Ten years leading Java teams"))

    def test_unverified_model_claims_do_not_fail_entire_candidate(self):
        async def run():
            graph = build_scoring_graph(UnverifiedEvidenceLLM(), MemorySaver())
            return await graph.ainvoke({"job_profile": {"title": "Engineer"}, "candidate_profile": {},
                "cv_text": "Python and SQL projects", "weights": dict(zip(DIMENSIONS, (40, 30, 10, 20)))},
                {"configurable": {"thread_id": "test-f2-unverified"}})
        result = asyncio.run(run())
        self.assertEqual(result["aggregate_score"], 0)
        self.assertTrue(all(dimension["missing"] for dimension in result["dimensions"]))

    def test_graph_scores_four_dimensions_and_keeps_missing_weight(self):
        async def run():
            graph = build_scoring_graph(FakeLLM(), MemorySaver())
            return await asyncio.wait_for(graph.ainvoke({"job_profile": {"title": "Engineer"}, "candidate_profile": {},
                "cv_text": "Python API project; two years of work; BSc Computer Science",
                "weights": dict(zip(DIMENSIONS, (40, 30, 10, 20)))},
                {"configurable": {"thread_id": "test-f2"}}), timeout=10)
        result = asyncio.run(run())
        self.assertEqual(result["aggregate_score"], 75.0)
        self.assertTrue(result["dimensions"][2]["missing"])


if __name__ == "__main__":
    unittest.main()
