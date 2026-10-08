import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  ApprovedJob,
  archiveJob,
  CvUploadResult,
  listApprovedJobs,
  listExpiredJobs,
  restoreJob,
  uploadCvFiles,
} from "../api/jobs";

type JobsSection = "approved" | "expired";

export default function Jobs() {
  const [section, setSection] = useState<JobsSection>("approved");
  const [jobs, setJobs] = useState<ApprovedJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [results, setResults] = useState<CvUploadResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [actionJobId, setActionJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    const request = section === "approved" ? listApprovedJobs() : listExpiredJobs();
    request.then((items) => {
      if (!active) return;
      setJobs(items);
      setSelectedJobId((current) => items.some((job) => job.id === current) ? current : items[0]?.id ?? "");
    }).catch((err: unknown) => {
      if (active) setError(err instanceof ApiError ? err.message : `Could not load ${section} jobs.`);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [section]);

  const selectedJob = jobs.find((job) => job.id === selectedJobId);
  const selectFiles = (event: ChangeEvent<HTMLInputElement>) => setFiles(Array.from(event.target.files ?? []));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (section !== "approved" || !selectedJobId || !files.length || uploading) return;
    setUploading(true);
    setError(null);
    try {
      const uploaded = await uploadCvFiles(selectedJobId, files);
      setResults(uploaded);
      setFiles([]);
      const input = document.getElementById("cv-upload-input") as HTMLInputElement | null;
      if (input) input.value = "";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "CV upload failed. Please try again.");
    } finally {
      setUploading(false);
    }
  };

  const changeSection = (next: JobsSection) => {
    setError(null);
    setNotice(null);
    if (next === "expired") {
      setFiles([]);
      setResults([]);
      const input = document.getElementById("cv-upload-input") as HTMLInputElement | null;
      if (input) input.value = "";
    }
    setSection(next);
  };

  const archive = async (job: ApprovedJob) => {
    if (!window.confirm(`Move “${job.title}” to Expired Jobs? Its job description, posting history, and CVs will be kept. New CV uploads and posting will be disabled.`)) return;
    setActionJobId(job.id);
    setError(null);
    setNotice(null);
    try {
      await archiveJob(job.id);
      setJobs((current) => current.filter((item) => item.id !== job.id));
      setSelectedJobId((current) => current === job.id ? jobs.find((item) => item.id !== job.id)?.id ?? "" : current);
      setFiles([]);
      setResults([]);
      const input = document.getElementById("cv-upload-input") as HTMLInputElement | null;
      if (input) input.value = "";
      setNotice(`${job.title} was moved to Expired Jobs. Its JD and CV history were preserved.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not move this job to Expired Jobs.");
    } finally {
      setActionJobId(null);
    }
  };

  const restore = async (job: ApprovedJob) => {
    setActionJobId(job.id);
    setError(null);
    setNotice(null);
    try {
      await restoreJob(job.id);
      setJobs((current) => current.filter((item) => item.id !== job.id));
      setNotice(`${job.title} was restored to Approved Jobs and is available for uploads again.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not restore this job.");
    } finally {
      setActionJobId(null);
    }
  };

  const approvedView = section === "approved";

  return (
    <div className="jobs-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">HIRING WORKSPACE</p>
          <div className="jobs-heading-row">
            <h1>{approvedView ? "Approved jobs" : "Expired jobs"}</h1>
            <div className="jobs-tabs" role="tablist" aria-label="Job status">
              <button type="button" role="tab" aria-selected={approvedView} className={approvedView ? "active" : "secondary"} onClick={() => changeSection("approved")}>Approved Jobs</button>
              <button type="button" role="tab" aria-selected={!approvedView} className={!approvedView ? "active" : "secondary"} onClick={() => changeSection("expired")}>Expired Jobs</button>
            </div>
          </div>
          <p className="muted">{approvedView ? "Manage approved roles and collect CVs in one place." : "Review archived jobs and restore them when hiring resumes."}</p>
        </div>
        <Link className="button-link" to="/assistant">Create a job description</Link>
      </header>

      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      <div className={`jobs-layout${approvedView ? "" : " expired-layout"}`}>
        <section className="jobs-list" aria-label={approvedView ? "Approved job descriptions" : "Expired job descriptions"}>
          {loading ? <div className="card empty-state">Loading {section} jobs…</div> : jobs.length === 0 ? (
            <div className="card empty-state">
              <span className="empty-icon" aria-hidden="true">✦</span>
              <h2>{approvedView ? "Your approved JDs will appear here" : "No expired jobs"}</h2>
              <p className="muted">{approvedView ? "Create a job description with the AI assistant and approve it to get started." : "Jobs moved out of Approved Jobs will appear here."}</p>
              {approvedView && <Link className="button-link" to="/assistant">Open AI Assistant</Link>}
            </div>
          ) : jobs.map((job) => (
            <article className="job-card card" key={job.id}>
              <Link className="job-card-link" to={`/jobs/${job.id}`}>
                <div className="job-card-top">
                  <span className="job-code">{job.public_code}</span>
                  <span className="version-tag">Version {job.version}</span>
                </div>
                <h2>{job.title}</h2>
                <p className="job-summary">{job.summary || "Approved job description"}</p>
                <div className="job-card-footer">
                  <span>{[job.location, job.employment_type].filter(Boolean).join(" · ") || (approvedView ? "Approved" : "Expired")}</span>
                  <span className="job-card-open">View job <span aria-hidden="true">→</span></span>
                </div>
              </Link>
              <div className="job-card-actions">
                {approvedView ? (
                  <button type="button" className="danger-button" disabled={actionJobId !== null} onClick={() => void archive(job)}>
                    {actionJobId === job.id ? "Moving…" : "Delete job"}
                  </button>
                ) : (
                  <button type="button" disabled={actionJobId !== null} onClick={() => void restore(job)}>
                    {actionJobId === job.id ? "Restoring…" : "Restore job"}
                  </button>
                )}
              </div>
            </article>
          ))}
        </section>

        {approvedView && <aside className="card upload-panel" aria-label="Upload candidate CVs">
          <div className="upload-icon" aria-hidden="true">↑</div>
          <p className="eyebrow">CANDIDATE PIPELINE</p>
          <h2>Upload CVs</h2>
          <p className="muted">Attach one or more PDF or DOCX CVs to an approved role.</p>
          <form onSubmit={submit} className="upload-form">
            <label htmlFor="job-select">Select a job</label>
            <select id="job-select" value={selectedJobId} onChange={(event) => setSelectedJobId(event.target.value)} disabled={!jobs.length || uploading}>
              {jobs.map((job) => <option key={job.id} value={job.id}>{job.public_code} · {job.title}</option>)}
            </select>
            <label className="file-drop" htmlFor="cv-upload-input">
              <span className="file-drop-icon" aria-hidden="true">＋</span>
              <strong>{files.length ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "Choose CV files"}</strong>
              <small>PDF or DOCX · Multiple files allowed</small>
            </label>
            <input id="cv-upload-input" type="file" accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" multiple onChange={selectFiles} disabled={!jobs.length || uploading} />
            {files.length > 0 && <ul className="selected-files">{files.map((file) => <li key={`${file.name}-${file.size}`}>{file.name}</li>)}</ul>}
            <button type="submit" disabled={!selectedJobId || !files.length || uploading}>
              {uploading ? "Uploading…" : "Upload to this job"}
            </button>
          </form>
          {selectedJob && <p className="upload-target">CVs will be saved under <strong>{selectedJob.public_code} · {selectedJob.title}</strong>.</p>}
          {results.length > 0 && <div className="upload-results" role="status">
            {results.map((result) => <p key={`${result.filename}-${result.application_id ?? "failed"}`} className={result.success ? "upload-success" : "error"}>
              {result.success ? `${selectedJob?.public_code} · ${selectedJob?.title} · ${result.candidate_name}` : `${result.filename}: ${result.error}`}
            </p>)}
          </div>}
        </aside>}
      </div>
    </div>
  );
}
