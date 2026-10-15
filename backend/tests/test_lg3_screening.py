"""F1 graph evaluates CV evidence without placing raw CV text in checkpoints."""

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver

from app.db.models import Application, Job
from app.graphs.lg3_screening.f1_screening_graph import build_screening_graph, register_screening_graph
from app.graphs.orchestrator import GraphRegistry
from app.schemas.contracts import WorkKind
from app.services.f1_screening_results import save_screening_result


class FakeSession:
    def __init__(self, application, job, profile, version):
        self.application, self.job, self.profile, self.version = application, job, profile, version

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, model, _id):
        return self.application if model is Application else self.job if model is Job else None

    async def scalar(self, query):
        model = query.column_descriptions[0]["entity"]
        return self.profile if model.__name__ == "CandidateProfile" else self.version


class FakeStore:
    async def search(self, *_args, **_kwargs):
        return []


class FakeLLM:
    async def chat(self, messages, *, schema):
        assert schema and "CV evidence" in messages[1]["content"]
        return json.dumps({"suitability_score": 63, "explanation": "Partial CV evidence",
            "requirements": [{"text": "Python", "kind": "mandatory", "status": "unmet",
                "evidence": "", "explanation": "Not shown in the CV"}]})


class TestLG3Screening(unittest.TestCase):
    def test_screening_result_persists_prompt_provenance(self):
        application = SimpleNamespace(id=uuid.uuid4(), stage="ranking")

        class SaveSession:
            def __init__(self):
                self.saved = []

            async def scalar(self, _query):
                return application

            def add(self, row):
                self.saved.append(row)

        session = SaveSession()
        result = {"job_id": str(uuid.uuid4()), "job_version": 1, "suitability_score": 63,
                  "mandatory_pass": False, "requirements": [], "explanation": "Missing evidence",
                  "prompt_key": "cv.screening", "prompt_version": "1"}
        screening = asyncio.run(save_screening_result(session, application, result))
        self.assertEqual((screening.prompt_key, screening.prompt_version), ("cv.screening", "1"))
        self.assertEqual(application.stage, "f1_review")

    def test_screening_graph_keeps_raw_cv_out_of_checkpoint(self):
        application_id, job_id = uuid.uuid4(), uuid.uuid4()
        application = SimpleNamespace(id=application_id, job_id=job_id, job_version=1)
        job = SimpleNamespace(id=job_id, title="Engineer")
        profile = SimpleNamespace(profile={"skills": ["SQL"]}, extracted_text="PRIVATE CV EVIDENCE")
        version = SimpleNamespace(version=1, profile={"requirements": [{"text": "Python", "kind": "mandatory"}]})
        session = FakeSession(application, job, profile, version)
        registry = GraphRegistry()
        register_screening_graph(registry, lambda: session, SimpleNamespace(rag_namespace_company="company"),
                                 FakeLLM(), FakeStore())
        checkpointer = MemorySaver()

        async def run():
            graph = registry.get(WorkKind.APPLICATION_SCREENING, checkpointer)
            return await graph.ainvoke({"application_id": str(application_id)},
                {"configurable": {"thread_id": "f1-test"}})

        result = asyncio.run(run())
        self.assertFalse(result["mandatory_pass"])
        self.assertEqual(result["suitability_score"], 63)
        self.assertEqual(result["prompt_key"], "cv.screening")
        self.assertNotIn("PRIVATE CV EVIDENCE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
