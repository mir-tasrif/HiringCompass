"""
HiringCompass - Phase 1 blueprint: global schemas (review copy).

Layout when split in Phase 2/3:
  app/schemas/enums.py       -> section 1
  app/schemas/domain.py      -> section 2 (Pydantic v2 contracts, persisted + API)
  app/schemas/approvals.py   -> section 3 (HITL + durable work records)
  app/graphs/<lgN>/state.py  -> section 4 (LangGraph TypedDict states)

Design rules (from Project Plan sections 3, 10, 11):
  * Graph state holds IDs, small typed outputs and execution metadata only.
  * CV files, transcript bodies and chat history live in PostgreSQL / file volume.
  * Every result carries entity IDs, input versions, status and evidence refs.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 1. ENUMERATIONS
# ---------------------------------------------------------------------------


# Two roles only (Q&A 18).
class UserRole(str, Enum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


# Requirement priority inside a job profile.
class RequirementKind(str, Enum):
    MANDATORY = "mandatory"
    PREFERRED = "preferred"


# F1 per-requirement outcome; NEEDS_REVIEW means evidence is missing/unreliable.
class RequirementStatus(str, Enum):
    MET = "met"
    PARTIAL = "partially_met"
    UNMET = "unmet"
    NEEDS_REVIEW = "needs_review"


# F11 verdict (never "dishonest" - only a prompt for human review).
class IntegrityVerdict(str, Enum):
    NORMAL = "normal"
    REVIEW_REQUIRED = "review_required"


# Severity of a single F11 signal.
class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Candidate application lifecycle (Project Plan section 11).
class ApplicationStage(str, Enum):
    UPLOADED = "uploaded"
    INTEGRITY_REVIEW = "integrity_review"
    SCREENING = "screening"
    ELIGIBILITY_HOLD = "eligibility_hold"
    SCREENED_OUT = "screened_out"
    F1_REVIEW = "f1_review"
    SCORING = "scoring"
    INTERVIEW_SELECTION = "interview_selection"
    NOT_SELECTED = "not_selected"
    SCHEDULED = "scheduled"
    LIVE = "live"
    TRANSCRIPT_READY = "transcript_ready"
    EVALUATION_REQUESTED = "evaluation_requested"
    ANALYZED = "analyzed"
    ASSESSED = "assessed"
    QUARANTINED = "quarantined"
    EXTRACTION_FAILED = "extraction_failed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# The nine human-in-the-loop gates.
class ApprovalGate(str, Enum):
    JOB_ACTIVATION = "job_activation"
    INTEGRITY_REVIEW = "integrity_review"
    ELIGIBILITY_UNCERTAINTY = "eligibility_uncertainty"
    F2_PROGRESSION = "f2_progression"
    INTERVIEW_SELECTION = "interview_selection"
    SCHEDULE_AND_INVITE = "schedule_and_invite"
    EVALUATION_REQUEST = "evaluation_request"
    FINAL_DECISION = "final_decision"
    DELETION = "deletion"


# State of a persisted approval record.
class ApprovalStatus(str, Enum):
    PENDING = "pending"
    DECIDED = "decided"
    STALE = "stale"
    CANCELLED = "cancelled"


# Durable work-item lifecycle (worker claims with lease).
class WorkStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Kinds of graph runs the worker can execute.
class WorkKind(str, Enum):
    JOB_DRAFT = "job_draft"
    APPLICATION_SCREENING = "application_screening"
    SCORING = "scoring"
    INTERVIEW_ANALYSIS = "interview_analysis"
    ASSESSMENT = "assessment"


# F3 session lifecycle.
class InterviewStatus(str, Enum):
    SCHEDULED = "scheduled"
    INVITED = "invited"
    JOINED = "joined"
    LIVE = "live"
    ENDED = "ended"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


# F4 transcript completeness.
class TranscriptStatus(str, Enum):
    LIVE = "live"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


# F5 evaluation criteria (technical accuracy may be "unverified").
class AccuracyVerdict(str, Enum):
    ACCURATE = "accurate"
    PARTIAL = "partially_accurate"
    INACCURATE = "inaccurate"
    UNVERIFIED = "unverified"


# How much of a requirement the interview explored.
class CoverageLevel(str, Enum):
    EXPLORED = "explored"
    PARTIAL = "partially_explored"
    NEVER = "never_explored"


# ---------------------------------------------------------------------------
# 2. DOMAIN CONTRACTS (Pydantic v2)
# ---------------------------------------------------------------------------


# Shared base: strict, immutable-friendly, versioned contract.
class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False, populate_by_name=True)
    schema_version: str = "1.0"


# Pointer to the exact evidence behind a conclusion (CV page or transcript turn or KB chunk).
class EvidenceRef(Contract):
    source: Literal["cv", "transcript", "knowledge"]
    ref_id: str = Field(description="document_id, turn_id or chunk_id")
    page: int | None = None
    snippet: str | None = Field(default=None, max_length=500)


# One normalized job requirement.
class Requirement(Contract):
    requirement_id: str
    text: str
    kind: RequirementKind
    weight: float = Field(default=1.0, gt=0)
    min_years: float | None = Field(default=None, ge=0)
    equivalents: list[str] = Field(default_factory=list)


# Draft/active job profile (new version on every edit).
class JobProfile(Contract):
    job_id: UUID
    version: int = Field(ge=1)
    title: str
    summary: str
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[Requirement]
    policy_source_ids: list[str] = Field(default_factory=list)
    posting_text: str | None = None
    active: bool = False


# One scoring dimension in the rubric (e.g. technical skills).
class RubricDimension(Contract):
    key: str
    description: str
    weight: float = Field(ge=0, le=1)
    anchors: dict[int, str] = Field(description="score anchor -> meaning, e.g. {0:..,50:..,100:..}")


# Rubric approved once per job version (Q&A 22 + Plan section 6).
class EvaluationRubric(Contract):
    rubric_id: UUID
    job_id: UUID
    job_version: int
    version: int = Field(ge=1)
    dimensions: list[RubricDimension]

    # Weights must sum to 1 and include the four mandatory dimensions.
    @model_validator(mode="after")
    def _check_weights(self) -> "EvaluationRubric":
        if abs(sum(d.weight for d in self.dimensions) - 1.0) > 1e-6:
            raise ValueError("rubric weights must sum to 1")
        required = {"technical_skills", "relevant_experience", "qualifications", "job_match"}
        if not required.issubset({d.key for d in self.dimensions}):
            raise ValueError(f"rubric must include {sorted(required)}")
        return self


# Education entry extracted from a CV.
class Education(Contract):
    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    end_year: int | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


# Work-history entry extracted from a CV.
class WorkExperience(Contract):
    employer: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    duration_months: int | None = Field(default=None, ge=0)
    highlights: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


# Evaluation-only candidate view: protected attributes and contact data excluded (N20).
class CandidateProfile(Contract):
    application_id: UUID
    document_id: UUID
    skills: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    experience: list[WorkExperience] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    total_experience_years: float | None = Field(default=None, ge=0)
    uncertainty_flags: list[str] = Field(default_factory=list)


# One F11 detection signal with location and explanation.
class IntegritySignal(Contract):
    signal_type: Literal["hidden_text", "tiny_font", "off_page", "unicode", "injection_pattern", "layout_mismatch"]
    severity: Severity
    page: int | None = None
    explanation: str
    excerpt: str | None = Field(default=None, max_length=200)


# F11 result persisted per document version.
class IntegrityAssessment(Contract):
    document_id: UUID
    document_hash: str
    policy_version: str
    verdict: IntegrityVerdict
    signals: list[IntegritySignal] = Field(default_factory=list)
    normalization_notes: list[str] = Field(default_factory=list)


# F1 outcome for one requirement.
class RequirementMatch(Contract):
    requirement_id: str
    status: RequirementStatus
    explanation: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


# F1 result: matches, eligibility and weighted suitability (Met=1, Partial=0.5, Unmet=0).
class ScreeningResult(Contract):
    application_id: UUID
    job_id: UUID
    job_version: int
    matches: list[RequirementMatch]
    eligible: bool
    screened_out_reason: str | None = None
    suitability: float | None = Field(default=None, ge=0, le=100)
    rank: int | None = None


# One 0-100 dimension score with justification.
class DimensionScore(Contract):
    key: str
    score: float = Field(ge=0, le=100)
    justification: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


# F2 result; aggregate is computed in code, never by the LLM.
class CandidateScore(Contract):
    application_id: UUID
    rubric_id: UUID
    job_version: int
    dimensions: list[DimensionScore]
    aggregate: float = Field(ge=0, le=100)
    input_fingerprint: str
    model_config_snapshot: dict[str, Any] = Field(default_factory=dict)


# F3 interview session record.
class InterviewSession(Contract):
    session_id: UUID
    application_id: UUID
    job_id: UUID
    status: InterviewStatus
    scheduled_at: datetime
    timezone: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    room_name: str | None = None


# F4 finalized, speaker-attributed, timestamped turn (offsets in seconds from session start).
class TranscriptTurn(Contract):
    turn_id: UUID
    session_id: UUID
    participant_id: str
    role: UserRole
    start_offset: float = Field(ge=0)
    end_offset: float = Field(ge=0)
    text: str
    dedup_key: str


# Marks lost audio / failed STT so evidence limits stay visible.
class GapEvent(Contract):
    session_id: UUID
    start_offset: float
    end_offset: float
    reason: Literal["dropped_audio", "stt_failure", "reconnect"]


# One evaluable question-answer unit (supports multi-part and follow-ups).
class QAUnit(Contract):
    unit_id: str
    question_turn_ids: list[UUID]
    answer_turn_ids: list[UUID]
    is_followup: bool = False
    requirement_ids: list[str] = Field(default_factory=list)
    technical: bool = False


# F5 evaluation of one unit across the four criteria with citations.
class AnswerEvaluation(Contract):
    unit_id: str
    relevance: float = Field(ge=0, le=100)
    accuracy: AccuracyVerdict
    accuracy_score: float | None = Field(default=None, ge=0, le=100)
    completeness: float = Field(ge=0, le=100)
    articulation: float = Field(ge=0, le=100)
    justification: str
    evidence: list[EvidenceRef] = Field(min_length=1)


# F5 requirement coverage row.
class RequirementCoverage(Contract):
    requirement_id: str
    level: CoverageLevel
    unit_ids: list[str] = Field(default_factory=list)


# F5 aggregate output.
class InterviewEvaluation(Contract):
    session_id: UUID
    application_id: UUID
    analysis_version: int
    evaluations: list[AnswerEvaluation]
    coverage: list[RequirementCoverage]
    transcript_status: TranscriptStatus
    limitations: list[str] = Field(default_factory=list)


# F6 report; no automated hire/no-hire label (Plan section 8).
class ConsolidatedAssessment(Contract):
    application_id: UUID
    job_id: UUID
    job_version: int
    rubric_version: int
    screening_id: UUID
    score_id: UUID
    evaluation_id: UUID
    integrity_verdict: IntegrityVerdict
    strengths: list[str]
    weaknesses: list[str]
    evidence_gaps: list[str]
    follow_up_areas: list[str]
    discrepancies: list[str] = Field(default_factory=list)
    conclusions: list[str] = Field(description="each conclusion must have >=1 evidence ref")
    conclusion_evidence: dict[int, list[EvidenceRef]] = Field(default_factory=dict)

    # Every conclusion index must be traceable to evidence.
    @model_validator(mode="after")
    def _traceable(self) -> "ConsolidatedAssessment":
        for i in range(len(self.conclusions)):
            if not self.conclusion_evidence.get(i):
                raise ValueError(f"conclusion {i} has no evidence")
        return self


# ---------------------------------------------------------------------------
# 3. HITL APPROVALS AND DURABLE WORK RECORDS
# ---------------------------------------------------------------------------


# Persisted approval request shown in chat and review queue.
class ApprovalRequest(Contract):
    approval_id: UUID
    gate: ApprovalGate
    job_id: UUID | None = None
    application_id: UUID | None = None
    run_id: UUID | None = None
    version: int = Field(ge=1)
    evidence_summary: str
    allowed_options: list[str]
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime


# Human decision; rejected by backend when stale or already decided.
class ApprovalDecision(Contract):
    approval_id: UUID
    expected_version: int
    option: str
    reason: str | None = None
    actor_id: UUID | None = None

    # Chosen option must be non-empty.
    @field_validator("option")
    @classmethod
    def _option_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("option required")
        return v


# Durable work item claimed by the worker with a time-limited lease.
class WorkItem(Contract):
    work_id: UUID
    kind: WorkKind
    thread_id: str
    idempotency_key: str
    status: WorkStatus = WorkStatus.READY
    attempts: int = 0
    max_attempts: int = 3
    next_retry_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    error_category: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------

