"""Persisted human-review approvals: idempotent requests and validated, race-safe decisions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApprovalError
from app.db.models import Approval
from app.schemas.contracts import ApprovalGate


# Convert a run id to a UUID when it is one; other identifiers are simply not linked.
def _as_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


# Create a pending approval, or return the existing one for the same gate/target/version (nodes may re-execute).
async def request_approval(session: AsyncSession, *, gate: ApprovalGate, version: int, evidence_summary: str,
                           allowed_options: list[str], job_id: uuid.UUID | None = None,
                           application_id: uuid.UUID | None = None, run_id: str | None = None) -> Approval:
    existing = await session.scalar(
        select(Approval).where(Approval.gate == gate.value, Approval.job_id == job_id,
                               Approval.application_id == application_id, Approval.version == version)
    )
    if existing:
        return existing
    row = Approval(gate=gate.value, job_id=job_id, application_id=application_id, run_id=_as_uuid(run_id), version=version,
                   evidence_summary=evidence_summary, allowed_options=allowed_options, status="pending")
    session.add(row)
    await session.commit()
    return row


# Record a decision. Duplicate identical decisions return the same outcome; stale or conflicting ones are rejected.
async def decide(session: AsyncSession, *, approval_id: uuid.UUID, expected_version: int, option: str,
                 reason: str | None = None, actor_id: uuid.UUID | None = None) -> Approval:
    row = await session.get(Approval, approval_id, with_for_update=True)
    if row is None:
        raise ApprovalError("Approval not found.")
    if row.status == "decided":
        if row.decision_option == option:
            return row
        raise ApprovalError("This approval was already decided differently.")
    if row.status != "pending":
        raise ApprovalError("This approval is no longer pending.")
    if row.version != expected_version:
        raise ApprovalError("This approval is out of date; reload and review again.")
    if option not in row.allowed_options:
        raise ApprovalError("That option is not allowed for this approval.")
    row.status, row.decision_option, row.decision_reason = "decided", option, reason
    row.decided_by, row.decided_at = actor_id, datetime.now(timezone.utc)
    await session.commit()
    return row