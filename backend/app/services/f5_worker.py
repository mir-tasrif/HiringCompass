"""Claim and process F5 runs independently of CV tasks, with durable retries and leases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Application, F5Batch, F5Run, JobVersion
from app.graphs.f5_interview_analysis.graph import build_f5_graph
from app.rag.ingest import ingest_directory

logger = get_logger("services.f5_worker")


async def _batch_status(session: AsyncSession, batch_id) -> None:
    batch = await session.get(F5Batch, batch_id)
    if batch:
        statuses = set(await session.scalars(select(F5Run.status).where(F5Run.batch_id == batch_id)))
        batch.status = ("processing" if "processing" in statuses else "queued" if "queued" in statuses
                        else "completed_with_errors" if "failed" in statuses else "completed")


async def run_f5_worker(session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                        llm, store, checkpointer=None) -> None:
    """Import folder documents and analyse queued runs; a restart reclaims expired leases."""
    try:
        await ingest_directory("technical", settings.rag_corpus_dir / "technical", settings=settings,
                               store=store, session_factory=session_factory)
    except Exception:
        # Analysis still works provisionally when the approved-reference folder is unavailable.
        logger.exception("F5 technical reference import failed; accuracy will be provisional")
    graph = build_f5_graph(llm, store, session_factory, settings.rag_namespace_technical, checkpointer)
    while True:
        try:
            now = datetime.now(timezone.utc)
            run_id = None
            async with session_factory() as session:
                run = await session.scalar(select(F5Run).where(
                    ((F5Run.status == "queued") & ((F5Run.next_retry_at.is_(None)) | (F5Run.next_retry_at <= now))) |
                    ((F5Run.status == "processing") & (F5Run.lease_expires_at < now)))
                    .order_by(F5Run.created_at).with_for_update(skip_locked=True).limit(1))
                if run is not None:
                    run.status, run.attempts = "processing", run.attempts + 1
                    run.next_retry_at = None
                    run.lease_expires_at = now + timedelta(minutes=30)
                    run_id, batch_id = run.id, run.batch_id
                    await _batch_status(session, batch_id)
                    await session.commit()
            if run_id is None:
                await asyncio.sleep(settings.worker_poll_seconds)
                continue
            try:
                async with session_factory() as session:
                    run = await session.get(F5Run, run_id)
                    application = await session.get(Application, run.application_id)
                    version = await session.scalar(select(JobVersion).where(JobVersion.job_id == application.job_id,
                        JobVersion.version == run.job_version))
                    if version is None:
                        raise ValueError("The saved job version is unavailable.")
                    transcript = run.transcript
                    segment_overrides = run.segment_overrides
                    requirements = version.profile.get("requirements", [])
                result = await graph.ainvoke({"transcript": transcript, "requirements": requirements,
                                              "segment_overrides": segment_overrides},
                                             {"configurable": {"thread_id": f"f5-{run_id}"}})
                async with session_factory() as session:
                    run = await session.get(F5Run, run_id)
                    run.result = {key: result[key] for key in ("evaluations", "coverage", "overall_score", "provisional", "prompt_versions")}
                    run.result = {**run.result, "model_name": settings.llm_model}
                    run.status, run.error, run.lease_expires_at = "completed", None, None
                    await _batch_status(session, batch_id)
                    await session.commit()
            except Exception as exc:
                async with session_factory() as session:
                    run = await session.get(F5Run, run_id)
                    run.error, run.lease_expires_at = str(exc)[:1000], None
                    run.status = "queued" if run.attempts < settings.worker_max_attempts else "failed"
                    run.next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=min(
                        settings.worker_backoff_max_seconds, settings.worker_backoff_base_seconds * 2 ** (run.attempts - 1)))) if run.status == "queued" else None
                    await _batch_status(session, batch_id)
                    await session.commit()
                logger.exception("F5 run failed", extra={"run_id": str(run_id)})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("F5 worker loop failed")
            await asyncio.sleep(settings.worker_poll_seconds)
