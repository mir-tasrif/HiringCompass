import { apiFetch, tokenStore } from "./client";

export interface Interview {
  id: string; application_id: string; job_id: string; job_code: string; job_title: string;
  candidate_name: string; candidate_email: string; scheduled_at: string; timezone_name: string;
  status: string; invitation_sent_at: string | null; candidate_waiting_at: string | null;
  candidate_admitted_at: string | null; candidate_joined_at: string | null;
  interviewer_joined_at: string | null; started_at: string | null; ended_at: string | null;
  questions: { text: string; source: string }[];
  candidate_profile: Record<string, unknown> | null;
  f1: { score: number; requirements: unknown[] } | null;
  f2: { score: number; dimensions: unknown[] } | null;
}

export interface GuestStatus {
  session_id: string; job_title: string; candidate_name: string; scheduled_at: string;
  timezone_name: string; status: string; admitted: boolean; started: boolean;
}
export interface JoinCredentials { url: string; token: string; room: string; role: string }
export interface TranscriptTurn { id: string; role: string; started_at: string; ended_at: string; text: string }

export const listInterviews = (): Promise<Interview[]> => apiFetch("/interviews");
export const getInterview = (id: string): Promise<Interview> => apiFetch(`/interviews/${id}`);
export const scheduleInterview = (applicationId: string, candidateEmail: string, scheduledAt: string, timezoneName: string): Promise<Interview> =>
  apiFetch("/interviews", { method: "POST", body: JSON.stringify({ application_id: applicationId, candidate_email: candidateEmail, scheduled_at: scheduledAt, timezone_name: timezoneName }) });
export const rescheduleInterview = (id: string, candidateEmail: string, scheduledAt: string, timezoneName: string): Promise<Interview> =>
  apiFetch(`/interviews/${id}`, { method: "PATCH", body: JSON.stringify({ candidate_email: candidateEmail, scheduled_at: scheduledAt, timezone_name: timezoneName }) });
export const updateQuestions = (id: string, questions: string[]): Promise<Interview> =>
  apiFetch(`/interviews/${id}/questions`, { method: "PATCH", body: JSON.stringify({ questions }) });
export const regenerateQuestions = (id: string): Promise<Interview> =>
  apiFetch(`/interviews/${id}/questions/regenerate`, { method: "POST" });
export const resendInvitation = (id: string): Promise<Interview> => apiFetch(`/interviews/${id}/resend`, { method: "POST" });
export const admitInterview = (id: string): Promise<Interview> => apiFetch(`/interviews/${id}/admit`, { method: "POST" });
export const startInterview = (id: string): Promise<Interview> => apiFetch(`/interviews/${id}/start`, { method: "POST", body: JSON.stringify({ consent: true }) });
export const endInterview = (id: string): Promise<Interview> => apiFetch(`/interviews/${id}/end`, { method: "POST" });
export const recruiterJoin = (id: string): Promise<JoinCredentials> =>
  apiFetch(`/interviews/${id}/room-token`, { method: "POST", body: JSON.stringify({ consent: true }) });
export const listTranscript = (id: string): Promise<TranscriptTurn[]> => apiFetch(`/interviews/${id}/turns`);
export const askInterviewAssistant = (id: string, question: string): Promise<{ answer: string }> =>
  apiFetch(`/interviews/${id}/assist`, { method: "POST", body: JSON.stringify({ question }) });

async function guestFetch<T>(path: string, invitation: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Interview-Token", invitation);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/interviews/guest/${path}`, { ...init, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail ?? "Interview request failed.");
  return data as T;
}

export const guestStatus = (invitation: string): Promise<GuestStatus> => guestFetch("status", invitation);
export const enterWaitingRoom = (invitation: string): Promise<GuestStatus> =>
  guestFetch("wait", invitation, { method: "POST", body: JSON.stringify({ consent: true }) });
export const guestJoin = (invitation: string): Promise<JoinCredentials> =>
  guestFetch("join", invitation, { method: "POST", body: JSON.stringify({ consent: true }) });

export async function sendAudioSegment(role: "candidate" | "interviewer", idOrInvitation: string,
  chunkId: string, startedAt: string, endedAt: string, blob: Blob): Promise<void> {
  const body = new FormData();
  body.set("chunk_id", chunkId); body.set("started_at", startedAt); body.set("ended_at", endedAt);
  body.set("file", blob, blob.type.includes("ogg") ? "segment.ogg" : "segment.webm");
  const headers: Record<string, string> = {};
  if (role === "candidate") headers["X-Interview-Token"] = idOrInvitation;
  else if (tokenStore.get()) headers.Authorization = `Bearer ${tokenStore.get()}`;
  const url = role === "candidate" ? "/api/interviews/guest/segments" : `/api/interviews/${idOrInvitation}/audio`;
  const response = await fetch(url, { method: "POST", headers, body });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail ?? "Transcription is temporarily unavailable.");
  }
}
