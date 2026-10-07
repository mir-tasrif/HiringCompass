import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { ApprovedJob, CvSubmission, deleteSubmissions, getJob, listSubmissions, loadCvPreview } from "../api/jobs";

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function JobSubmissions() {
  const { jobId = "" } = useParams();
  const [job, setJob] = useState<ApprovedJob | null>(null);
  const [items, setItems] = useState<CvSubmission[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const [jobData, submissions] = await Promise.all([getJob(jobId), listSubmissions(jobId)]);
    setJob(jobData);
    setItems(submissions);
    setPreviewId((current) => submissions.some((item) => item.id === current) ? current : submissions[0]?.id ?? null);
    setSelected((current) => new Set([...current].filter((id) => submissions.some((item) => item.id === id))));
  };

  useEffect(() => {
    refresh().catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Could not load CV submissions."))
      .finally(() => setLoading(false));
  }, [jobId]);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setPreviewUrl(null);
    if (previewId) {
      loadCvPreview(jobId, previewId).then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (active) setPreviewUrl(objectUrl);
        else URL.revokeObjectURL(objectUrl);
      }).catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "Could not open this CV preview.");
      });
    }
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId, previewId]);

  const toggle = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const selectAll = () => setSelected(selected.size === items.length ? new Set() : new Set(items.map((item) => item.id)));

  const remove = async () => {
    if (!selected.size || deleting) return;
    const reason = window.prompt(`Move ${selected.size} selected CV${selected.size === 1 ? "" : "s"} to Rejected. They will remain stored with a decision log. Enter a reason:`);
    if (!reason?.trim()) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteSubmissions(jobId, [...selected], reason.trim());
      setSelected(new Set());
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not move the selected CVs to Rejected.");
    } finally {
      setDeleting(false);
    }
  };

  const activePreview = items.find((item) => item.id === previewId);
  const allSelected = items.length > 0 && selected.size === items.length;

  if (loading) return <section className="card empty-state">Loading CV submissions…</section>;
  if (!job) return <section className="card empty-state"><p className="error">{error || "Job not found."}</p><Link to="/jobs">Back to Jobs</Link></section>;

  return (
    <div className="submissions-page">
      <Link className="back-link" to={`/jobs/${job.id}`}>← Back to job</Link>
      <header className="submissions-heading">
        <div>
          <p className="eyebrow">{job.public_code} · {job.title}</p>
          <h1>CV submissions</h1>
          <p className="muted">{items.length} CV{items.length === 1 ? "" : "s"} attached to this job</p>
        </div>
        <button type="button" className="secondary" disabled title="CV parsing will be available in a future update">Proceed for CV Parsing · Coming soon</button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="submissions-layout">
        <section className="card cv-list" aria-label="CV submissions list">
          <div className="cv-list-toolbar">
            <label className="select-all"><input type="checkbox" checked={allSelected} onChange={selectAll} disabled={!items.length} /> Select all</label>
            <button type="button" className="danger-button" onClick={() => void remove()} disabled={!selected.size || deleting} title="Moves CVs to Rejected with a reason; files are retained.">
              {deleting ? "Moving…" : `Delete${selected.size ? ` (${selected.size})` : ""}`}
            </button>
          </div>
          {items.length === 0 ? <div className="empty-state compact"><h2>No CVs yet</h2><p className="muted">Uploaded CVs for this job will appear here.</p></div> : (
            <ul className="cv-submission-list">
              {items.map((item) => (
                <li className={item.id === previewId ? "cv-submission active" : "cv-submission"} key={item.id}>
                  <input aria-label={`Select ${item.candidate_name}`} type="checkbox" checked={selected.has(item.id)} onChange={() => toggle(item.id)} />
                  <button type="button" className="cv-open" onClick={() => setPreviewId(item.id)}>
                    <strong>{job.public_code} · {job.title} · {item.candidate_name}</strong>
                    <span>{item.original_filename || `${item.candidate_name}.pdf`}</span>
                    <small>{formatBytes(item.size_bytes)} · {new Date(item.uploaded_at).toLocaleDateString()}</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card cv-preview" aria-label="CV preview">
          {activePreview ? (
            <>
              <div className="preview-heading"><div><strong>{activePreview.candidate_name}</strong><span className="muted">{activePreview.original_filename}</span></div><span className="file-badge">PDF</span></div>
              {previewUrl ? <iframe title={`CV preview: ${activePreview.candidate_name}`} src={previewUrl} /> : <div className="preview-placeholder">Loading CV preview…</div>}
            </>
          ) : <div className="preview-placeholder"><span aria-hidden="true">▧</span><p>Select a CV to preview it here.</p></div>}
        </section>
      </div>
    </div>
  );
}
