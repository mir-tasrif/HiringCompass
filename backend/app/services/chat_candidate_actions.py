"""Chat commands for starting and confirming F11, F1, and F2 CV work."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Application, ApplicationEvent, ChatMessage, ChatThread, CvBatch, CvBatchItem, Job
from app.services import jobs
from app.services.f2_batches import start_f2_batch
from app.services.f2_policy import suggested_weights, validate_weights


async def _handle_candidate_work_request(session: AsyncSession, thread: ChatThread, message: str,
                                         messages: list[ChatMessage] | None = None) -> tuple[str, dict[str, Any]] | None:
    """Start CV work for explicit labels or a clear all/available follow-up in the linked job chat."""
    text = message.strip()
    lowered = text.lower()
    previous_assistant = next((m for m in reversed((messages or [])[:-1]) if m.role == "assistant"), None)
    previous_payload = (previous_assistant.payload or {}) if previous_assistant else {}
    labels = [int(number) for number in re.findall(r"\bcandidate\s*0*(\d{1,6})\b", lowered)]
    integrity_request = bool(re.search(r"\b(?:integrity|parse|parsing|extract(?:ion)?)\b", lowered) and
                             re.search(r"\b(?:cv|cvs|resume|resumes|candidate|candidates)\b", lowered))
    ranking_request = bool(re.search(r"\b(?:rank|ranking|screen|screening|feature\s*1|f1)\b", lowered) and
                           re.search(r"\b(?:cv|cvs|resume|resumes|candidate|candidates)\b", lowered))
    if not integrity_request and not ranking_request and previous_payload.get("type") == "candidate_work":
        prior_kind = previous_payload.get("kind")
        if prior_kind == "integrity" and (labels or re.search(r"\b(?:start|proceed|yes|all|them|those)\b", lowered)):
            integrity_request = True
        elif prior_kind == "ranking" and (labels or re.search(r"\b(?:start|proceed|yes|all|them|those)\b", lowered)):
            ranking_request = True
    if not integrity_request and not ranking_request:
        return None
    all_requested = bool(re.search(r"\b(?:all|every|available|these|them|those)\b", lowered))
    job_code_match = re.search(r"\bjd\s*0*(\d{1,6})\b", lowered, re.I)
    job = await jobs.get_job(session, thread.job_id) if thread.job_id else None
    if job is not None and job.archived_at is not None and thread.state == "approved":
        return "This chat is linked to an expired job and is read-only. Start a new job chat to process CVs for an active job.", {"type": "text"}
    if job is not None and job.active_version is None:
        return (f"I found the job linked to this chat ({job.public_code or job.title}), but it has no active approved version recorded. "
                "Please check its status in Jobs; I won’t label it inactive without that status check.",
                {"type": "candidate_work", "route": "/jobs?section=approved", "job_id": str(job.id)})
    if job_code_match:
        code = f"JD{int(job_code_match.group(1)):03d}"
        explicit_job = await session.scalar(select(Job).where(Job.owner_id == thread.owner_id,
            Job.public_code == code, Job.active_version.is_not(None), Job.archived_at.is_(None)))
        if explicit_job is None:
            return f"I couldn't find active job {code}. Check its job code and try again.", {"type": "text"}
        if job is not None and job.id != explicit_job.id:
            return "This chat is linked to a different job. Open that job's chat or select its CVs in Candidates.", {"type": "text"}
        job = explicit_job
    if job is None:
        return ("Tell me the job code, such as JD101, and the candidate labels to process. You can also select up to 20 CVs in Candidates.",
                {"type": "candidate_work", "route": "/candidates"})
    if thread.state == "awaiting_decision":
        return "Finish reviewing the current job-description draft first. Then I can start CV work for an approved job.", {"type": "text"}
    if thread.job_id != job.id:
        thread.job_id, thread.title, thread.state = job.id, job.title, "approved"
    expected_stage = "uploaded" if integrity_request else "parsed"
    route = f"/candidates?jobId={job.id}&section={'uploaded' if integrity_request else 'parsed'}"
    if not labels and not all_requested:
        task = "integrity check and parsing" if integrity_request else "Feature 1 ranking"
        return (f"I can start {task} for {job.public_code}. Say ‘all uploaded CVs’ to process every eligible CV for this job, or name candidate labels (up to 20).",
                {"type": "candidate_work", "route": route, "job_id": str(job.id), "job_code": job.public_code,
                 "kind": "integrity" if integrity_request else "ranking",
                 "auto_start_ranking": bool(integrity_request and ranking_request)})
    if len(labels) > 20:
        return "A batch can contain up to 20 CVs. Please name a smaller group or select them in Candidates.", {"type": "candidate_work", "route": route}
    if labels:
        applications = list(await session.scalars(select(Application).where(Application.job_id == job.id,
            Application.candidate_number.in_(set(labels)), Application.stage == expected_stage).order_by(Application.candidate_number)))
        found = {application.candidate_number for application in applications}
        missing = sorted(set(labels) - found)
        if missing:
            missing_labels = ", ".join(f"candidate{number:03d}" for number in missing)
            return (f"I couldn't start the batch because {missing_labels} is not in the {expected_stage.replace('_', ' ')} stage for {job.public_code}. Check the job and CV section.",
                    {"type": "candidate_work", "route": route})
    else:
        applications = list(await session.scalars(select(Application).where(
            Application.job_id == job.id, Application.stage == expected_stage
        ).order_by(Application.candidate_number).limit(21)))
        if len(applications) > 20:
            return (f"There are more than 20 eligible CVs for {job.public_code}. Please select or name up to 20 for this batch.",
                    {"type": "candidate_work", "route": route, "job_id": str(job.id), "job_code": job.public_code,
                     "kind": "integrity" if integrity_request else "ranking"})
        if not applications:
            stage_label = "uploaded" if integrity_request else "parsed"
            return (f"There are no {stage_label} CVs ready for that task under {job.public_code}.",
                    {"type": "candidate_work", "route": route, "job_id": str(job.id), "job_code": job.public_code,
                     "kind": "integrity" if integrity_request else "ranking"})
    kind = "integrity_parse" if integrity_request else "ranking"
    auto_start_ranking = bool(integrity_request and ranking_request)
    if integrity_request and previous_payload.get("auto_start_ranking"):
        auto_start_ranking = True
    batch = CvBatch(owner_id=thread.owner_id, job_id=job.id, thread_id=thread.id, kind=kind,
                    status="queued", total=len(applications), auto_start_ranking=auto_start_ranking)
    session.add(batch)
    await session.flush()
    for application in applications:
        old_stage = application.stage
        application.stage = "integrity_check" if integrity_request else "ranking"
        session.add(CvBatchItem(batch_id=batch.id, application_id=application.id, status="queued"))
        session.add(ApplicationEvent(application_id=application.id, actor_id=thread.owner_id,
            action="batch_started", from_stage=old_stage, to_stage=application.stage,
            reason=f"Recruiter started {('integrity check and parsing' if integrity_request else 'Feature 1 ranking')} from the job chat."))
    await session.commit()
    operation = "Integrity check and parsing" if integrity_request else "Feature 1 ranking"
    followup = (" F1 ranking will start automatically for CVs that finish parsing successfully." if auto_start_ranking else
                " After parsing finishes, I’ll ask before starting F1 ranking." if integrity_request else "")
    return (f"{operation} has started for {len(applications)} CV(s) under {job.public_code}. Processing continues in the background.{followup}",
            {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section={'integrity' if integrity_request else 'ranking'}",
             "batch_id": str(batch.id), "job_id": str(job.id), "job_code": job.public_code,
             "kind": "integrity" if integrity_request else "ranking"})


async def _handle_scoring_work_request(session: AsyncSession, thread: ChatThread,
                                       message: str, messages: list[ChatMessage] | None = None) -> tuple[str, dict[str, Any]] | None:
    lowered = message.lower()
    previous = next((m for m in reversed((messages or [])[:-1]) if m.role == "assistant"), None)
    prior_payload = previous.payload or {} if previous else {}
    continuing = prior_payload.get("kind") == "scoring" and bool(re.search(r"\b(?:all|every|available|candidate\s*\d+)\b", lowered))
    explicit = bool(re.search(r"\b(?:f2|feature\s*2|scor(?:e|ing))\b", lowered) and
                    re.search(r"\b(?:start|run|calculate|score|scoring|process)\b", lowered))
    if not (explicit or continuing):
        return None
    job = await jobs.get_job(session, thread.job_id) if thread.job_id else None
    route = f"/candidates?jobId={job.id}&section=scoring" if job else "/candidates?section=scoring"
    if job is None or job.active_version is None or job.archived_at is not None:
        return "Open an active approved job chat before starting F2 scoring.", {"type": "candidate_work", "route": route}
    labels = sorted({int(n) for n in re.findall(r"\bcandidate\s*0*(\d{1,6})\b", lowered)})
    all_requested = bool(re.search(r"\b(?:all|every|available)\b", lowered))
    if not labels and not all_requested:
        return (f"Which F2-ready CVs under {job.public_code} should I score? Name candidate labels or say ‘all F2-ready CVs’ (up to 20).",
                {"type": "candidate_work", "route": route, "kind": "scoring"})
    if len(labels) > 20:
        return "Select at most 20 F2-ready CVs for one batch.", {"type": "candidate_work", "route": route}
    query = select(Application).where(Application.job_id == job.id, Application.stage == "scoring")
    if labels:
        query = query.where(Application.candidate_number.in_(labels))
    applications = list(await session.scalars(query.order_by(Application.candidate_number).limit(21)))
    if len(applications) > 20 or (labels and len(applications) != len(labels)) or not applications:
        return ("I could not match that selection to 1–20 F2-ready CVs. Check Feature 2 Scoring for eligible candidates.",
                {"type": "candidate_work", "route": route})
    weights, policy_version = await suggested_weights(session, job)
    summary = ", ".join(f"{key.replace('_', ' ')} {value}%" for key, value in weights.items())
    return (f"I found {len(applications)} F2-ready CV(s) under {job.public_code}. Suggested scoring weights: {summary}. "
            "Reply ‘confirm F2’ to use them, give four weights such as ‘technical 40, experience 30, certifications 10, education 20’, "
            "or edit them in Candidates before starting the batch.",
            {"type": "candidate_work", "route": route, "kind": "scoring", "awaiting_confirmation": "scoring",
             "application_ids": [str(app.id) for app in applications], "weights": weights,
             "policy_version": policy_version, "job_id": str(job.id), "job_code": job.public_code})


async def _confirm_candidate_work(session: AsyncSession, thread: ChatThread, messages: list[ChatMessage],
                                  simple: str) -> tuple[str, dict[str, Any]] | None:
    """Continue the exact parsed-CV selection from a prior assistant offer after explicit recruiter confirmation."""
    affirmative = simple in {"yes", "yes please", "sure", "go ahead", "proceed", "please proceed", "start it", "start ranking"}
    previous = next((message for message in reversed(messages[:-1]) if message.role == "assistant"), None)
    payload = (previous.payload or {}) if previous else {}
    if payload.get("awaiting_confirmation") == "scoring" and simple not in {"confirm f2", "confirm scoring", "use these weights", "yes", "proceed"}:
        aliases = {"technical_projects": r"technical(?:\s+skills)?|projects?",
                   "relevant_experience": r"experience",
                   "certifications_soft_skills": r"certifications?|soft\s+skills?",
                   "education_qualifications": r"education|qualifications?"}
        found: dict[str, int] = {}
        for key, alias in aliases.items():
            match = re.search(rf"\b(?:{alias})\b\s*[:=]?\s*(\d{{1,3}})\s*%?", simple)
            if match:
                found[key] = int(match.group(1))
        if found:
            updated = {**payload.get("weights", {}), **found}
            try:
                validate_weights(updated)
            except ValueError as exc:
                return f"{exc} Please give all four percentages or adjust the suggested weights.", payload
            summary = ", ".join(f"{key.replace('_', ' ')} {value}%" for key, value in updated.items())
            return f"Updated F2 weights: {summary}. Reply ‘confirm F2’ to start the selected CVs.", {**payload, "weights": updated}
    if payload.get("awaiting_confirmation") == "scoring" and simple in {"confirm f2", "confirm scoring", "use these weights", "yes", "proceed"}:
        try:
            ids = [uuid.UUID(value) for value in payload["application_ids"]]
            weights = payload["weights"]
            job_id = uuid.UUID(payload["job_id"])
        except (KeyError, TypeError, ValueError, AttributeError):
            return "The F2 selection expired. Please select the CVs again.", {"type": "text"}
        if thread.job_id != job_id:
            return "This chat is no longer linked to that job. Select the CVs again.", {"type": "text"}
        job = await session.get(Job, job_id)
        if job is None or job.archived_at is not None or job.active_version is None:
            return "This job is no longer active.", {"type": "text"}
        batch = await start_f2_batch(session, owner_id=thread.owner_id, application_ids=ids,
                                     weights=weights, thread_id=thread.id)
        return (f"F2 scoring started for {len(ids)} CV(s) under {job.public_code}. I’ll link you to Review when scores are ready.",
                {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=scoring",
                 "kind": "scoring", "job_id": str(job.id), "batch_id": str(batch.id)})
    if not affirmative:
        return None
    if payload.get("type") != "candidate_work" or payload.get("awaiting_confirmation") != "ranking":
        return None
    try:
        application_ids = list(dict.fromkeys(uuid.UUID(value) for value in payload.get("application_ids", [])))
    except (ValueError, TypeError, AttributeError):
        return "I couldn't confirm that CV selection. Please select the parsed CVs in Candidates and try again.", {"type": "text"}
    if not application_ids or len(application_ids) > 20:
        return "There are no eligible parsed CVs in that batch. Select up to 20 parsed CVs in Candidates.", {"type": "text"}
    job_id_value = payload.get("job_id")
    job = None
    try:
        if job_id_value:
            job = await session.scalar(select(Job).where(Job.id == uuid.UUID(job_id_value), Job.owner_id == thread.owner_id,
                Job.active_version.is_not(None), Job.archived_at.is_(None)))
    except (ValueError, TypeError):
        job = None
    if job is None or (thread.job_id is not None and thread.job_id != job.id):
        return "That job is no longer active in this chat. Select parsed CVs from an active job in Candidates.", {"type": "text"}
    applications = list(await session.scalars(select(Application).where(Application.id.in_(application_ids),
        Application.job_id == job.id, Application.stage == "parsed").with_for_update()))
    if len(applications) != len(application_ids):
        return "The selected CVs have changed stage and cannot all be ranked. Review the Parsed CV section and select the current set.", {"type": "text"}
    batch = CvBatch(owner_id=thread.owner_id, job_id=job.id, thread_id=thread.id, kind="ranking", status="queued", total=len(applications))
    session.add(batch)
    await session.flush()
    for application in applications:
        application.stage = "ranking"
        session.add(CvBatchItem(batch_id=batch.id, application_id=application.id, status="queued"))
        session.add(ApplicationEvent(application_id=application.id, actor_id=thread.owner_id, action="batch_started",
            from_stage="parsed", to_stage="ranking", reason="Recruiter confirmed Feature 1 ranking in the job chat."))
    await session.commit()
    return (f"Feature 1 ranking started for {len(applications)} CV(s) under {job.public_code}. I’ll share the results and direct you to Review when the batch finishes.",
            {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=ranking", "batch_id": str(batch.id),
             "job_id": str(job.id), "job_code": job.public_code, "kind": "ranking"})

