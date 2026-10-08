import { ApiError, apiFetch, tokenStore } from "./client";

export interface ApprovedJob {
  id: string;
  public_code: string;
  title: string;
  version: number;
  summary: string;
  location: string | null;
  employment_type: string | null;
  jd_text: string;
  updated_at: string;
  posted_platforms: string[];
  archived: boolean;
}

export interface JobDetail extends ApprovedJob {
  profile: Record<string, unknown>;
}

export interface CvSubmission {
  id: string;
  candidate_number: number;
  candidate_name: string;
  original_filename: string | null;
  size_bytes: number;
  status: string;
  converted_from_docx: boolean;
  uploaded_at: string;
}

export interface CvUploadResult {
  filename: string;
  success: boolean;
  application_id: string | null;
  candidate_number: number | null;
  candidate_name: string | null;
  error: string | null;
}

export const listApprovedJobs = (): Promise<ApprovedJob[]> => apiFetch("/jobs");
export const listExpiredJobs = (): Promise<ApprovedJob[]> => apiFetch("/jobs/expired");
export const archiveJob = (id: string): Promise<{ id: string; archived: boolean }> =>
  apiFetch(`/jobs/${id}`, { method: "DELETE" });
export const restoreJob = (id: string): Promise<{ id: string; archived: boolean }> =>
  apiFetch(`/jobs/${id}/restore`, { method: "POST" });
export const getJob = (id: string): Promise<JobDetail> => apiFetch(`/jobs/${id}`);
export const postJob = (id: string): Promise<{ status: string; channel: string; version: number }> =>
  apiFetch(`/jobs/${id}/post`, { method: "POST" });
export const listSubmissions = (jobId: string): Promise<CvSubmission[]> => apiFetch(`/jobs/${jobId}/applications`);

export async function uploadCvFiles(jobId: string, files: File[]): Promise<CvUploadResult[]> {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  return apiFetch(`/jobs/${jobId}/applications`, { method: "POST", body });
}

export const deleteSubmissions = (jobId: string, ids: string[], reason: string): Promise<{ rejected_ids: string[] }> =>
  apiFetch(`/jobs/${jobId}/applications`, { method: "DELETE", body: JSON.stringify({ application_ids: ids, reason }) });

export async function loadCvPreview(jobId: string, applicationId: string): Promise<Blob> {
  const token = tokenStore.get();
  const response = await fetch(`/api/jobs/${jobId}/applications/${applicationId}/file`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && token) window.dispatchEvent(new Event("hc:unauthorized"));
    throw new ApiError(response.status, data.detail ?? "Could not open this CV.", data.code);
  }
  return response.blob();
}
