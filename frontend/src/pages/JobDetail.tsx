import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import FormattedJd from "../components/FormattedJd";
import { getJob, JobDetail, postJob } from "../api/jobs";

export default function JobDetailPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    getJob(jobId).then(setJob).catch((err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Could not load this job.");
    });
  }, [jobId]);

  const copy = async () => {
    if (!job) return;
    try {
      await navigator.clipboard.writeText(job.jd_text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("Clipboard access was blocked by the browser.");
    }
  };

  const post = async () => {
    if (!job || posting) return;
    setPosting(true);
    setError(null);
    try {
      const result = await postJob(job.id);
      setNotice(result.status === "already_posted" ? "This version is already posted to Discord." : `Posted to Discord ${result.channel}.`);
      setJob(await getJob(job.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not post this job.");
    } finally {
      setPosting(false);
    }
  };

  if (error && !job) return <section className="card empty-state"><p className="error">{error}</p><button type="button" className="secondary" onClick={() => navigate("/jobs")}>Back to Jobs</button></section>;
  if (!job) return <section className="card empty-state">Loading job description…</section>;

  const posted = job.posted_platforms.includes("discord");
  return (
    <div className="job-detail-page">
      <Link className="back-link" to="/jobs">← All jobs</Link>
      <header className="detail-heading">
        <div>
          <div className="detail-kickers"><span className="job-code">{job.public_code}</span><span className={job.archived ? "expired-tag" : "version-tag"}>{job.archived ? "Expired" : "Approved"} · v{job.version}</span></div>
          <h1>{job.title}</h1>
          <p className="muted">{[job.location, job.employment_type].filter(Boolean).join(" · ")}</p>
        </div>
      </header>
      {(error || notice) && <p className={error ? "error" : "notice"} role={error ? "alert" : "status"}>{error || notice}</p>}
      <article className="card full-jd">
        <div className="full-jd-label"><span>APPROVED JOB DESCRIPTION</span><span>Version {job.version}</span></div>
        <FormattedJd text={job.jd_text} />
      </article>
      <footer className="job-detail-actions">
        <button type="button" className="secondary" onClick={() => void copy()}>{copied ? "Copied" : "Copy"}</button>
        {!job.archived && <button type="button" onClick={() => void post()} disabled={posting || posted}>
          {posting ? "Posting…" : posted ? "Posted to Discord" : "Post to Hiring Sites"}
        </button>}
        <button type="button" className="secondary" onClick={() => navigate(`/jobs/${job.id}/updates`)}>See Updates</button>
      </footer>
    </div>
  );
}
