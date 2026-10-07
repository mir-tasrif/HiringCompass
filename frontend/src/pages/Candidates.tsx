import { FormEvent, useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { actOnCandidateCvs, CandidateAction, CandidateCv, listCandidateCvs } from "../api/candidates";
import { loadCvPreview } from "../api/jobs";

const sections = [
  { id: "uploaded", label: "Uploaded CV", active: true },
  { id: "parsed", label: "Parsed CV", active: false },
  { id: "ranking", label: "Feature 1 Ranking", active: false },
  { id: "scoring", label: "Feature 2 Scoring", active: false },
  { id: "interview", label: "Selected for Interview", active: false },
  { id: "assessment", label: "Feature 6 Assessment", active: false },
  { id: "final", label: "Final Selection", active: false },
  { id: "rejected", label: "Rejected", active: true },
] as const;

type ActiveSection = "uploaded" | "rejected";

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function stageLabel(stage: string) {
  return stage.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function Candidates() {
  const [section, setSection] = useState<ActiveSection>("uploaded");
  const [items, setItems] = useState<CandidateCv[]>([]);
  const [counts, setCounts] = useState({ uploaded: 0, rejected: 0 });
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [action, setAction] = useState<CandidateAction | null>(null);
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const [uploaded, rejected] = await Promise.all([listCandidateCvs("uploaded"), listCandidateCvs("rejected")]);
    setCounts({ uploaded: uploaded.length, rejected: rejected.length });
    const nextItems = section === "uploaded" ? uploaded : rejected;
    setItems(nextItems);
    setPreviewId((current) => nextItems.some((item) => item.id === current) ? current : nextItems[0]?.id ?? null);
    setSelected((current) => new Set([...current].filter((id) => nextItems.some((item) => item.id === id))));
  };

  useEffect(() => {
    setLoading(true);
    refresh().catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Could not load candidates."))
      .finally(() => setLoading(false));
  }, [section]);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setPreviewUrl(null);
    const item = items.find((candidate) => candidate.id === previewId);
    if (item) {
      loadCvPreview(item.job_id, item.id).then((blob) => {
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
  }, [items, previewId]);

  const visibleItems = useMemo(() => {
    const search = query.trim().toLowerCase();
    if (!search) return items;
    return items.filter((item) => `${item.job_code} ${item.job_title} ${item.candidate_name} ${item.original_filename ?? ""}`.toLowerCase().includes(search));
  }, [items, query]);

  const toggle = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const selectAll = () => setSelected(selected.size === visibleItems.length ? new Set() : new Set(visibleItems.map((item) => item.id)));

  const submitAction = async (event: FormEvent) => {
    event.preventDefault();
    if (!action || !selected.size || !reason.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      await actOnCandidateCvs([...selected], action, reason.trim());
      setAction(null);
      setReason("");
      setSelected(new Set());
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the selected CVs.");
    } finally {
      setSaving(false);
    }
  };

  const activePreview = items.find((item) => item.id === previewId);
  const allSelected = visibleItems.length > 0 && visibleItems.every((item) => selected.has(item.id));

  return (
    <div className="candidates-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">TALENT PIPELINE</p>
          <h1>Candidates</h1>
          <p className="muted">Review CVs and track candidates through each hiring stage.</p>
        </div>
      </header>

      <nav className="candidate-sections" aria-label="Candidate pipeline sections">
        {sections.map((item) => {
          const isActive = item.id === section;
          const count = item.id === "uploaded" ? counts.uploaded : item.id === "rejected" ? counts.rejected : null;
          return (
            <button
              key={item.id}
              type="button"
              className={`candidate-section${isActive ? " active" : ""}${item.active ? "" : " upcoming"}`}
              disabled={!item.active}
              onClick={() => item.active && setSection(item.id as ActiveSection)}
              aria-current={isActive ? "page" : undefined}
              title={item.active ? undefined : "This pipeline step will be available in a future update."}
            >
              <span>{item.label}</span>
              {count !== null ? <span className="candidate-count">{count}</span> : <small>Coming soon</small>}
            </button>
          );
        })}
      </nav>

      <div className="candidate-section-heading">
        <div>
          <h2>{section === "uploaded" ? "Uploaded CVs" : "Rejected CVs"}</h2>
          <p className="muted">{section === "uploaded" ? "Manually uploaded CVs across your approved jobs." : "CVs moved here by a recruiter, with the decision reason and history."}</p>
        </div>
        <label className="candidate-search"><span className="sr-only">Search candidates</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by job or candidate" /></label>
      </div>

      {error && <p className="error" role="alert">{error}</p>}

      {loading ? <section className="card empty-state">Loading candidates…</section> : (
        <div className="submissions-layout candidates-layout">
          <section className="card cv-list" aria-label={section === "uploaded" ? "Uploaded CVs" : "Rejected CVs"}>
            <div className="cv-list-toolbar">
              {section === "uploaded" ? (
                <>
                  <label className="select-all"><input type="checkbox" checked={allSelected} onChange={selectAll} disabled={!visibleItems.length} /> Select all</label>
                  <div className="candidate-actions">
                    <button type="button" className="secondary" onClick={() => setAction("reject")} disabled={!selected.size || saving}>Reject{selected.size ? ` (${selected.size})` : ""}</button>
                    <button type="button" className="danger-button" onClick={() => setAction("delete")} disabled={!selected.size || saving}>Delete{selected.size ? ` (${selected.size})` : ""}</button>
                  </div>
                </>
              ) : <span className="muted">{visibleItems.length} archived CV{visibleItems.length === 1 ? "" : "s"}</span>}
            </div>
            {visibleItems.length === 0 ? (
              <div className="empty-state compact"><h2>{query ? "No matching CVs" : section === "uploaded" ? "No uploaded CVs yet" : "No rejected CVs"}</h2><p className="muted">{query ? "Try another job code, title, or candidate label." : section === "uploaded" ? "CVs uploaded from the Jobs page will appear here." : "Rejected or deleted CVs and their decision history will appear here."}</p></div>
            ) : (
              <ul className="cv-submission-list">
                {visibleItems.map((item) => (
                  <li className={`cv-submission${item.id === previewId ? " active" : ""}${section === "rejected" ? " no-checkbox" : ""}`} key={item.id}>
                    {section === "uploaded" && <input aria-label={`Select ${item.candidate_name}`} type="checkbox" checked={selected.has(item.id)} onChange={() => toggle(item.id)} />}
                    <button type="button" className="cv-open" onClick={() => setPreviewId(item.id)}>
                      <strong>{item.job_code} · {item.job_title} · {item.candidate_name}</strong>
                      <span>{item.original_filename || `${item.candidate_name}.pdf`}</span>
                      <small>{formatBytes(item.size_bytes)} · {new Date(item.uploaded_at).toLocaleDateString()} · {item.source === "manual" ? "Manual upload" : stageLabel(item.source)}</small>
                      {section === "rejected" && <span className="rejection-summary">{item.rejection_action === "deleted" ? "Deleted" : "Rejected"} from {stageLabel(item.rejected_from_stage || "uploaded")}: {item.rejection_reason}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card cv-preview" aria-label="CV preview and decision history">
            {activePreview ? (
              <>
                <div className="preview-heading"><div><strong>{activePreview.candidate_name}</strong><span className="muted">{activePreview.job_code} · {activePreview.job_title}</span></div><span className="file-badge">PDF</span></div>
                {previewUrl ? <iframe title={`CV preview: ${activePreview.candidate_name}`} src={previewUrl} /> : <div className="preview-placeholder">Loading CV preview…</div>}
                {section === "rejected" && <div className="candidate-history"><h3>Decision history</h3>{activePreview.events.map((event, index) => <article className="history-event" key={`${event.created_at}-${index}`}><div><strong>{event.action === "deleted" ? "Deleted" : "Rejected"}</strong><span>{new Date(event.created_at).toLocaleString()}</span></div><p>Moved from {stageLabel(event.from_stage)} to {stageLabel(event.to_stage)}</p><p>{event.reason}</p></article>)}</div>}
              </>
            ) : <div className="preview-placeholder"><span aria-hidden="true">▧</span><p>Select a CV to preview it here.</p></div>}
          </section>
        </div>
      )}

      {action && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setAction(null); }}>
          <form className="card decision-modal" onSubmit={(event) => void submitAction(event)} aria-labelledby="decision-title">
            <p className="eyebrow">CANDIDATE DECISION</p>
            <h2 id="decision-title">{action === "delete" ? "Move CVs to Rejected" : "Reject selected CVs"}</h2>
            <p className="muted">{selected.size} CV{selected.size === 1 ? "" : "s"} will remain stored and appear in Rejected with this reason and a history entry.</p>
            <label htmlFor="decision-reason">Reason</label>
            <textarea id="decision-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} rows={4} placeholder="Add a reason for this decision…" required />
            <div className="actions"><button type="button" className="secondary" onClick={() => setAction(null)} disabled={saving}>Cancel</button><button type="submit" className={action === "delete" ? "danger-button" : ""} disabled={!reason.trim() || saving}>{saving ? "Saving…" : action === "delete" ? "Move to Rejected" : "Reject CVs"}</button></div>
          </form>
        </div>
      )}
    </div>
  );
}
