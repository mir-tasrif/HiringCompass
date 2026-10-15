"""Generate interview guide questions and answer the recruiter's private in-call requests."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (Application, CandidateProfile, CandidateScore, CandidateScreening,
                           InterviewSession, InterviewTurn, JobVersion)
from app.llm.client import get_llm_client
from app.prompts.system import INTERVIEW_ASSISTANT, INTERVIEW_QUESTIONS


async def generate_questions(session: AsyncSession, application: Application,
                             avoid_questions: list[str] | None = None) -> list[dict]:
    """Ground short questions in the active job, parsed CV, and F1/F2 evidence."""
    version = await session.scalar(select(JobVersion).where(JobVersion.job_id == application.job_id,
                                                            JobVersion.version == application.job_version))
    profile = await session.scalar(select(CandidateProfile).where(CandidateProfile.application_id == application.id))
    screening = await session.scalar(select(CandidateScreening).where(CandidateScreening.application_id == application.id))
    score = await session.scalar(select(CandidateScore).where(CandidateScore.application_id == application.id)
                                 .order_by(CandidateScore.created_at.desc()).limit(1))
    context = {"job": version.profile if version else {}, "cv_profile": profile.profile if profile else {},
               "f1": screening.requirements if screening else [], "f2": score.dimensions if score else [],
               "avoid_questions": avoid_questions or []}
    excluded = {" ".join(value.lower().split()).rstrip("?.! ") for value in (avoid_questions or [])}
    schema = {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
              "required": ["questions"], "additionalProperties": False}
    try:
        raw = await get_llm_client().chat([
            {"role": "system", "content": INTERVIEW_QUESTIONS.text},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)[:24_000]},
        ], schema=schema, temperature=0.25, seed=uuid.uuid4().int & 0x7FFFFFFF)
        values = json.loads(raw).get("questions", [])
        questions = [str(value).strip() for value in values if isinstance(value, str) and value.strip()
                     and len(value.split()) <= 16 and len(value) <= 120
                     and " ".join(value.lower().split()).rstrip("?.! ") not in excluded][:5]
        if len(questions) >= 3:
            return [{"text": value, "source": "job_and_cv"} for value in questions]
    except Exception:
        pass
    title = str((version.profile if version else {}).get("title") or "this role")
    skills = (profile.profile if profile else {}).get("skills") or []
    first_skill = str(skills[0])[:40] if isinstance(skills, list) and skills else "a required skill"
    fallback = [f"Which project best shows your fit for {title[:55]}?",
                f"How have you used {first_skill} in a project?",
                "What was one challenge in that project?",
                "How do you test your work before sharing it?",
                "What would you improve next in that project?"]
    fresh_fallback = [question for question in fallback if
                      " ".join(question.lower().split()).rstrip("?.! ") not in excluded]
    chosen: list[str] = []
    chosen_keys: set[str] = set()
    for question in fresh_fallback + fallback:
        key = " ".join(question.lower().split()).rstrip("?.! ")
        if key not in chosen_keys:
            chosen.append(question)
            chosen_keys.add(key)
        if len(chosen) == 3:
            break
    return [{"text": value, "source": "fallback"} for value in chosen]


async def answer_recruiter(session: AsyncSession, record: InterviewSession, question: str,
                           runtime: object, settings: Settings) -> str:
    """Answer from interview context; knowledge-search failure must not stop the live call."""
    application = await session.get(Application, record.application_id)
    version = await session.scalar(select(JobVersion).where(JobVersion.job_id == record.job_id,
                                                            JobVersion.version == application.job_version))
    profile = await session.scalar(select(CandidateProfile).where(CandidateProfile.application_id == application.id))
    turns = list(await session.scalars(select(InterviewTurn).where(InterviewTurn.session_id == record.id)
                                     .order_by(InterviewTurn.started_at.desc()).limit(80)))
    knowledge: list[dict[str, str]] = []
    try:
        hits = await runtime.deps.store.search(session, namespace=settings.rag_namespace_company,
            query=f"{version.profile.get('title', '') if version else ''} {question}", top_k=4)
        knowledge = [{"title": hit.title, "content": hit.content[:1000]} for hit in hits]
    except Exception:
        pass
    context = {"job": version.profile if version else {}, "candidate": profile.profile if profile else {},
               "company_knowledge": knowledge, "questions": record.questions,
               "transcript": [{"role": turn.role, "time": turn.started_at.isoformat(), "text": turn.text}
                              for turn in reversed(turns)]}
    return await get_llm_client().chat([
        {"role": "system", "content": INTERVIEW_ASSISTANT.text},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)[:35_000] + "\nRecruiter asks: " + question},
    ], temperature=0, seed=42)
