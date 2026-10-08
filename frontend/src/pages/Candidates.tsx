import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { actOnCandidateCvs, CandidateAction, CandidateCv, CandidateSection, CvBatch,
  decideCandidateReview, getCvBatch, listActiveCvBatches, listCandidateCvs, retryCvBatchItem, startCvBatch } from "../api/candidates";
import { loadCvPreview } from "../api/jobs";

const sections: { id: CandidateSection; label: string; active: boolean }[] = [
  { id: "uploaded", label: "Uploaded CV", active: true },
  { id: "integrity", label: "Integrity Check", active: true },
  { id: "parsed", label: "Parsed CV", active: true },
  { id: "ranking", label: "Feature 1 Ranking", active: true },
  { id: "scoring", label: "Feature 2 Scoring", active: false },
  { id: "interview", label: "Selected for Interview", active: false },
  { id: "assessment", label: "Feature 6 Assessment", active: false },
  { id: "final", label: "Final Selection", active: false },
  { id: "review", label: "Review", active: true },
  { id: "rejected", label: "Rejected", active: true },
];
const activeSections = new Set<CandidateSection>(sections.filter((item) => item.active).map((item) => item.id));

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
function stageLabel(stage: string) { return stage.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function batchLabel(kind: string) { return kind === "ranking" ? "Feature 1 ranking" : "Integrity check & parsing"; }

export default function Candidates() {
  const [searchParams] = useSearchParams();
  const jobFilter = searchParams.get("jobId");
  const sectionParam = searchParams.get("section") as CandidateSection | null;
  const [section, setSection] = useState<CandidateSection>(sectionParam && activeSections.has(sectionParam) ? sectionParam : "uploaded");
  const [items, setItems] = useState<CandidateCv[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [action, setAction] = useState<CandidateAction | "review_reject" | null>(null);
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [batches, setBatches] = useState<Record<string, CvBatch>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = async (target: CandidateSection = section) => {
    const nextItems = await listCandidateCvs(target);
    setItems(nextItems);
    setPreviewId((current) => nextItems.some((item) => item.id === current) ? current : nextItems[0]?.id ?? null);
    setSelected((current) => new Set([...current].filter((id) => nextItems.some((item) => item.id === id))));
  };

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([listCandidateCvs(section), listActiveCvBatches()]).then(([nextItems, currentBatches]) => {
      if (!active) return;
      setItems(nextItems);
      setPreviewId((current) => nextItems.some((item) => item.id === current) ? current : nextItems[0]?.id ?? null);
      setBatches(Object.fromEntries(currentBatches.map((batch) => [batch.id, batch])));
    }).catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Could not load candidates and processing status."))
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [section]);

  const watchedBatchIds = Object.values(batches).filter((batch) => ["queued", "processing", "waiting_review"].includes(batch.status)).map((batch) => batch.id).join(",");
  useEffect(() => {
    if (!watchedBatchIds) return;
    let active = true;
    const timer = window.setInterval(() => {
      void Promise.all(watchedBatchIds.split(",").map((id) => getCvBatch(id).catch(() => null))).then((updates) => {
        if (!active) return;
        const valid = updates.filter((batch): batch is CvBatch => batch !== null);
        setBatches((current) => ({ ...current, ...Object.fromEntries(valid.map((batch) => [batch.id, batch])) }));
        if (valid.some((batch) => batch.status === "completed" || batch.status === "completed_with_errors")) void refresh();
      });
    }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [watchedBatchIds, section]);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setPreviewUrl(null);
    const item = items.find((candidate) => candidate.id === previewId);
    if (item) loadCvPreview(item.job_id, item.id).then((blob) => {
      objectUrl = URL.createObjectURL(blob);
      if (active) setPreviewUrl(objectUrl); else URL.revokeObjectURL(objectUrl);
    }).catch((err: unknown) => { if (active) setError(err instanceof ApiError ? err.message : "Could not open this CV preview."); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [items, previewId]);

  const visibleItems = useMemo(() => {
    const search = query.trim().toLowerCase();
    return items.filter((item) => {
      const matchesJob = !jobFilter || item.job_id === jobFilter;
      const matchesSearch = !search || `${item.job_code} ${item.job_title} ${item.candidate_name} ${item.original_filename ?? ""}`.toLowerCase().includes(search);
      return matchesJob && matchesSearch;
    });
  }, [items, query, jobFilter]);

  const toggle = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else if (next.size < (section === "rejected" ? 100 : 20)) next.add(id);
    else setError(section === "rejected" ? "You can permanently delete up to 100 CVs at a time." : "A batch can include at most 20 CVs.");
    return next;
  });
  const selectionLimit = section === "rejected" ? 100 : 20;
  const selectAll = () => setSelected(selected.size === Math.min(visibleItems.length, selectionLimit) ? new Set() : new Set(visibleItems.slice(0, selectionLimit).map((item) => item.id)));

  const startBatch = async (kind: "integrity" | "ranking") => {
    if (!selected.size || saving) return;
    setSaving(true); setError(null); setNotice(null);
    try {
      const result = await startCvBatch([...selected], kind);
      setBatches((current) => ({ ...current, ...Object.fromEntries(result.batches.map((batch) => [batch.id, batch])) }));
      setSelected(new Set());
      setNotice(`${batchLabel(kind)} started for ${result.batches.reduce((total, batch) => total + batch.total, 0)} CV${selected.size === 1 ? "" : "s"}. Processing continues in the background.`);
      await refresh();
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not start CV processing."); }
    finally { setSaving(false); }
  };

  const submitAction = async (event: FormEvent) => {
    event.preventDefault();
    if (!action || !reason.trim() || saving) return;
    setSaving(true); setError(null);
    try {
      if (action === "review_reject") {
        if (!activePreview) return;
        await decideCandidateReview(activePreview.id, "reject", reason.trim());
      } else {
        await actOnCandidateCvs([...selected], action, reason.trim());
      }
      setAction(null); setReason(""); setSelected(new Set());
      setNotice("The candidate decision was saved with an audit record.");
      await refresh();
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not save the candidate decision."); }
    finally { setSaving(false); }
  };

  const review = async (decision: "approve" | "reject") => {
    if (!activePreview || saving) return;
    if (decision === "reject") { setAction("review_reject"); setReason(""); return; }
    setSaving(true); setError(null);
    try {
      await decideCandidateReview(activePreview.id, "approve", "");
      setNotice(activePreview.stage === "integrity_review" ? "Integrity review approved. Profile parsing will continue in the background." : "F1 screening approved. Candidate moved to Feature 2 Scoring.");
      await refresh();
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not approve this candidate."); }
    finally { setSaving(false); }
  };

  const retry = async (itemId: string) => {
    setSaving(true); setError(null);
    try {
      await retryCvBatchItem(itemId);
      setNotice("This CV was queued for an individual retry.");
      const updated = await Promise.all(Object.keys(batches).map((id) => getCvBatch(id).catch(() => null)));
      setBatches((current) => ({ ...current, ...Object.fromEntries(updated.filter((batch): batch is CvBatch => batch !== null).map((batch) => [batch.id, batch])) }));
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not retry this CV."); }
    finally { setSaving(false); }
  };

  const activePreview = items.find((item) => item.id === previewId);
  const allSelected = visibleItems.length > 0 && visibleItems.slice(0, selectionLimit).every((item) => selected.has(item.id));
  const title = sections.find((item) => item.id === section)?.label ?? "Candidates";

  return <div className="candidates-page">
    <header className="page-heading"><div><p className="eyebrow">TALENT PIPELINE</p><h1>Candidates</h1><p className="muted">Review CV evidence and move candidates through the hiring stages.</p></div></header>
    <nav className="candidate-sections" aria-label="Candidate pipeline sections">
      {sections.map((item) => <button key={item.id} type="button" className={`candidate-section${item.id === section ? " active" : ""}${item.active ? "" : " upcoming"}`}
        disabled={!item.active} onClick={() => { setSection(item.id); setSelected(new Set()); setNotice(null); setError(null); }} aria-current={item.id === section ? "page" : undefined}
        title={item.active ? undefined : "This pipeline step will be available in a future update."}>
        <span>{item.label}</span>{!item.active && <small>Coming soon</small>}
      </button>)}
    </nav>
    <div className="candidate-section-heading"><div><h2>{title}</h2><p className="muted">{section === "uploaded" ? "Select up to 20 CVs to run text extraction, F11 integrity checks, and profile parsing." : section === "integrity" ? "Processing status and saved integrity outcomes. Flagged CVs also appear in Review for a recruiter decision." : section === "parsed" ? "Parsed candidate profiles ready for Feature 1 screening." : section === "ranking" ? "Evidence-based requirement matches and per-job batch rankings." : section === "review" ? "Integrity flags and F1 screening results that need a recruiter decision." : section === "rejected" ? "Rejected or deleted CVs and their recorded reasons." : "Candidate records for this pipeline stage."}</p></div>
      <label className="candidate-search"><span className="sr-only">Search candidates</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by job or candidate" /></label>
    </div>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}

    {Object.values(batches).length > 0 && <section className="cv-batch-panel card" aria-label="Background CV processing">
      <div className="cv-batch-heading"><div><p className="eyebrow">BACKGROUND PROCESSING</p><h3>Recent CV batches</h3></div><span className="muted">Progress is saved if you leave this page.</span></div>
      {Object.values(batches).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 1).map((batch) => <article className="cv-batch" key={batch.id}>
        <div className="cv-batch-title"><strong>{batchLabel(batch.kind)}</strong><span>{stageLabel(batch.status)} · {batch.completed}/{batch.total} complete · {batch.waiting_review} awaiting review · {batch.rejected} rejected · {batch.failed} failed</span></div>
        <progress max={batch.total || 1} value={batch.completed + batch.waiting_review + batch.rejected + batch.failed} aria-label={`${batchLabel(batch.kind)} progress`} />
        {batch.items.filter((item) => item.status === "failed").map((item) => <div className="cv-batch-failure" key={item.id}><span>{item.application_id.slice(0, 8)}: {item.error || "Processing failed."}</span><button type="button" className="secondary" onClick={() => void retry(item.id)} disabled={saving}>Retry this CV</button></div>)}
      </article>)}
    </section>}

    {loading ? <section className="card empty-state">Loading candidates…</section> : <div className="submissions-layout candidates-layout">
      <section className="card cv-list" aria-label={`${title} candidate list`}>
        <div className="cv-list-toolbar">
          {section === "uploaded" || section === "parsed" || section === "rejected" ? <>
            <label className="select-all"><input type="checkbox" checked={allSelected} onChange={selectAll} disabled={!visibleItems.length} /> Select up to {selectionLimit}</label>
            <div className="candidate-actions">
              {section === "uploaded" ? <button type="button" onClick={() => void startBatch("integrity")} disabled={!selected.size || saving}>Proceed to CV integrity check and parsing{selected.size ? ` (${selected.size})` : ""}</button>
                : section === "parsed" ? <button type="button" onClick={() => void startBatch("ranking")} disabled={!selected.size || saving}>Start Feature 1 ranking{selected.size ? ` (${selected.size})` : ""}</button>
                  : <button type="button" className="danger-button" onClick={() => { setAction("permanent_delete"); setReason("Recruiter permanently deleted this rejected CV."); }} disabled={!selected.size || saving}>Delete permanently{selected.size ? ` (${selected.size})` : ""}</button>}
              {section === "uploaded" && <><button type="button" className="secondary" onClick={() => setAction("reject")} disabled={!selected.size || saving}>Reject{selected.size ? ` (${selected.size})` : ""}</button><button type="button" className="danger-button" onClick={() => setAction("delete")} disabled={!selected.size || saving}>Delete{selected.size ? ` (${selected.size})` : ""}</button></>}
            </div>
          </> : <span className="muted">{visibleItems.length} CV{visibleItems.length === 1 ? "" : "s"}</span>}
        </div>
        {visibleItems.length === 0 ? <div className="empty-state compact"><h2>{section === "uploaded" ? "No uploaded CVs yet" : `No ${title.toLowerCase()} yet`}</h2><p className="muted">{section === "uploaded" ? "CVs uploaded from the Jobs page will appear here." : "Candidates will appear here as they reach this step."}</p></div> : <ul className="cv-submission-list">
          {visibleItems.map((item) => <li className={`cv-submission${item.id === previewId ? " active" : ""}${section !== "uploaded" && section !== "parsed" && section !== "rejected" ? " no-checkbox" : ""}`} key={item.id}>
            {(section === "uploaded" || section === "parsed" || section === "rejected") && <input aria-label={`Select ${item.candidate_name}`} type="checkbox" checked={selected.has(item.id)} onChange={() => toggle(item.id)} />}
            <button type="button" className="cv-open" onClick={() => setPreviewId(item.id)}>
              <strong>{item.job_code} · {item.job_title} · {item.candidate_name}</strong>
              <span>{item.original_filename || `${item.candidate_name}.pdf`}</span>
              <small>{formatBytes(item.size_bytes)} · {new Date(item.uploaded_at).toLocaleDateString()} · {stageLabel(item.stage)}</small>
              {section === "integrity" && <span className={item.integrity_verdict === "normal" ? "candidate-score" : item.integrity_task_status === "failed" || item.integrity_verdict === "review_required" ? "rejection-summary" : "muted"}>
                {item.stage === "integrity_check" && item.integrity_task_status === "failed" ? `Processing failed · ${item.integrity_task_error || "Retry or delete this CV."}` : item.stage === "integrity_check" ? `${item.integrity_task_status === "queued" ? "Queued" : "Processing"} extraction and integrity checks` : item.stage === "integrity_review" ? `Flagged for human review · ${item.integrity_signals.length} signal(s)` : item.integrity_verdict === "normal" ? "Passed integrity check · no suspicious signals" : item.integrity_verdict === "approved_after_review" ? "Approved after integrity review" : item.stage === "rejected" && item.rejection_action === "deleted" ? "Deleted · available for re-upload" : `Extraction failed: ${item.rejection_reason || "See rejection history"}`}
              </span>}
              {section === "review" && <span className={item.stage === "f1_review" && item.screening?.mandatory_pass ? "review-summary-pass" : "review-summary-fail"}>{item.stage === "integrity_review" ? `F11 review required · ${item.integrity_signals.length} signal(s)` : item.screening?.mandatory_pass ? `F1 review · Mandatory requirements met · Rank #${item.screening.rank ?? "pending"} · ${item.screening.suitability_score}% match` : `F1 review · Screened out by mandatory gate · No rank · ${item.screening?.suitability_score ?? 0}% match`}</span>}
              {section === "ranking" && item.screening && <span className="candidate-score">Rank #{item.screening.rank ?? "—"} · {item.screening.suitability_score}% · {item.screening.mandatory_pass ? "Mandatory gate passed" : "Screened out"}</span>}
              {section === "rejected" && <span className="rejection-summary">{item.rejection_action === "deleted" ? "Deleted" : "Rejected"} from {stageLabel(item.rejected_from_stage || "uploaded")}: {item.rejection_reason}</span>}
            </button>
          </li>)}
        </ul>}
      </section>
      <section className="card cv-preview" aria-label="Candidate details and CV preview">
        {activePreview ? <>
          <div className="preview-heading"><div><strong>{activePreview.job_code} · {activePreview.job_title} · {activePreview.candidate_name}</strong><span className="muted">{activePreview.original_filename}</span></div><span className="file-badge">PDF{activePreview.converted_from_docx ? " · converted" : ""}</span></div>
          {previewUrl ? <iframe title={`CV preview: ${activePreview.candidate_name}`} src={previewUrl} /> : <div className="preview-placeholder">Loading CV preview…</div>}
          {activePreview.parsed_profile && <div className="candidate-profile"><h3>Parsed candidate profile</h3><p><strong>Candidate name:</strong> {String(activePreview.parsed_profile.candidate_name ?? "Not identified")}</p>
            {(["education", "experience", "skills", "certifications", "projects"] as const).map((key) => <div key={key}><h4>{stageLabel(key)}</h4><pre>{JSON.stringify(activePreview.parsed_profile?.[key] ?? [], null, 2)}</pre></div>)}
          </div>}
          {(section === "integrity" || activePreview.integrity_verdict || activePreview.integrity_signals.length > 0) && <div className="candidate-history"><h3>F11 integrity evidence · {activePreview.integrity_verdict ? stageLabel(activePreview.integrity_verdict) : activePreview.stage === "integrity_check" ? "Processing" : activePreview.stage === "rejected" && activePreview.rejection_action === "deleted" ? "Deleted" : "Extraction failed"}</h3>
            {activePreview.integrity_verdict === "normal" && <p>The CV successfully passed F11 integrity checks. No suspicious document or text signals were found. Extraction method: {activePreview.extraction_method || "not available"}.</p>}
            {activePreview.integrity_signals.length === 0 && activePreview.integrity_verdict !== "normal" && <p>{activePreview.stage === "integrity_check" ? "Text extraction and integrity checks are still running." : activePreview.rejection_reason || "No detailed integrity signals were recorded."}</p>}
            {activePreview.integrity_signals.map((signal, index) => <article className="history-event" key={`${signal.code}-${index}`}><strong>{stageLabel(signal.code)} · {signal.severity}</strong><p>{signal.evidence}</p></article>)}
            {activePreview.events.filter((event) => event.action.startsWith("integrity_")).map((event, index) => <article className="history-event" key={`${event.created_at}-${index}`}><div><strong>{stageLabel(event.action)}</strong><span>{new Date(event.created_at).toLocaleString()}</span></div><p>{event.reason}</p></article>)}
          </div>}
          {section === "integrity" && activePreview.stage === "integrity_check" && <div className="candidate-actions review-actions">
            {activePreview.integrity_task_status === "failed" && activePreview.integrity_task_id && <button type="button" onClick={() => void retry(activePreview.integrity_task_id!)} disabled={saving}>Retry this CV</button>}
            <button type="button" className="danger-button" disabled={saving} onClick={() => { setSelected(new Set([activePreview.id])); setAction("delete"); setReason("Deleted from Integrity Check so this CV can be uploaded again."); }}>Delete and allow re-upload</button>
          </div>}
          {activePreview.screening && <div className="candidate-history"><h3>Feature 1 screening · {activePreview.screening.suitability_score}% suitability</h3><p>{activePreview.screening.explanation}</p><p><strong>{activePreview.screening.mandatory_pass ? "Mandatory requirement gate passed (met or partially met)" : "Screened out by mandatory requirement gate"}</strong> · {activePreview.screening.mandatory_pass ? `Rank #${activePreview.screening.rank ?? "pending"}` : "No rank"}</p>
            {activePreview.screening.requirements.map((requirement, index) => <article className="history-event" key={`${requirement.text}-${index}`}><strong>{requirement.kind}: {requirement.text} · {stageLabel(requirement.status)}</strong><p>{requirement.explanation}</p><p><strong>CV evidence:</strong> {requirement.evidence || "No supporting evidence found."}</p></article>)}
          </div>}
          {section === "review" && <div className="candidate-actions review-actions"><button type="button" onClick={() => void review("approve")} disabled={saving || (activePreview.stage === "f1_review" && !activePreview.screening?.mandatory_pass)}>{saving ? "Saving…" : activePreview.stage === "integrity_review" ? "Approve and continue parsing" : "Approve for Feature 2"}</button><button type="button" className="danger-button" onClick={() => void review("reject")} disabled={saving}>Reject CV</button>{activePreview.stage === "f1_review" && !activePreview.screening?.mandatory_pass && <p className="muted">The mandatory-requirement gate blocks progression. This CV can be rejected with a reason.</p>}</div>}
          {section === "rejected" && <div className="candidate-history"><h3>Decision history</h3>{activePreview.events.map((event, index) => <article className="history-event" key={`${event.created_at}-${index}`}><div><strong>{stageLabel(event.action)}</strong><span>{new Date(event.created_at).toLocaleString()}</span></div><p>Moved from {stageLabel(event.from_stage)} to {stageLabel(event.to_stage)}</p><p>{event.reason}</p></article>)}</div>}
        </> : <div className="preview-placeholder"><span aria-hidden="true">▧</span><p>Select a CV to inspect its details.</p></div>}
      </section>
    </div>}

    {action && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setAction(null); }}><form className="card decision-modal" onSubmit={(event) => void submitAction(event)} aria-labelledby="decision-title">
      <p className="eyebrow">CANDIDATE DECISION</p><h2 id="decision-title">{action === "permanent_delete" ? "Permanently delete rejected CVs?" : action === "delete" ? "Move CVs to Rejected" : action === "review_reject" ? "Reject candidate CV" : "Reject selected CVs"}</h2>
      <p className="muted">{action === "permanent_delete" ? "This permanently removes the selected CV files, parsed profiles, screening results, and candidate records. The files can be uploaded again later." : "The CV and its evidence will be retained in Rejected with your reason and a history entry."}</p>
      <label htmlFor="decision-reason">Reason</label><textarea id="decision-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} rows={4} placeholder="Add a reason for this decision…" required />
      <div className="actions"><button type="button" className="secondary" onClick={() => setAction(null)} disabled={saving}>Cancel</button><button type="submit" className="danger-button" disabled={!reason.trim() || saving}>{saving ? "Saving…" : action === "permanent_delete" ? "Permanently delete" : "Move to Rejected"}</button></div>
    </form></div>}
  </div>;
}
