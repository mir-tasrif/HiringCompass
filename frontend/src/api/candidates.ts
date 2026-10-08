import { apiFetch } from "./client";

export interface CandidateEvent {
  action: "deleted" | "rejected" | string;
  from_stage: string;
  to_stage: string;
  reason: string;
  created_at: string;
}

export interface CandidateCv {
  id: string;
  job_id: string;
  job_code: string;
  job_title: string;
  job_version: number;
  candidate_number: number;
  candidate_name: string;
  source: string;
  original_filename: string | null;
  converted_from_docx: boolean;
  size_bytes: number;
  uploaded_at: string;
  stage: string;
  rejection_action: string | null;
  rejection_reason: string | null;
  rejected_from_stage: string | null;
  rejected_at: string | null;
  events: CandidateEvent[];
  parsed_profile: Record<string, unknown> | null;
  integrity_verdict: string | null;
  integrity_signals: { code: string; severity: string; evidence: string; page?: number }[];
  extraction_method: string | null;
  screening: { suitability_score: number; mandatory_pass: boolean; requirements: { text: string; kind: string; status: string; evidence: string; explanation: string }[]; explanation: string; rank: number | null } | null;
  integrity_task_id: string | null;
  integrity_task_status: string | null;
  integrity_task_error: string | null;
}

export type CandidateAction = "delete" | "reject" | "permanent_delete";
export type CandidateSection = "uploaded" | "integrity" | "parsed" | "ranking" | "scoring" | "interview" | "assessment" | "final" | "review" | "rejected";

export interface CvBatch {
  id: string;
  created_at: string;
  kind: string;
  status: string;
  total: number;
  completed: number;
  failed: number;
  waiting_review: number;
  rejected: number;
  items: { id: string; application_id: string; status: string; attempts: number; error: string | null }[];
}

export const listCandidateCvs = (section: CandidateSection): Promise<CandidateCv[]> =>
  apiFetch(`/candidates?section=${section}`);

export const startCvBatch = (applicationIds: string[], kind: "integrity" | "ranking"): Promise<{ batches: CvBatch[] }> =>
  apiFetch(`/candidates/batches/${kind}`, { method: "POST", body: JSON.stringify({ application_ids: applicationIds }) });

export const listActiveCvBatches = (): Promise<CvBatch[]> => apiFetch("/candidates/batches");
export const getCvBatch = (batchId: string): Promise<CvBatch> => apiFetch(`/candidates/batches/${batchId}`);
export const retryCvBatchItem = (itemId: string): Promise<{ id: string; status: string }> =>
  apiFetch(`/candidates/batch-items/${itemId}/retry`, { method: "POST" });
export const decideCandidateReview = (applicationId: string, decision: "approve" | "reject", reason: string): Promise<{ application_id: string; stage: string; decision: string }> =>
  apiFetch(`/candidates/${applicationId}/review`, { method: "POST", body: JSON.stringify({ decision, reason }) });

export const actOnCandidateCvs = (applicationIds: string[], action: CandidateAction, reason: string): Promise<{ rejected_ids: string[] }> =>
  apiFetch("/candidates/actions", {
    method: "POST",
    body: JSON.stringify({ application_ids: applicationIds, action, reason }),
  });
