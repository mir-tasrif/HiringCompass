"""Read-only candidate pipeline view with F11, F1, F2, batch status, and F5 readiness."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.candidates import CandidateCvOut, CandidateEventOut
from app.db.models import (Application, ApplicationEvent, Candidate, CandidateProfile, CandidateScreening,
                           CandidateScore, CvBatch, CvBatchItem, Document, F5Handoff, Job)
from app.services.f5_handoff import latest_transcript_session


async def list_candidate_cvs(session: AsyncSession, owner_id: uuid.UUID,
                             section: str) -> list[CandidateCvOut]:
    """Project stored pipeline evidence into the recruiter-facing candidate card contract."""
    statement = (select(Application, Candidate, Document, Job, CandidateProfile, CandidateScreening)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Document, Document.application_id == Application.id)
        .join(Job, Job.id == Application.job_id)
        .outerjoin(CandidateProfile, CandidateProfile.application_id == Application.id)
        .outerjoin(CandidateScreening, CandidateScreening.application_id == Application.id)
        .where(Job.owner_id == owner_id, Job.active_version.is_not(None)))
    if section == "uploaded":
        statement = statement.where(Application.stage == "uploaded")
    elif section == "integrity":
        statement = statement.where(or_(
            Application.stage == "integrity_check",
            CandidateProfile.integrity_verdict.is_not(None),
            and_(Application.stage == "rejected", Application.rejected_from_stage == "integrity_check"),
        ))
    elif section == "parsed":
        statement = statement.where(Application.stage == "parsed")
    elif section == "ranking":
        statement = statement.where(or_(Application.stage == "ranking",
            and_(CandidateScreening.id.is_not(None), CandidateScreening.mandatory_pass.is_(True))))
    elif section == "scoring":
        statement = statement.where(Application.stage.in_(["scoring", "scoring_processing", "f2_review", "interview_selection"]))
    elif section == "review":
        statement = statement.where(Application.stage.in_(["integrity_review", "f1_review", "f2_review"]))
    elif section == "rejected":
        statement = statement.where(Application.stage == "rejected")
    elif section == "f5":
        statement = statement.join(F5Handoff, F5Handoff.application_id == Application.id)
    else:
        target = {"interview": "interview_selection", "assessment": "assessed"}[section]
        statement = statement.where(Application.stage == target)
    if section == "ranking":
        statement = statement.order_by(Job.public_code.asc(), CandidateScreening.rank.asc().nulls_last(),
                                       CandidateScreening.suitability_score.desc(), Application.created_at.desc())
    else:
        statement = statement.order_by(Application.created_at.desc(), Application.id)
    rows = (await session.execute(statement)).all()
    if not rows:
        return []
    ids = [application.id for application, *_ in rows]
    event_rows = (await session.execute(select(ApplicationEvent).where(ApplicationEvent.application_id.in_(ids))
                    .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id))).scalars().all()
    task_rows = (await session.execute(select(CvBatchItem).join(CvBatch, CvBatch.id == CvBatchItem.batch_id)
                    .where(CvBatchItem.application_id.in_(ids), CvBatch.kind == "integrity_parse")
                    .order_by(CvBatchItem.created_at.desc(), CvBatchItem.id))).scalars().all()
    integrity_tasks: dict[uuid.UUID, CvBatchItem] = {}
    for task in task_rows:
        integrity_tasks.setdefault(task.application_id, task)
    scoring_rows = (await session.execute(select(CvBatchItem).join(CvBatch, CvBatch.id == CvBatchItem.batch_id)
                    .where(CvBatchItem.application_id.in_(ids), CvBatch.kind == "scoring")
                    .order_by(CvBatchItem.created_at.desc(), CvBatchItem.id.desc()))).scalars().all()
    scoring_tasks: dict[uuid.UUID, CvBatchItem] = {}
    for task in scoring_rows:
        scoring_tasks.setdefault(task.application_id, task)
    ranking_rows = (await session.execute(select(CvBatchItem).join(CvBatch, CvBatch.id == CvBatchItem.batch_id)
                    .where(CvBatchItem.application_id.in_(ids), CvBatch.kind == "ranking")
                    .order_by(CvBatchItem.created_at.desc(), CvBatchItem.id.desc()))).scalars().all()
    ranking_tasks: dict[uuid.UUID, CvBatchItem] = {}
    for task in ranking_rows:
        ranking_tasks.setdefault(task.application_id, task)
    score_rows = list(await session.scalars(select(CandidateScore).where(CandidateScore.application_id.in_(ids))
        .order_by(CandidateScore.created_at.desc(), CandidateScore.id.desc())))
    current_scores: dict[uuid.UUID, CandidateScore] = {}
    for score in score_rows:
        current_scores.setdefault(score.application_id, score)
    events: dict[uuid.UUID, list[CandidateEventOut]] = {}
    for event in event_rows:
        events.setdefault(event.application_id, []).append(CandidateEventOut(action=event.action,
            from_stage=event.from_stage, to_stage=event.to_stage, reason=event.reason, created_at=event.created_at))
    result = []
    for application, candidate, document, job, parsed, screening in rows:
        f5_interview = await latest_transcript_session(session, application.id) if section == "f5" else None
        if section == "f5" and f5_interview is None:
            continue
        screening_data = None if screening is None else {
            "suitability_score": screening.suitability_score, "mandatory_pass": screening.mandatory_pass,
            "requirements": screening.requirements, "explanation": screening.explanation, "rank": screening.rank,
            "prompt_key": screening.prompt_key, "prompt_version": screening.prompt_version,
        }
        result.append(CandidateCvOut(id=application.id, job_id=job.id, job_code=job.public_code or "",
            job_title=job.title, job_version=application.job_version, candidate_number=application.candidate_number,
            candidate_name=candidate.full_name, candidate_email=candidate.email,
            f5_interview_id=f5_interview.id if f5_interview else None,
            f5_interview_ended_at=f5_interview.ended_at if f5_interview else None,
            source=application.source, original_filename=document.original_filename,
            converted_from_docx=document.status == "converted_from_docx", size_bytes=document.size_bytes,
            uploaded_at=document.created_at, stage=application.stage, rejection_action=application.rejection_action,
            rejection_reason=application.rejection_reason, rejected_from_stage=application.rejected_from_stage,
            rejected_at=application.rejected_at, events=events.get(application.id, []),
            parsed_profile=parsed.profile if parsed and parsed.profile else None,
            integrity_verdict=parsed.integrity_verdict if parsed else None,
            integrity_signals=parsed.integrity_signals if parsed else [],
            extraction_method=parsed.extraction_method if parsed else None, screening=screening_data,
            profile_prompt_key=parsed.profile_prompt_key if parsed else None,
            profile_prompt_version=parsed.profile_prompt_version if parsed else None,
            scoring=({"aggregate_score": current_scores[application.id].aggregate_score,
                      "dimensions": current_scores[application.id].dimensions,
                      "weights": current_scores[application.id].weights,
                      "policy_version": current_scores[application.id].policy_version,
                      "model_name": current_scores[application.id].model_name,
                      "prompt_key": current_scores[application.id].prompt_key,
                      "prompt_version": current_scores[application.id].prompt_version,
                      "scored_at": current_scores[application.id].created_at.isoformat()}
                     if application.id in current_scores else None),
            integrity_task_id=integrity_tasks[application.id].id if application.id in integrity_tasks else None,
            integrity_task_status=integrity_tasks[application.id].status if application.id in integrity_tasks else None,
            integrity_task_error=integrity_tasks[application.id].error if application.id in integrity_tasks else None,
            ranking_task_id=ranking_tasks[application.id].id if application.id in ranking_tasks else None,
            ranking_task_status=ranking_tasks[application.id].status if application.id in ranking_tasks else None,
            ranking_task_error=ranking_tasks[application.id].error if application.id in ranking_tasks else None,
            ranking_task_next_attempt_at=ranking_tasks[application.id].next_attempt_at if application.id in ranking_tasks else None,
            scoring_task_id=scoring_tasks[application.id].id if application.id in scoring_tasks else None,
            scoring_task_status=scoring_tasks[application.id].status if application.id in scoring_tasks else None,
            scoring_task_error=scoring_tasks[application.id].error if application.id in scoring_tasks else None,
            scoring_task_next_attempt_at=scoring_tasks[application.id].next_attempt_at if application.id in scoring_tasks else None))
    return result
