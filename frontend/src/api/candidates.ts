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
  size_bytes: number;
  uploaded_at: string;
  stage: string;
  rejection_action: string | null;
  rejection_reason: string | null;
  rejected_from_stage: string | null;
  rejected_at: string | null;
  events: CandidateEvent[];
}

export type CandidateAction = "delete" | "reject";

export const listCandidateCvs = (section: "uploaded" | "rejected"): Promise<CandidateCv[]> =>
  apiFetch(`/candidates?section=${section}`);

export const actOnCandidateCvs = (applicationIds: string[], action: CandidateAction, reason: string): Promise<{ rejected_ids: string[] }> =>
  apiFetch("/candidates/actions", {
    method: "POST",
    body: JSON.stringify({ application_ids: applicationIds, action, reason }),
  });
