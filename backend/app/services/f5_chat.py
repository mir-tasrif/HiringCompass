"""Recruiter chat commands for F5 readiness, confirmed bulk starts, and status."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import F5Batch, F5Run
from app.services.f5_analysis import ready_roster, start_batch


async def handle_f5_chat(session: AsyncSession, owner_id: uuid.UUID, job_id: uuid.UUID | None,
                         message: str, previous_payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    text = message.strip().lower()
    pending = previous_payload.get("type") == "f5_confirm"
    scope_job_id = None if "all jobs" in text or "across jobs" in text else job_id
    if pending and text in {"confirm f5", "confirm analysis", "yes", "confirm", "go ahead", "start analysis"}:
        ids = [uuid.UUID(value) for value in previous_payload.get("application_ids", [])]
        scope = previous_payload.get("scope_job_id")
        roster = await ready_roster(session, owner_id, uuid.UUID(scope) if scope else None)
        eligible = {uuid.UUID(row["application_id"]) for row in roster if row["analysis_status"] in {"ready", "outdated", "failed"}}
        if not ids or any(value not in eligible for value in ids):
            return "The F5 candidate list changed. Ask me to show the ready candidates again.", {"type": "text"}
        batch = await start_batch(session, owner_id, ids)
        return f"F5 analysis started for {len(ids)} candidate(s). Batch {batch.id}. You can ask me for F5 progress.", {"type": "f5_started", "batch_id": str(batch.id)}
    if not re.search(r"\b(?:f5|q\s*&\s*a analysis|qa analysis|interview answer analysis)\b", text):
        return None
    if re.search(r"\b(?:progress|status|result|finished|completed)\b", text):
        rows = list(await session.scalars(select(F5Batch).where(F5Batch.owner_id == owner_id)
            .order_by(F5Batch.created_at.desc()).limit(1)))
        if not rows:
            return "No F5 analysis batch has started yet.", {"type": "text"}
        runs = list(await session.scalars(select(F5Run).where(F5Run.batch_id == rows[0].id)))
        counts = {status: sum(run.status == status for run in runs) for status in ("queued", "processing", "completed", "failed")}
        return (f"Latest F5 batch: {rows[0].status}. " + ", ".join(f"{number} {status}" for status, number in counts.items())
                + ". Detailed evidence and review are in Candidates → F5 QA Analysis.", {"type": "text"})
    roster = await ready_roster(session, owner_id, scope_job_id)
    if not roster:
        return "No candidates have a completed transcript handed off to F5 for this job.", {"type": "text"}
    listing = "\n".join(f"- {row['job_code']} candidate{row['candidate_number']:03d} ({row['candidate_name']}): {row['analysis_status']}"
                        for row in roster)
    if not re.search(r"\b(?:start|run|analyse|analyze|process)\b", text):
        return "F5 candidates:\n" + listing, {"type": "text"}
    eligible = [row for row in roster if row["analysis_status"] in {"ready", "outdated", "failed"}]
    requested = re.findall(r"candidate\s*0*(\d+)", text)
    if requested:
        wanted = {int(value) for value in requested}
        eligible = [row for row in eligible if row["candidate_number"] in wanted]
    else:
        named = [row for row in eligible if row["candidate_name"].strip().casefold() in text and
                 not re.fullmatch(r"candidate\d+", row["candidate_name"].strip(), re.I)]
        if named:
            eligible = named
    if not requested and not named and not re.search(r"\b(?:all|every|ready|selected)\b", text) and len(eligible) > 1:
        return "Which candidates should I analyse?\n" + listing, {"type": "text"}
    if not eligible:
        return "None of the requested candidates needs a new F5 run. Use Request reanalysis on an existing result if needed.\n" + listing, {"type": "text"}
    if len(eligible) > 20:
        return "Select up to 20 candidates per F5 batch.", {"type": "text"}
    names = ", ".join(f"{row['job_code']} candidate{row['candidate_number']:03d}" for row in eligible)
    return (f"I will analyse {len(eligible)} candidate(s): {names}. Reply ‘confirm F5’ to start.",
            {"type": "f5_confirm", "application_ids": [row["application_id"] for row in eligible],
             "scope_job_id": str(scope_job_id) if scope_job_id else None})
