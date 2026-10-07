import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { ApprovedJob, CvUploadResult, listApprovedJobs, uploadCvFiles } from "../api/jobs";

export default function Jobs() {
  const [jobs, setJobs] = useState<ApprovedJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [results, setResults] = useState<CvUploadResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listApprovedJobs().then((items) => {
      setJobs(items);
      if (items.length) setSelectedJobId(items[0].id);
    }).catch((err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not load approved jobs.");
    }).finally(() => setLoading(false));
  }, []);

  const selectedJob = jobs.find((job) => job.id === selectedJobId);
  const selectFiles = (event: ChangeEvent<HTMLInputElement>) => setFiles(Array.from(event.target.files ?? []));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedJobId || !files.length || uploading) return;
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

  return (
    <div className="jobs-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">HIRING WORKSPACE</p>
          <h1>Approved jobs</h1>
          <p className="muted">Manage published roles and collect CVs in one place.</p>
        </div>
        <Link className="button-link" to="/assistant">Create a job description</Link>
      </header>

      {error && <p className="error" role="alert">{error}</p>}
      <div className="jobs-layout">
        <section className="jobs-list" aria-label="Approved job descriptions">
          {loading ? <div className="card empty-state">Loading approved jobs…</div> : jobs.length === 0 ? (
            <div className="card empty-state">
              <span className="empty-icon" aria-hidden="true">✦</span>
              <h2>Your approved JDs will appear here</h2>
              <p className="muted">Create a job description with the AI assistant and approve it to get started.</p>
              <Link className="button-link" to="/assistant">Open AI Assistant</Link>
            </div>
          ) : jobs.map((job) => (
            <Link className="job-card card" to={`/jobs/${job.id}`} key={job.id}>
              <div className="job-card-top">
                <span className="job-code">{job.public_code}</span>
                <span className="version-tag">Version {job.version}</span>
              </div>
              <h2>{job.title}</h2>
              <p className="job-summary">{job.summary || "Approved job description"}</p>
              <div className="job-card-footer">
                <span>{[job.location, job.employment_type].filter(Boolean).join(" · ") || "Approved"}</span>
                <span className="job-card-open">View job <span aria-hidden="true">→</span></span>
              </div>
            </Link>
          ))}
        </section>

        <aside className="card upload-panel" aria-label="Upload candidate CVs">
          <div className="upload-icon" aria-hidden="true">↑</div>
          <p className="eyebrow">CANDIDATE PIPELINE</p>
          <h2>Upload CVs</h2>
          <p className="muted">Attach one or more PDF CVs to an approved role.</p>
          <form onSubmit={submit} className="upload-form">
            <label htmlFor="job-select">Select a job</label>
            <select id="job-select" value={selectedJobId} onChange={(event) => setSelectedJobId(event.target.value)} disabled={!jobs.length || uploading}>
              {jobs.map((job) => <option key={job.id} value={job.id}>{job.public_code} · {job.title}</option>)}
            </select>
            <label className="file-drop" htmlFor="cv-upload-input">
              <span className="file-drop-icon" aria-hidden="true">＋</span>
              <strong>{files.length ? `${files.length} PDF${files.length === 1 ? "" : "s"} selected` : "Choose CV files"}</strong>
              <small>PDF only · Multiple files allowed</small>
            </label>
            <input id="cv-upload-input" type="file" accept="application/pdf,.pdf" multiple onChange={selectFiles} disabled={!jobs.length || uploading} />
            {files.length > 0 && <ul className="selected-files">{files.map((file) => <li key={`${file.name}-${file.size}`}>{file.name}</li>)}</ul>}
            <button type="submit" disabled={!selectedJobId || !files.length || uploading}>
              {uploading ? "Uploading…" : "Upload to this job"}
            </button>
          </form>
          {selectedJob && <p className="upload-target">CVs will be saved under <strong>{selectedJob.public_code} · {selectedJob.title}</strong>.</p>}
          {results.length > 0 && (
            <div className="upload-results" role="status">
              {results.map((result) => <p key={`${result.filename}-${result.application_id ?? "failed"}`} className={result.success ? "upload-success" : "error"}>
                {result.success ? `${selectedJob?.public_code} · ${selectedJob?.title} · ${result.candidate_name}` : `${result.filename}: ${result.error}`}
              </p>)}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
