"""F5 batch eligibility, immutable transcript snapshots, correction audit, and review decisions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, ApplicationEvent, Candidate, CandidateProfile, F5Batch, F5Handoff, F5Run, InterviewTurn, Job
from app.services.f5_handoff import latest_transcript_session


async def ready_roster(session: AsyncSession, owner_id: uuid.UUID, job_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Return only handed-off applications whose latest completed interview has turns."""
    query = (select(Application, Candidate, Job, CandidateProfile).join(Candidate, Candidate.id == Application.candidate_id)
        .join(Job, Job.id == Application.job_id).join(F5Handoff, F5Handoff.application_id == Application.id)
        .outerjoin(CandidateProfile, CandidateProfile.application_id == Application.id)
        .where(Job.owner_id == owner_id, Application.stage != "rejected")
        .order_by(Job.public_code, Application.candidate_number))
    if job_id:
        query = query.where(Job.id == job_id)
    rows = (await session.execute(query)).all()
    result = []
    for application, candidate, job, profile in rows:
        interview = await latest_transcript_session(session, application.id)
        if interview is None:
            continue
        current = await session.scalar(select(F5Run).where(F5Run.application_id == application.id)
            .order_by(F5Run.created_at.desc(), F5Run.id.desc()).limit(1))
        parsed_name = (profile.profile or {}).get("candidate_name") if profile else None
        display_name = parsed_name if candidate.full_name.lower().startswith("candidate") and isinstance(parsed_name, str) and parsed_name.strip() else candidate.full_name
        result.append({"application_id": str(application.id), "candidate_name": display_name,
            "candidate_number": application.candidate_number, "job_id": str(job.id), "job_code": job.public_code,
            "job_title": job.title, "interview_id": str(interview.id),
            "analysis_status": "outdated" if current and current.interview_id != interview.id else current.status if current else "ready",
            "run_id": str(current.id) if current else None})
    return result


async def start_batch(session: AsyncSession, owner_id: uuid.UUID, application_ids: list[uuid.UUID],
                      thread_id: uuid.UUID | None = None) -> F5Batch:
    ids = list(dict.fromkeys(application_ids))
    if not ids or len(ids) > 20:
        raise HTTPException(422, "Select between 1 and 20 candidates for F5 analysis.")
    rows = (await session.execute(select(Application, Job).join(Job, Job.id == Application.job_id)
        .join(F5Handoff, F5Handoff.application_id == Application.id)
        .where(Application.id.in_(ids), Job.owner_id == owner_id, Application.stage != "rejected"))).all()
    if len(rows) != len(ids):
        raise HTTPException(409, "A selected candidate is not ready for F5.")
    batch = F5Batch(owner_id=owner_id, thread_id=thread_id)
    session.add(batch)
    await session.flush()
    for application, _ in rows:
        interview = await latest_transcript_session(session, application.id)
        if interview is None:
            raise HTTPException(409, "A selected candidate has no completed interview transcript.")
        current = await session.scalar(select(F5Run).where(F5Run.application_id == application.id,
            F5Run.interview_id == interview.id, F5Run.status.in_(["queued", "processing", "completed"]))
            .order_by(F5Run.created_at.desc()).limit(1))
        if current:
            raise HTTPException(409, "A selected candidate already has a current F5 run. Use Request reanalysis to rerun it.")
        turns = list(await session.scalars(select(InterviewTurn).where(InterviewTurn.session_id == interview.id)
            .order_by(InterviewTurn.started_at, InterviewTurn.id)))
        snapshot = [{"id": str(t.id), "role": t.role, "text": t.text,
                     "started_at": t.started_at.isoformat(), "ended_at": t.ended_at.isoformat()} for t in turns]
        session.add(F5Run(batch_id=batch.id, application_id=application.id, interview_id=interview.id,
                          job_version=application.job_version, transcript=snapshot))
    await session.commit()
    return batch


async def owned_run(session: AsyncSession, run_id: uuid.UUID, owner_id: uuid.UUID) -> F5Run:
    run = await session.scalar(select(F5Run).join(F5Batch, F5Batch.id == F5Run.batch_id)
                               .where(F5Run.id == run_id, F5Batch.owner_id == owner_id))
    if run is None:
        raise HTTPException(404, "F5 analysis not found.")
    return run


async def correct_transcript(session: AsyncSession, run: F5Run, turn_id: str, text: str, actor_id: uuid.UUID) -> None:
    """Correct a snapshotted turn without changing the original interview transcript."""
    if run.status == "processing":
        raise HTTPException(409, "Wait for analysis to finish before correcting the transcript.")
    transcript = [dict(turn) for turn in run.transcript]
    target = next((turn for turn in transcript if turn["id"] == turn_id), None)
    if target is None:
        raise HTTPException(404, "Transcript turn not found in this F5 run.")
    before = target["text"]
    target["text"] = text.strip()
    run.transcript = transcript
    run.corrections = [*run.corrections, {"turn_id": turn_id, "before": before, "after": target["text"],
                                            "actor_id": str(actor_id), "at": datetime.now(timezone.utc).isoformat()}]
    run.result, run.status, run.review_status, run.error = None, "queued", "pending", None
    run.attempts = 0
    await session.commit()


async def correct_grouping(session: AsyncSession, run: F5Run, groups: list[dict[str, Any]], actor_id: uuid.UUID) -> None:
    """Save explicit Q&A groupings as turn-index overrides and rerun with an audit entry."""
    if run.status == "processing":
        raise HTTPException(409, "Wait for analysis to finish before correcting Q&A grouping.")
    for group in groups:
        questions = group.get("question_turns", [])
        answers = group.get("answer_turns", [])
        if not questions or any(not isinstance(i, int) or i < 0 or i >= len(run.transcript) or
                                run.transcript[i]["role"] != "interviewer" for i in questions):
            raise HTTPException(422, "Each Q&A group needs valid interviewer turn indexes.")
        if any(not isinstance(i, int) or i < 0 or i >= len(run.transcript) or
               run.transcript[i]["role"] != "candidate" for i in answers):
            raise HTTPException(422, "A Q&A group contains an invalid candidate turn index.")
    run.corrections = [*run.corrections, {"type": "grouping", "before": run.segment_overrides,
        "after": groups, "actor_id": str(actor_id), "at": datetime.now(timezone.utc).isoformat()}]
    run.segment_overrides = groups
    run.result, run.status, run.review_status, run.error, run.attempts = None, "queued", "pending", None, 0
    await session.commit()


async def review_run(session: AsyncSession, run: F5Run, decision: str, reason: str, actor_id: uuid.UUID) -> None:
    if run.status != "completed":
        raise HTTPException(409, "F5 analysis must finish before review.")
    latest = await latest_transcript_session(session, run.application_id)
    if latest is None or latest.id != run.interview_id:
        raise HTTPException(409, "A newer interview exists. Analyse its transcript before review.")
    if run.review_status != "pending":
        raise HTTPException(409, "This F5 result has already been reviewed.")
    if decision == "reanalyse":
        run.status, run.result, run.review_status, run.error, run.attempts = "queued", None, "pending", None, 0
    elif decision in {"approve", "reject_candidate"}:
        run.review_status = "approved" if decision == "approve" else "candidate_rejected"
        application = await session.get(Application, run.application_id)
        if application:
            session.add(ApplicationEvent(application_id=application.id, actor_id=actor_id,
                action="f5_approved" if decision == "approve" else "f5_rejected",
                from_stage=application.stage, to_stage="rejected" if decision == "reject_candidate" else application.stage,
                reason=reason or "Recruiter approved F5 Q&A analysis for F6."))
        if decision == "reject_candidate":
            if application:
                application.rejected_from_stage = application.stage
                application.stage, application.rejection_action = "rejected", "rejected"
                application.rejection_reason, application.rejected_at, application.rejected_by = reason, datetime.now(timezone.utc), actor_id
    else:
        raise HTTPException(422, "Unknown F5 review decision.")
    run.review_reason, run.reviewed_by, run.reviewed_at = reason, actor_id, datetime.now(timezone.utc)
    await session.commit()
