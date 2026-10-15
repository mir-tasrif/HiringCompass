"""Claim, retry, and finalize durable CV batches across F11, F1, and F2.

Feature-specific evidence checks and result builders live in their own modules.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import PermanentError, TransientError
from app.db.models import (Application, ApplicationEvent, Candidate, CandidateProfile, CandidateScreening,
                           CandidateScore, F2ScoringPolicy, ChatMessage, ChatThread, CvBatch, CvBatchItem, Document, Job, JobVersion)
from app.rag.store import KnowledgeStore
from app.prompts.system import CV_PROFILE
from app.services.cv_pipeline import CvExtractionFailure, _safe_path, extract_pdf_text, parse_candidate_profile
from app.services.cv_stage_events import CandidateStageChanged, record_stage_event as _event
from app.services.f1_screening_results import save_screening_result
from app.services.f2_scoring_results import prepare_scoring_input, score_record


def _retry_delay_seconds(settings: Settings, attempts: int, error: Exception) -> int:
    """Exponential per-item delay, bounded and never shorter than a provider Retry-After hint."""
    delay = min(settings.worker_backoff_max_seconds,
                settings.worker_backoff_base_seconds * (2 ** min(max(attempts - 1, 0), 10)))
    if isinstance(error, TransientError) and error.retry_after_seconds is not None:
        delay = min(settings.worker_backoff_max_seconds, max(delay, int(error.retry_after_seconds)))
    if isinstance(error, TransientError) and "HTTP 429" in str(error):
        delay = max(delay, min(settings.worker_backoff_max_seconds, 60 * attempts))
    return delay


async def refresh_batch_status(session: AsyncSession, batch_id: uuid.UUID) -> None:
    batch = await session.get(CvBatch, batch_id)
    if batch is None:
        return
    items = list(await session.scalars(select(CvBatchItem).where(CvBatchItem.batch_id == batch_id)))
    statuses = {item.status for item in items}
    if "processing" in statuses or "queued" in statuses:
        batch.status = "processing" if "processing" in statuses else "queued"
    elif "waiting_review" in statuses:
        batch.status = "waiting_review"
    elif "failed" in statuses or "cancelled" in statuses:
        batch.status = "completed_with_errors"
    else:
        batch.status = "completed"


async def _profile_for(session: AsyncSession, application_id: uuid.UUID) -> CandidateProfile | None:
    return await session.scalar(select(CandidateProfile).where(CandidateProfile.application_id == application_id))


async def _mark_item_for_current_stage(session_factory: async_sessionmaker[AsyncSession], item_id: uuid.UUID) -> None:
    batch_id: uuid.UUID | None = None
    async with session_factory() as session:
        item = await session.get(CvBatchItem, item_id)
        if item is None:
            return
        application = await session.get(Application, item.application_id)
        if application is None or application.stage == "rejected":
            item.status = "rejected"
            item.error = "The CV was rejected while processing was in progress."
        elif application.stage in {"parsed", "f1_review", "scoring", "f2_review", "interview_selection", "screened_out"}:
            item.status, item.error = "completed", None
        else:
            item.status, item.error = "failed", "The candidate moved to another stage while processing was in progress."
        item.lease_expires_at = None
        batch_id = item.batch_id
        await refresh_batch_status(session, item.batch_id)
        await session.commit()
    if batch_id:
        await _finalize_batch(session_factory, batch_id)


async def _notify_chat_review(session: AsyncSession, batch: CvBatch, item: CvBatchItem,
                              application: Application, *, gate: str, evidence: str) -> None:
    if batch.thread_id is None or item.review_notified:
        return
    job = await session.get(Job, batch.job_id)
    candidate = await session.get(Candidate, application.candidate_id)
    if job is None or candidate is None:
        return
    gate_name = "F11 integrity" if gate == "integrity" else "F1 screening"
    session.add(ChatMessage(thread_id=batch.thread_id, role="assistant",
        content=f"{gate_name} review is needed for {candidate.full_name} under {job.public_code}. The CV and evidence are ready in the Review section.",
        payload={"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=review",
                 "kind": gate, "application_id": str(application.id), "review_required": True,
                 "evidence_summary": evidence[:1000]}))
    thread = await session.get(ChatThread, batch.thread_id)
    if thread:
        thread.updated_at = datetime.now(timezone.utc)
    item.review_notified = True


async def _notify_chat_completion(session: AsyncSession, batch: CvBatch) -> None:
    if batch.thread_id is None or batch.completion_notified or batch.status not in {"completed", "completed_with_errors"}:
        return
    job = await session.get(Job, batch.job_id)
    if job is None:
        return
    items = list(await session.scalars(select(CvBatchItem).where(CvBatchItem.batch_id == batch.id)))
    application_ids: list[str] = []
    if batch.kind == "integrity_parse":
        for item in items:
            app = await session.get(Application, item.application_id)
            if app and app.stage == "parsed":
                application_ids.append(str(app.id))
        count = len(application_ids)
        rejected = sum(item.status == "rejected" for item in items)
        failed = sum(item.status == "failed" for item in items)
        if count and batch.auto_start_ranking:
            ranking_batch = CvBatch(owner_id=batch.owner_id, job_id=batch.job_id, thread_id=batch.thread_id,
                                    kind="ranking", status="queued", total=count)
            session.add(ranking_batch)
            await session.flush()
            for application_id in application_ids:
                app = await session.get(Application, uuid.UUID(application_id), with_for_update=True)
                if app is None or app.stage != "parsed":
                    continue
                app.stage = "ranking"
                session.add(CvBatchItem(batch_id=ranking_batch.id, application_id=app.id, status="queued"))
                session.add(ApplicationEvent(application_id=app.id, actor_id=batch.owner_id,
                    action="batch_started", from_stage="parsed", to_stage="ranking",
                    reason="Recruiter requested integrity, parsing, and F1 ranking in the job chat."))
            message = (f"Integrity checks and profile parsing finished for {count} CV(s) under {job.public_code}; "
                       f"Feature 1 ranking has now started for the successfully parsed CVs. "
                       f"{rejected} CV(s) were moved to Rejected; {failed} CV(s) need a technical retry. "
                       "I’ll direct you to Review when ranking finishes.")
            payload = {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=ranking",
                       "job_id": str(job.id), "job_code": job.public_code, "kind": "ranking",
                       "batch_id": str(ranking_batch.id)}
        elif count:
            message = (f"Integrity checks and profile parsing finished for {count} CV(s) under {job.public_code}. "
                       f"{rejected} CV(s) were moved to Rejected with reasons; {failed} CV(s) need a technical retry. "
                       "Would you like me to start Feature 1 ranking for the parsed CVs?")
            payload = {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=parsed",
                       "job_id": str(job.id), "job_code": job.public_code, "kind": "ranking",
                       "awaiting_confirmation": "ranking", "application_ids": application_ids}
        else:
            message = (f"No CVs from this batch could be parsed successfully under {job.public_code}. "
                       f"{rejected} CV(s) are in Rejected with reasons; {failed} technical failure(s) can be retried in Candidates.")
            payload = {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=integrity",
                       "job_id": str(job.id), "job_code": job.public_code, "kind": "integrity"}
    elif batch.kind == "ranking":
        failed = sum(item.status == "failed" for item in items)
        message = (f"Feature 1 ranking finished for the selected CV batch under {job.public_code}. "
                   f"The available evidence and decisions are ready in Review; {failed} CV(s) need a technical retry. "
                   "I’ll leave the recruiter decision to you.")
        payload = {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=review",
                   "job_id": str(job.id), "job_code": job.public_code, "kind": "review"}
    elif batch.kind == "scoring":
        failed = sum(item.status == "failed" for item in items)
        completed = sum(item.status == "completed" for item in items)
        message = (f"Feature 2 scoring finished for {completed} CV(s) under {job.public_code}. "
                   f"Scores and evidence are ready for recruiter decisions in Review; {failed} CV(s) need a technical retry.")
        payload = {"type": "candidate_work", "route": f"/candidates?jobId={job.id}&section=review",
                   "job_id": str(job.id), "job_code": job.public_code, "kind": "review"}
    else:
        return
    session.add(ChatMessage(thread_id=batch.thread_id, role="assistant", content=message, payload=payload))
    thread = await session.get(ChatThread, batch.thread_id)
    if thread:
        thread.updated_at = datetime.now(timezone.utc)
    batch.completion_notified = True


async def _recompute_job_ranks(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Assign distinct, deterministic ranks across all active F1 candidates for one job."""
    rows = (await session.execute(select(CandidateScreening, Application)
        .join(Application, Application.id == CandidateScreening.application_id)
        .where(CandidateScreening.job_id == job_id))).all()
    eligible: list[tuple[CandidateScreening, Application]] = []
    for screening, application in rows:
        mandatory_requirements = [requirement for requirement in screening.requirements
                                  if requirement.get("kind") == "mandatory"]
        mandatory_eligible = all(requirement.get("status") in {"met", "partially_met"}
                                 for requirement in mandatory_requirements)
        screening.mandatory_pass = mandatory_eligible
        if mandatory_eligible and application.stage in {"f1_review", "scoring", "scoring_processing", "f2_review", "interview_selection"}:
            eligible.append((screening, application))
        else:
            screening.rank = None
    eligible.sort(key=lambda pair: (-pair[0].suitability_score, pair[1].candidate_number,
                                    pair[1].created_at, str(pair[1].id)))
    for rank, (screening, _) in enumerate(eligible, 1):
        screening.rank = rank


async def _finalize_batch(session_factory: async_sessionmaker[AsyncSession], batch_id: uuid.UUID) -> None:
    """Serialize completion, relative ranking, and chatbot notices after each item's commit."""
    async with session_factory() as session:
        batch = await session.scalar(select(CvBatch).where(CvBatch.id == batch_id).with_for_update())
        if batch is None:
            return
        await refresh_batch_status(session, batch.id)
        items = list(await session.scalars(select(CvBatchItem).where(CvBatchItem.batch_id == batch.id)))
        statuses = {item.status for item in items}
        if statuses & {"queued", "processing", "waiting_review"}:
            await session.commit()
            return
        if batch.kind == "ranking":
            await _recompute_job_ranks(session, batch.job_id)
        await _notify_chat_completion(session, batch)
        batch.completion_notified = True
        await session.commit()


async def finalize_batch(session_factory: async_sessionmaker[AsyncSession], batch_id: uuid.UUID) -> None:
    """Public batch-completion operation used by API routes after recruiter actions."""
    await _finalize_batch(session_factory, batch_id)


async def recompute_job_ranks(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Public F1 rank refresh after a recruiter changes candidate review state."""
    await _recompute_job_ranks(session, job_id)


async def process_intake_item(session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                              llm: Any, item_id: uuid.UUID, integrity_graph: Any) -> None:
    async with session_factory() as session:
        item = await session.get(CvBatchItem, item_id)
        if item is None:
            return
        application = await session.get(Application, item.application_id)
        if application is None:
            raise PermanentError("The CV application record no longer exists.")
        if application.stage != "integrity_check":
            await session.rollback()
            await _mark_item_for_current_stage(session_factory, item_id)
            return
        document = await session.scalar(select(Document).where(Document.application_id == application.id).order_by(Document.version.desc()))
        if document is None:
            raise PermanentError("The CV file record could not be found.")
        profile_row = await _profile_for(session, application.id)

        # An F11 reviewer approval resumes at profile extraction; do not rerun or discard the recorded integrity evidence.
        if profile_row is None:
            path = _safe_path(settings.file_storage_dir, document.file_path)
            extracted_text, method = await asyncio.to_thread(extract_pdf_text, path, settings)
            profile_row = CandidateProfile(application_id=application.id, extracted_text=extracted_text,
                extraction_method=method, profile={}, integrity_verdict="pending",
                integrity_signals=[], integrity_policy_version=settings.integrity_policy_version)
            session.add(profile_row)
            await session.commit()
        if profile_row.integrity_verdict == "pending":
            config = {"configurable": {"thread_id": f"f11:{item.id}"}}
            result = await integrity_graph.ainvoke({"application_id": str(application.id)}, config)
            signals = result["signals"]
            review_required = result["review_required"]
            current_application = await session.scalar(select(Application).where(Application.id == application.id)
                .with_for_update().execution_options(populate_existing=True))
            if current_application is None or current_application.stage != "integrity_check":
                await session.rollback()
                await _mark_item_for_current_stage(session_factory, item_id)
                return
            application = current_application
            profile_row.integrity_signals = signals
            profile_row.integrity_verdict = "review_required" if review_required else "normal"
            profile_row.integrity_policy_version = result["policy_version"]
            if review_required:
                evidence = "; ".join(f"{s['code']}: {s['evidence']}" for s in signals)[:4000]
                await _event(session, application, None, "integrity_flagged", "integrity_review", evidence)
                item.status, item.error, item.lease_expires_at = "waiting_review", None, None
                await refresh_batch_status(session, item.batch_id)
                batch = await session.get(CvBatch, item.batch_id)
                if batch:
                    await _notify_chat_review(session, batch, item, application, gate="integrity", evidence=evidence)
                await session.commit()
                return
            session.add(ApplicationEvent(application_id=application.id, actor_id=None, action="integrity_passed",
                from_stage="integrity_check", to_stage="integrity_check",
                reason="The PDF passed F11 integrity checks with no suspicious document or text signals."))
            await session.commit()
            profile_row = await _profile_for(session, application.id)
        if profile_row.integrity_verdict not in {"normal", "approved_after_review"}:
            item.status, item.error, item.lease_expires_at = "waiting_review", None, None
            await refresh_batch_status(session, item.batch_id)
            await session.commit()
            return
        profile_row.profile = await parse_candidate_profile(llm, profile_row.extracted_text)
        profile_row.profile_prompt_key = CV_PROFILE.key
        profile_row.profile_prompt_version = CV_PROFILE.version
        parsed_email = profile_row.profile.get("email")
        if parsed_email:
            candidate = await session.get(Candidate, application.candidate_id)
            if candidate and not candidate.email:
                candidate.email = parsed_email
        current_application = await session.scalar(select(Application).where(Application.id == application.id)
            .with_for_update().execution_options(populate_existing=True))
        if current_application is None or current_application.stage != "integrity_check":
            await session.rollback()
            await _mark_item_for_current_stage(session_factory, item_id)
            return
        application = current_application
        await _event(session, application, None, "parsed", "parsed", "CV text extracted and structured profile saved.")
        item.status, item.error, item.lease_expires_at = "completed", None, None
        await refresh_batch_status(session, item.batch_id)
        batch_id = item.batch_id
        await session.commit()
        await _finalize_batch(session_factory, batch_id)


async def process_ranking_item(session_factory: async_sessionmaker[AsyncSession], item_id: uuid.UUID,
                               screening_graph: Any) -> None:
    async with session_factory() as session:
        item = await session.get(CvBatchItem, item_id)
        if item is None:
            return
        application = await session.get(Application, item.application_id)
        if application is None:
            raise PermanentError("The CV application record no longer exists.")
        if application.stage != "ranking":
            await session.rollback()
            await _mark_item_for_current_stage(session_factory, item_id)
            return
        existing = await session.scalar(select(CandidateScreening).where(CandidateScreening.application_id == application.id))
        if existing is None:
            config = {"configurable": {"thread_id": f"f1:{item.id}"}}
            result = await screening_graph.ainvoke({"application_id": str(application.id)}, config)
            await save_screening_result(session, application, result)
        item.status, item.error, item.lease_expires_at = "completed", None, None
        await refresh_batch_status(session, item.batch_id)
        batch_id = item.batch_id
        await session.commit()
        await _finalize_batch(session_factory, batch_id)


async def process_scoring_item(session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                               graph: Any, item_id: uuid.UUID) -> None:
    async with session_factory() as session:
        item = await session.get(CvBatchItem, item_id)
        if item is None:
            return
        application = await session.get(Application, item.application_id)
        if application is None:
            raise PermanentError("The CV application no longer exists.")
        if application.stage != "scoring_processing":
            await session.rollback()
            await _mark_item_for_current_stage(session_factory, item_id)
            return
        existing = await session.scalar(select(CandidateScore).where(CandidateScore.batch_item_id == item.id))
        batch = await session.get(CvBatch, item.batch_id)
        policy = await session.get(F2ScoringPolicy, batch.scoring_policy_id) if batch and batch.scoring_policy_id else None
        profile = await _profile_for(session, application.id)
        version = await session.scalar(select(JobVersion).where(JobVersion.job_id == application.job_id,
            JobVersion.version == application.job_version))
        if policy is None or profile is None or not profile.profile or version is None:
            raise PermanentError("F2 needs a saved job profile, parsed CV, and confirmed scoring policy.")
        if existing is None:
            digest, state = prepare_scoring_input(profile, version, policy, settings)
            config = {"configurable": {"thread_id": f"f2:{item.id}"}}
            previous = await session.scalar(select(CandidateScore).where(CandidateScore.application_id == application.id,
                CandidateScore.input_hash == digest, CandidateScore.model_name == settings.llm_model)
                .order_by(CandidateScore.created_at.desc()).limit(1))
            if previous:
                result = {"dimensions": previous.dimensions, "aggregate_score": previous.aggregate_score,
                          "prompt_key": previous.prompt_key, "prompt_version": previous.prompt_version}
            else:
                checkpoint = await graph.aget_state(config)
                saved_state = checkpoint.values or {}
                if saved_state.get("input_hash") == digest and saved_state.get("aggregate_score") is not None and not checkpoint.next:
                    result = saved_state
                else:
                    result = await graph.ainvoke(state, config)
        current = await session.scalar(select(Application).where(Application.id == application.id)
            .with_for_update().execution_options(populate_existing=True))
        if current is None or current.stage != "scoring_processing":
            raise CandidateStageChanged()
        if existing is None:
            session.add(score_record(application.id, item.id, policy, settings, result, digest))
        await _event(session, current, None, "f2_scored", "f2_review",
                     f"F2 score {result['aggregate_score'] if existing is None else existing.aggregate_score}/100; awaiting recruiter interview-selection decision.")
        item.status, item.error, item.lease_expires_at = "completed", None, None
        await refresh_batch_status(session, item.batch_id)
        batch_id = item.batch_id
        await session.commit()
        await _finalize_batch(session_factory, batch_id)


async def cv_worker_once(session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                         llm: Any, store: KnowledgeStore, scoring_graph: Any | None = None, screening_graph: Any | None = None,
                         integrity_graph: Any | None = None,
                         allowed_kinds: set[str] | None = None) -> bool:
    """Claim and process one durable CV task; expired leases make interrupted tasks recoverable."""
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        claim = select(CvBatchItem).join(CvBatch, CvBatch.id == CvBatchItem.batch_id).where(
            ((CvBatchItem.status == "queued") & ((CvBatchItem.next_attempt_at.is_(None)) | (CvBatchItem.next_attempt_at <= now))) |
            ((CvBatchItem.status == "processing") & (CvBatchItem.lease_expires_at < now))
        )
        if allowed_kinds is not None:
            claim = claim.where(CvBatch.kind.in_(allowed_kinds))
        item = await session.scalar(claim.order_by(CvBatchItem.created_at)
            .with_for_update(of=CvBatchItem, skip_locked=True).limit(1))
        if item is None:
            unfinished = list(await session.scalars(select(CvBatch.id).where(
                CvBatch.status.in_(["completed", "completed_with_errors"]), CvBatch.completion_notified.is_(False)
            ).order_by(CvBatch.created_at).limit(20)))
            for batch_id in unfinished:
                await _finalize_batch(session_factory, batch_id)
            return False
        batch = await session.get(CvBatch, item.batch_id)
        if batch is None:
            item.status, item.error = "failed", "The CV batch record is missing."
            await session.commit()
            return True
        item.status = "processing"
        item.attempts += 1
        item.next_attempt_at = None
        lease_seconds = max(settings.worker_lease_seconds, settings.llm_timeout_seconds + 780,
                            settings.cv_batch_target_seconds)
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        batch.status = "processing"
        item_id, kind = item.id, batch.kind
        await session.commit()
    try:
        if kind == "integrity_parse" and integrity_graph is not None:
            await process_intake_item(session_factory, settings, llm, item_id, integrity_graph)
        elif kind == "ranking" and screening_graph is not None:
            await process_ranking_item(session_factory, item_id, screening_graph)
        elif kind == "scoring" and scoring_graph is not None:
            await process_scoring_item(session_factory, settings, scoring_graph, item_id)
        else:
            raise PermanentError("Unknown CV batch operation.")
    except CandidateStageChanged:
        await _mark_item_for_current_stage(session_factory, item_id)
    except CvExtractionFailure as exc:
        stage_changed = False
        batch_id: uuid.UUID | None = None
        async with session_factory() as session:
            item = await session.get(CvBatchItem, item_id)
            app = await session.get(Application, item.application_id) if item else None
            if item and app:
                batch_id = item.batch_id
                if app.stage == "integrity_check":
                    await _event(session, app, None, "rejected", "rejected", f"CV text extraction failed: {exc}")
                    app.rejection_action, app.rejection_reason = "rejected", f"CV text extraction failed: {exc}"
                    app.rejected_from_stage, app.rejected_at, app.rejected_by = "integrity_check", now, None
                    item.status, item.error, item.lease_expires_at = "rejected", str(exc), None
                else:
                    stage_changed = True
                await refresh_batch_status(session, item.batch_id)
                if not stage_changed:
                    await session.commit()
        if stage_changed:
            await _mark_item_for_current_stage(session_factory, item_id)
        elif batch_id:
            await _finalize_batch(session_factory, batch_id)
    except PermanentError as exc:
        batch_id: uuid.UUID | None = None
        async with session_factory() as session:
            item = await session.get(CvBatchItem, item_id)
            if item:
                batch_id = item.batch_id
                application = await session.get(Application, item.application_id)
                item.status = "rejected" if application and application.stage == "rejected" else "failed"
                item.error, item.lease_expires_at, item.next_attempt_at = str(exc)[:2000], None, None
                await refresh_batch_status(session, item.batch_id)
                await session.commit()
        if batch_id:
            await _finalize_batch(session_factory, batch_id)
    except Exception as exc:
        batch_id: uuid.UUID | None = None
        async with session_factory() as session:
            item = await session.get(CvBatchItem, item_id)
            if item:
                batch_id = item.batch_id
                app = await session.get(Application, item.application_id)
                item.lease_expires_at = None
                if app and app.stage == "rejected":
                    item.status, item.error, item.next_attempt_at = "rejected", "The CV was rejected while processing was in progress.", None
                else:
                    item.error = str(exc)[:2000]
                    batch = await session.get(CvBatch, item.batch_id)
                    max_attempts = settings.worker_max_attempts
                    if item.attempts < max_attempts:
                        delay = _retry_delay_seconds(settings, item.attempts, exc)
                        item.status = "queued"
                        item.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    else:
                        item.status, item.next_attempt_at = "failed", None
                await refresh_batch_status(session, item.batch_id)
                await session.commit()
        if batch_id:
            await _finalize_batch(session_factory, batch_id)
    return True


async def run_cv_worker(session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                        llm: Any, store: KnowledgeStore, scoring_graph: Any | None = None,
                        screening_graph: Any | None = None, integrity_graph: Any | None = None) -> None:
    async def consume(allowed_kinds: set[str] | None = None) -> None:
        while True:
            try:
                worked = await cv_worker_once(session_factory, settings, llm, store, scoring_graph, screening_graph,
                                              integrity_graph, allowed_kinds)
            except asyncio.CancelledError:
                raise
            except Exception:
                worked = False
            if not worked:
                await asyncio.sleep(settings.worker_poll_seconds)

    if settings.llm_profile == "remote":
        # One remote AI task at a time prevents a CV batch from bursting into provider limits.
        await consume()
    elif scoring_graph is not None and settings.worker_max_concurrency > 1:
        await asyncio.gather(consume({"scoring"}),
            *(consume({"integrity_parse", "ranking"}) for _ in range(settings.worker_max_concurrency - 1)))
    else:
        await asyncio.gather(*(consume() for _ in range(settings.worker_max_concurrency)))

