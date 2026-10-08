// Typed client for the assistant chat API.
import { apiFetch } from "./client";

export type MessagePayload =
  | { type: "text" }
  | { type: "question"; field: string | null; options: string[] }
  | { type: "draft"; approval_id: string; version: number; title: string; jd_text: string; sources: string[];
      profile?: { summary?: string; about_company?: string; responsibilities?: string[];
        requirements?: { text: string; kind: "mandatory" | "preferred" }[]; benefits?: string[];
        employment_type?: string | null; location?: string | null };
      company_name?: string; google_form_link?: string }
  | { type: "approved"; job_id: string; job_code?: string; version: number; jd_text: string | null; posting_available?: boolean; posting_platforms?: string[] }
  | { type: "posting_offer"; platforms: string[] }
  | { type: "posting_result"; platform: string; status: "posted" | "already_posted" | "failed"; version?: number; channel?: string }
  | { type: "job_update"; jobs: { job_id: string; job_code: string | null; title: string; version: number; cv_submissions: number; cv_pipeline: { uploaded: number; integrity_check: number; integrity_review: number; parsed: number; ranking: number; f1_review: number; scoring: number; rejected: number }; posting_status: string; posting_configured: boolean; can_post: boolean; archived: boolean; postings: { platform: string; status: string }[] }[] }
  | { type: "candidate_work"; route: string; job_id?: string; job_code?: string | null; kind?: "integrity" | "ranking" | "review"; batch_id?: string; awaiting_confirmation?: string | null; application_ids?: string[]; review_required?: boolean; evidence_summary?: string }
  | { type: "decision"; option: string }
  | { type: "error" };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  payload: MessagePayload | null;
  created_at: string;
}

export interface ThreadSummary {
  id: string;
  title: string;
  state: "interviewing" | "awaiting_decision" | "approved";
  status: "idle" | "working";
  updated_at: string;
}

export interface ThreadDetail extends ThreadSummary {
  job_id: string | null;
  messages: ChatMessage[];
}

// Start a new job conversation.
export const createThread = (): Promise<ThreadDetail> => apiFetch<ThreadDetail>("/chat/threads", { method: "POST" });

// List conversations, newest first.
export const listThreads = (): Promise<ThreadSummary[]> => apiFetch<ThreadSummary[]>("/chat/threads");

// Read one conversation (polled while the assistant works).
export const getThread = (id: string): Promise<ThreadDetail> => apiFetch<ThreadDetail>(`/chat/threads/${id}`);

export const deleteThread = (id: string): Promise<void> => apiFetch<void>(`/chat/threads/${id}`, { method: "DELETE" });

// Send a typed message or a clicked option; the reply arrives asynchronously.
export const sendMessage = (id: string, content: string): Promise<ChatMessage> =>
  apiFetch<ChatMessage>(`/chat/threads/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) });

// Approve or regenerate the draft shown on screen.
export const sendDecision = (id: string, option: "approve" | "regenerate", approvalId: string, version: number): Promise<ChatMessage> =>
  apiFetch<ChatMessage>(`/chat/threads/${id}/decision`, {
    method: "POST",
    body: JSON.stringify({ option, approval_id: approvalId, version }),
  });
