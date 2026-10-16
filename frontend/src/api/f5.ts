// F5 API: roster, durable analysis batches, transcript corrections, and human review.
import { apiFetch } from "./client";

export interface F5Ready {
  application_id: string; candidate_name: string; candidate_number: number; job_id: string;
  job_code: string; job_title: string; interview_id: string; analysis_status: string; run_id: string | null;
}
export interface F5Evaluation {
  question: string; answer: string; relation: string; evaluable: boolean; scores?: Record<string, number> | null;
  overall_score?: number; provisional?: boolean; justification?: string; answer_evidence?: string;
  question_turn_ids: string[]; answer_turn_ids: string[];
  question_started_at?: string | null; answer_started_at?: string | null;
  references?: { id: string; title: string; uri: string | null; version: string | null; text: string }[];
}
export interface F5Run {
  id: string; batch_id: string; application_id: string; interview_id: string; job_version: number;
  status: string; review_status: string; review_reason: string | null; error: string | null;
  progress: { completed: number; total: number } | null;
  next_retry_at: string | null;
  transcript: { id: string; role: string; text: string; started_at: string }[];
  segment_overrides: F5Group[];
  corrections: { turn_id: string; before: string; after: string; actor_id: string; at: string }[];
  result: { evaluations: F5Evaluation[]; coverage: { requirement: string; kind: string; status: string; segment_indexes: number[] }[];
    overall_score: number | null; provisional: boolean; prompt_versions: Record<string, string> } | null;
  created_at: string | null;
}
export interface F5Group { question_turns: number[]; answer_turns: number[]; relation: string; evaluable: boolean; requirement_indexes: number[] }
export const f5Roster = (): Promise<F5Ready[]> => apiFetch("/f5/ready");
export const f5Runs = (): Promise<F5Run[]> => apiFetch("/f5/runs");
export const startF5 = (applicationIds: string[]): Promise<{ id: string; status: string }> =>
  apiFetch("/f5/batches", { method: "POST", body: JSON.stringify({ application_ids: applicationIds }) });
export const correctF5Turn = (runId: string, turnId: string, text: string): Promise<F5Run> =>
  apiFetch(`/f5/runs/${runId}/corrections`, { method: "POST", body: JSON.stringify({ turn_id: turnId, text }) });
export const correctF5Grouping = (runId: string, segments: F5Group[]): Promise<F5Run> =>
  apiFetch(`/f5/runs/${runId}/grouping`, { method: "POST", body: JSON.stringify({ segments }) });
export const reviewF5 = (runId: string, decision: "approve" | "reanalyse" | "reject_candidate", reason: string): Promise<F5Run> =>
  apiFetch(`/f5/runs/${runId}/review`, { method: "POST", body: JSON.stringify({ decision, reason }) });
