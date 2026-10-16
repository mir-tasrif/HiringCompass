// Candidate pipeline page: stage navigation, CV batches, recruiter review, and F5 handoff display.
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { actOnCandidateCvs, cancelCvBatchItem, CandidateAction, CandidateCv, CandidateSection, CvBatch,
  decideCandidateReview, getCvBatch, getF2Weights, listActiveCvBatches, listCandidateCvs, retryCvBatchItem, startCvBatch, startF2Batch } from "../api/candidates";
import { loadCvPreview } from "../api/jobs";
import CandidateStageNav, { activeCandidateStages, candidateStages } from "../components/CandidateStageNav";
import F5AnalysisPanel from "../components/F5AnalysisPanel";
import { f5Roster, F5Ready, startF5 } from "../api/f5";

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
function stageLabel(stage: string) { return stage.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function batchLabel(kind: string) { return kind === "ranking" ? "Feature 1 ranking" : kind === "scoring" ? "Feature 2 scoring" : "Integrity check & parsing"; }
const scoreDimensions = [
  { key: "technical_projects", label: "Technical skills and projects" },
  { key: "relevant_experience", label: "Relevant experience" },
  { key: "certifications_soft_skills", label: "Certifications and soft skills" },
  { key: "education_qualifications", label: "Education and qualifications" },
] as const;

export default function Candidates() {
  const [searchParams] = useSearchParams();
  const jobFilter = searchParams.get("jobId");
  const sectionParam = searchParams.get("section") as CandidateSection | null;
  const [section, setSection] = useState<CandidateSection>(sectionParam && activeCandidateStages.has(sectionParam) ? sectionParam : "uploaded");
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
  const [weightDialog, setWeightDialog] = useState<{ jobId: string; jobCode: string; policyVersion: number | null; applicationIds: string[]; rescore: boolean } | null>(null);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [f5Ready, setF5Ready] = useState<F5Ready[]>([]);
  const [f5Revision, setF5Revision] = useState(0);

  useEffect(() => {
    if (section !== "f5") return;
    let active = true;
    const loadRoster = () => void f5Roster().then((roster) => { if (active) setF5Ready(roster); }).catch(() => undefined);
    loadRoster();
    const timer = window.setInterval(loadRoster, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [section]);

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
        const scoringTasks = new Map(valid.filter((batch) => batch.kind === "scoring")
          .flatMap((batch) => batch.items.map((item) => [item.application_id, item] as const)));
        const rankingTasks = new Map(valid.filter((batch) => batch.kind === "ranking")
          .flatMap((batch) => batch.items.map((item) => [item.application_id, item] as const)));
        if (scoringTasks.size || rankingTasks.size) setItems((current) => {
          let changed = false;
          const next = current.map((candidate) => {
          const task = scoringTasks.get(candidate.id);
          if (candidate.stage === "ranking") {
            const rankingTask = rankingTasks.get(candidate.id);
            if (!rankingTask || candidate.ranking_task_status === rankingTask.status &&
                candidate.ranking_task_error === rankingTask.error &&
                candidate.ranking_task_next_attempt_at === rankingTask.next_attempt_at) return candidate;
            changed = true;
            return { ...candidate, ranking_task_status: rankingTask.status, ranking_task_error: rankingTask.error,
              ranking_task_next_attempt_at: rankingTask.next_attempt_at };
          }
          if (!task || candidate.stage !== "scoring_processing" ||
              candidate.scoring_task_status === task.status && candidate.scoring_task_error === task.error &&
              candidate.scoring_task_next_attempt_at === task.next_attempt_at) return candidate;
          changed = true;
          return { ...candidate, scoring_task_status: task.status, scoring_task_error: task.error,
            scoring_task_next_attempt_at: task.next_attempt_at };
          });
          return changed ? next : current;
        });
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
  const f5Eligible = new Set(f5Ready.filter((item) => ["ready", "outdated", "failed"].includes(item.analysis_status)).map((item) => item.application_id));
  const selectableItems = section === "scoring" ? visibleItems.filter((item) => item.stage === "scoring") : section === "f5" ? visibleItems.filter((item) => f5Eligible.has(item.id)) : visibleItems;
  const selectAll = () => setSelected(selected.size === Math.min(selectableItems.length, selectionLimit) ? new Set() : new Set(selectableItems.slice(0, selectionLimit).map((item) => item.id)));

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

  const startF5Selection = async () => {
    if (!selected.size || saving) return;
    setSaving(true); setError(null); setNotice(null);
    try {
      await startF5([...selected]);
      setSelected(new Set());
      setF5Revision((current) => current + 1);
      setNotice(`F5 analysis queued for ${selected.size} candidate${selected.size === 1 ? "" : "s"}. Progress is saved in the background.`);
      setF5Ready(await f5Roster());
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not start F5 analysis."); }
    finally { setSaving(false); }
  };

  const prepareScoring = async (rescoreId?: string) => {
    const chosen = items.filter((item) => rescoreId ? item.id === rescoreId : selected.has(item.id));
    if (!chosen.length || saving) return;
    const expectedStage = rescoreId ? "f2_review" : "scoring";
    if (new Set(chosen.map((item) => item.job_id)).size !== 1 || chosen.some((item) => item.stage !== expectedStage)) {
      setError(`Select only ${expectedStage === "scoring" ? "F2-ready" : "F2-reviewed"} CVs from the same job.`); return;
    }
    setSaving(true); setError(null);
    try {
      const suggestion = await getF2Weights(chosen[0].job_id);
      setWeights(suggestion.weights);
      setWeightDialog({ jobId: suggestion.job_id, jobCode: suggestion.job_code, policyVersion: suggestion.policy_version,
                        applicationIds: chosen.map((item) => item.id), rescore: Boolean(rescoreId) });
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not load scoring weights."); }
    finally { setSaving(false); }
  };

  const confirmScoring = async (event: FormEvent) => {
    event.preventDefault();
    if (!weightDialog || saving) return;
    if (scoreDimensions.some(({ key }) => !Number.isInteger(weights[key]) || weights[key] < 0 || weights[key] > 100) ||
        scoreDimensions.reduce((total, { key }) => total + weights[key], 0) !== 100) {
      setError("Set all four weights to whole percentages that add up to 100%."); return;
    }
    setSaving(true); setError(null);
    try {
      const result = await startF2Batch(weightDialog.applicationIds, weights, weightDialog.rescore);
      setBatches((current) => ({ ...current, ...Object.fromEntries(result.batches.map((batch) => [batch.id, batch])) }));
      setNotice(`Feature 2 scoring started for ${weightDialog.applicationIds.length} CV(s). Each candidate has independent progress and retry.`);
      setWeightDialog(null); setSelected(new Set());
      await refresh();
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not start F2 scoring."); }
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
      setNotice(activePreview.stage === "integrity_review" ? "Integrity review approved. Profile parsing will continue in the background." :
        activePreview.stage === "f2_review" ? "Candidate selected for interview. The decision was saved." :
        "F1 screening approved. Candidate is ready for Feature 2 Scoring.");
      await refresh();
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not approve this candidate."); }
    finally { setSaving(false); }
  };

  const retry = async (itemId: string) => {
    setSaving(true); setError(null);
    try {
      const retried = await retryCvBatchItem(itemId);
      setNotice("This CV was queued for an individual retry.");
      const updated = await Promise.all([...new Set([...Object.keys(batches), retried.batch_id])].map((id) => getCvBatch(id).catch(() => null)));
      setBatches((current) => ({ ...current, ...Object.fromEntries(updated.filter((batch): batch is CvBatch => batch !== null).map((batch) => [batch.id, batch])) }));
      await refresh();
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not retry this CV."); }
    finally { setSaving(false); }
  };

  const cancelQueued = async (itemId: string) => {
    if (!window.confirm("Cancel this queued CV task and return the CV to its previous section?")) return;
    setSaving(true); setError(null);
    try {
      const cancelled = await cancelCvBatchItem(itemId);
      const updated = await getCvBatch(cancelled.batch_id);
      setBatches((current) => ({ ...current, [updated.id]: updated }));
      setNotice("Queued processing cancelled. The CV is back in its previous section.");
      await refresh();
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not cancel queued processing."); }
    finally { setSaving(false); }
  };

  const deleteQueued = async (applicationId: string) => {
    setSelected(new Set([applicationId]));
    setReason("Recruiter deleted a CV while its processing was queued.");
    setAction("delete");
  };

  const activePreview = items.find((item) => item.id === previewId);
  const allSelected = selectableItems.length > 0 && selectableItems.slice(0, selectionLimit).every((item) => selected.has(item.id));
  const title = candidateStages.find((item) => item.id === section)?.label ?? "Candidates";
  const recentBatches = Object.values(batches).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const displayedBatch = recentBatches.find((batch) => ["queued", "processing"].includes(batch.status))
    ?? recentBatches.find((batch) => batch.status === "waiting_review") ?? recentBatches[0];
  const otherActiveBatches = recentBatches.filter((batch) => batch.id !== displayedBatch?.id &&
    ["queued", "processing", "waiting_review"].includes(batch.status));
  const renderBatch = (batch: CvBatch) => <article className="cv-batch" key={batch.id}>
    <div className="cv-batch-title"><strong>{batchLabel(batch.kind)}</strong><span>{stageLabel(batch.status)} · {batch.completed}/{batch.total} complete · {batch.waiting_review} awaiting review · {batch.rejected} rejected · {batch.failed} failed · {batch.cancelled} cancelled</span></div>
    <progress max={batch.total || 1} value={batch.completed + batch.waiting_review + batch.rejected + batch.failed + batch.cancelled} aria-label={`${batchLabel(batch.kind)} progress`} />
    {batch.items.map((item) => <div className={item.status === "failed" ? "cv-batch-failure" : "cv-batch-item"} key={item.id}>
      <span>{item.candidate_label ?? items.find((candidate) => candidate.id === item.application_id)?.candidate_name ?? item.application_id.slice(0, 8)} · {stageLabel(item.status)}{item.next_attempt_at ? ` · retry after ${new Date(item.next_attempt_at).toLocaleTimeString()}` : ""}{item.error ? ` · ${item.error}` : ""}</span>
      {(item.status === "failed" || item.status === "queued" && item.next_attempt_at) && <button type="button" className="secondary" onClick={() => void retry(item.id)} disabled={saving}>Retry now</button>}
      {item.status === "queued" && <button type="button" className="secondary" onClick={() => void cancelQueued(item.id)} disabled={saving}>Cancel task</button>}
      {item.status === "queued" && <button type="button" className="danger-button" onClick={() => void deleteQueued(item.application_id)} disabled={saving}>Delete CV</button>}
    </div>)}
  </article>;

  return <div className="candidates-page">
    <header className="page-heading"><div><p className="eyebrow">TALENT PIPELINE</p><h1>Candidates</h1><p className="muted">Review CV evidence and move candidates through the hiring stages.</p></div></header>
    <CandidateStageNav section={section} onSelect={(next) => {
      setSection(next); setSelected(new Set()); setNotice(null); setError(null);
    }} />
    <div className="candidate-section-heading"><div><h2>{title}</h2><p className="muted">{section === "uploaded" ? "Select up to 20 CVs to run text extraction, F11 integrity checks, and profile parsing." : section === "integrity" ? "Processing status and saved integrity outcomes. Flagged CVs also appear in Review for a recruiter decision." : section === "parsed" ? "Parsed candidate profiles ready for Feature 1 screening." : section === "ranking" ? "Evidence-based requirement matches and per-job batch rankings." : section === "scoring" ? "Select up to 20 F1-approved CVs from one job, confirm scoring weights, and review saved F2 evidence." : section === "review" ? "Integrity, F1, F2, and F5 results awaiting a recruiter decision." : section === "interview" ? "Candidates approved after F2 for the interview stage." : section === "f5" ? "Select handed-off transcripts for F5 Q&A analysis, then inspect scores and requirement coverage." : section === "rejected" ? "Rejected or deleted CVs and their recorded reasons." : "Candidate records for this pipeline stage."}</p></div>
      <label className="candidate-search"><span className="sr-only">Search candidates</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by job or candidate" /></label>
    </div>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    {section === "review" && <F5AnalysisPanel reviewOnly />}

    {displayedBatch && <section className="cv-batch-panel card" aria-label="Background CV processing">
      <div className="cv-batch-heading"><div><p className="eyebrow">BACKGROUND PROCESSING</p><h3>Recent CV batches</h3></div><span className="muted">Progress is saved if you leave this page.</span></div>
      {renderBatch(displayedBatch)}
      {otherActiveBatches.length > 0 && <details><summary>{otherActiveBatches.length} other active CV batch{otherActiveBatches.length === 1 ? "" : "es"}</summary>{otherActiveBatches.map(renderBatch)}</details>}
    </section>}

    {loading ? <section className="card empty-state">Loading candidates…</section> : <div className="submissions-layout candidates-layout">
      <section className="card cv-list" aria-label={`${title} candidate list`}>
        <div className="cv-list-toolbar">
          {section === "uploaded" || section === "parsed" || section === "scoring" || section === "rejected" || section === "f5" ? <>
            <label className="select-all"><input type="checkbox" checked={allSelected} onChange={selectAll} disabled={!selectableItems.length} /> Select up to {selectionLimit}</label>
            <div className="candidate-actions">
              {section === "uploaded" ? <button type="button" onClick={() => void startBatch("integrity")} disabled={!selected.size || saving}>Proceed to CV integrity check and parsing{selected.size ? ` (${selected.size})` : ""}</button>
                : section === "parsed" ? <button type="button" onClick={() => void startBatch("ranking")} disabled={!selected.size || saving}>Start Feature 1 ranking{selected.size ? ` (${selected.size})` : ""}</button>
                  : section === "scoring" ? <button type="button" onClick={() => void prepareScoring()} disabled={!selected.size || saving}>Start Feature 2 scoring{selected.size ? ` (${selected.size})` : ""}</button>
                  : section === "f5" ? <button type="button" onClick={() => void startF5Selection()} disabled={!selected.size || saving}>{saving ? "Starting…" : `Start F5 analysis${selected.size ? ` (${selected.size})` : ""}`}</button>
                  : <button type="button" className="danger-button" onClick={() => { setAction("permanent_delete"); setReason("Recruiter permanently deleted this rejected CV."); }} disabled={!selected.size || saving}>Delete permanently{selected.size ? ` (${selected.size})` : ""}</button>}
              {section === "uploaded" && <><button type="button" className="secondary" onClick={() => setAction("reject")} disabled={!selected.size || saving}>Reject{selected.size ? ` (${selected.size})` : ""}</button><button type="button" className="danger-button" onClick={() => setAction("delete")} disabled={!selected.size || saving}>Delete{selected.size ? ` (${selected.size})` : ""}</button></>}
            </div>
          </> : <span className="muted">{visibleItems.length} CV{visibleItems.length === 1 ? "" : "s"}</span>}
        </div>
        {visibleItems.length === 0 ? <div className="empty-state compact"><h2>{section === "uploaded" ? "No uploaded CVs yet" : `No ${title.toLowerCase()} yet`}</h2><p className="muted">{section === "uploaded" ? "CVs uploaded from the Jobs page will appear here." : "Candidates will appear here as they reach this step."}</p></div> : <ul className="cv-submission-list">
          {visibleItems.map((item) => <li className={`cv-submission${item.id === previewId ? " active" : ""}${section !== "uploaded" && section !== "parsed" && section !== "scoring" && section !== "rejected" && section !== "f5" ? " no-checkbox" : ""}`} key={item.id}>
            {(section === "uploaded" || section === "parsed" || section === "rejected" || section === "scoring" && item.stage === "scoring" || section === "f5") && <input aria-label={`Select ${item.candidate_name}${section === "f5" ? " for F5 analysis" : ""}`} type="checkbox" checked={selected.has(item.id)} disabled={section === "f5" && !f5Eligible.has(item.id)} onChange={() => toggle(item.id)} />}
            <button type="button" className="cv-open" onClick={() => setPreviewId(item.id)}>
              <strong>{item.job_code} · {item.job_title} · {section === "f5" && /^candidate\d+$/i.test(item.candidate_name) && typeof item.parsed_profile?.candidate_name === "string" ? item.parsed_profile.candidate_name : item.candidate_name}</strong>
              <span>{item.original_filename || `${item.candidate_name}.pdf`}</span>
              <small>{formatBytes(item.size_bytes)} · {new Date(item.uploaded_at).toLocaleDateString()} · {stageLabel(item.stage)}</small>
              {section === "integrity" && <span className={item.integrity_verdict === "normal" ? "candidate-score" : item.integrity_task_status === "failed" || item.integrity_verdict === "review_required" ? "rejection-summary" : "muted"}>
                {item.stage === "integrity_check" && item.integrity_task_status === "failed" ? `Processing failed · ${item.integrity_task_error || "Retry or delete this CV."}` : item.stage === "integrity_check" ? `${item.integrity_task_status === "queued" ? "Queued" : "Processing"} extraction and integrity checks` : item.stage === "integrity_review" ? `Flagged for human review · ${item.integrity_signals.length} signal(s)` : item.integrity_verdict === "normal" ? "Passed integrity check · no suspicious signals" : item.integrity_verdict === "approved_after_review" ? "Approved after integrity review" : item.stage === "rejected" && item.rejection_action === "deleted" ? "Deleted · available for re-upload" : `Extraction failed: ${item.rejection_reason || "See rejection history"}`}
              </span>}
              {section === "review" && <span className={item.stage === "f2_review" || item.stage === "f1_review" && item.screening?.mandatory_pass ? "review-summary-pass" : "review-summary-fail"}>{item.stage === "integrity_review" ? `F11 review required · ${item.integrity_signals.length} signal(s)` : item.stage === "f2_review" ? `F2 Scoring Review · ${item.scoring?.aggregate_score ?? "—"}/100 · recruiter decision needed` : item.screening?.mandatory_pass ? `F1 review · Mandatory requirements met · Rank #${item.screening.rank ?? "pending"} · ${item.screening.suitability_score}% match` : `F1 review · Screened out by mandatory gate · No rank · ${item.screening?.suitability_score ?? 0}% match`}</span>}
              {section === "scoring" && <span className={item.scoring_task_status === "failed" && item.stage === "scoring_processing" ? "rejection-summary" : "candidate-score"}>{item.stage === "scoring" ? "Ready for F2 scoring" : item.stage === "scoring_processing" && item.scoring_task_status === "failed" ? `F2 scoring failed · ${item.scoring_task_error ?? "Retry this CV"}` : item.stage === "scoring_processing" ? item.scoring_task_status === "queued" ? `F2 scoring queued${item.scoring_task_next_attempt_at ? ` · retry after ${new Date(item.scoring_task_next_attempt_at).toLocaleTimeString()}` : " · waiting for worker"}` : "F2 scoring in progress" : `F2 score ${item.scoring?.aggregate_score ?? "—"}/100 · ${item.stage === "interview_selection" ? "selected for interview" : "awaiting review"}`}</span>}
              {section === "interview" && <span className="candidate-score">Selected for interview · F2 score {item.scoring?.aggregate_score ?? "—"}/100</span>}
              {section === "f5" && <span className="candidate-score">{f5Ready.find((entry) => entry.application_id === item.id)?.analysis_status ?? "Transcript ready"} · {item.f5_interview_ended_at ? new Date(item.f5_interview_ended_at).toLocaleString() : "Latest interview"} · F1 {item.screening?.suitability_score ?? "—"}% · F2 {item.scoring?.aggregate_score ?? "—"}/100</span>}
              {section === "ranking" && (item.stage === "ranking" ? <span className={item.ranking_task_status === "failed" ? "rejection-summary" : "candidate-score"}>
                {item.ranking_task_status === "failed" ? `F1 ranking failed · ${item.ranking_task_error ?? "Retry this CV"}` : item.ranking_task_status === "queued" ? `F1 ranking queued${item.ranking_task_next_attempt_at ? ` · retry after ${new Date(item.ranking_task_next_attempt_at).toLocaleTimeString()}` : " · waiting for worker"}` : "F1 ranking in progress"}
              </span> : item.screening && <span className="candidate-score">Rank #{item.screening.rank ?? "—"} · {item.screening.suitability_score}% · {item.screening.mandatory_pass ? "Mandatory gate passed" : "Screened out"}</span>)}
              {section === "rejected" && <span className="rejection-summary">{item.rejection_action === "deleted" ? "Deleted" : "Rejected"} from {stageLabel(item.rejected_from_stage || "uploaded")}: {item.rejection_reason}</span>}
            </button>
            {section === "scoring" && item.stage === "scoring_processing" && item.scoring_task_status === "failed" && item.scoring_task_id &&
              <button type="button" className="secondary cv-item-retry" onClick={() => void retry(item.scoring_task_id!)} disabled={saving}>Retry F2 scoring</button>}
            {section === "ranking" && item.stage === "ranking" && item.ranking_task_status === "failed" && item.ranking_task_id &&
              <button type="button" className="secondary cv-item-retry" onClick={() => void retry(item.ranking_task_id!)} disabled={saving}>Retry F1 ranking</button>}
          </li>)}
        </ul>}
      </section>
      <section className="card cv-preview" aria-label="Candidate details and CV preview">
        {activePreview ? <>
          <div className="preview-heading"><div><strong>{activePreview.job_code} · {activePreview.job_title} · {activePreview.candidate_name}</strong><span className="muted">{activePreview.original_filename}</span></div><span className="file-badge">PDF{activePreview.converted_from_docx ? " · converted" : ""}</span></div>
          {previewUrl ? <iframe title={`CV preview: ${activePreview.candidate_name}`} src={previewUrl} /> : <div className="preview-placeholder">Loading CV preview…</div>}
          {section !== "f5" && activePreview.parsed_profile && <div className="candidate-profile"><h3>Parsed candidate profile</h3><p><strong>Candidate name:</strong> {String(activePreview.parsed_profile.candidate_name ?? "Not identified")}</p>
            <p><strong>Email:</strong> {activePreview.candidate_email ?? "Not found in CV"}</p>
            {(["education", "experience", "skills", "certifications", "projects"] as const).map((key) => <div key={key}><h4>{stageLabel(key)}</h4><pre>{JSON.stringify(activePreview.parsed_profile?.[key] ?? [], null, 2)}</pre></div>)}
          </div>}
          {section !== "f5" && (section === "integrity" || activePreview.integrity_verdict || activePreview.integrity_signals.length > 0) && <div className="candidate-history"><h3>F11 integrity evidence · {activePreview.integrity_verdict ? stageLabel(activePreview.integrity_verdict) : activePreview.stage === "integrity_check" ? "Processing" : activePreview.stage === "rejected" && activePreview.rejection_action === "deleted" ? "Deleted" : "Extraction failed"}</h3>
            {activePreview.integrity_verdict === "normal" && <p>The CV successfully passed F11 integrity checks. No suspicious document or text signals were found. Extraction method: {activePreview.extraction_method || "not available"}.</p>}
            {activePreview.integrity_signals.length === 0 && activePreview.integrity_verdict !== "normal" && <p>{activePreview.stage === "integrity_check" ? "Text extraction and integrity checks are still running." : activePreview.rejection_reason || "No detailed integrity signals were recorded."}</p>}
            {activePreview.integrity_signals.map((signal, index) => <article className="history-event" key={`${signal.code}-${index}`}><strong>{stageLabel(signal.code)} · {signal.severity}</strong><p>{signal.evidence}</p></article>)}
            {activePreview.events.filter((event) => event.action.startsWith("integrity_")).map((event, index) => <article className="history-event" key={`${event.created_at}-${index}`}><div><strong>{stageLabel(event.action)}</strong><span>{new Date(event.created_at).toLocaleString()}</span></div><p>{event.reason}</p></article>)}
          </div>}
          {section === "integrity" && activePreview.stage === "integrity_check" && <div className="candidate-actions review-actions">
            {activePreview.integrity_task_status === "failed" && activePreview.integrity_task_id && <button type="button" onClick={() => void retry(activePreview.integrity_task_id!)} disabled={saving}>Retry this CV</button>}
            <button type="button" className="danger-button" disabled={saving} onClick={() => { setSelected(new Set([activePreview.id])); setAction("delete"); setReason("Deleted from Integrity Check so this CV can be uploaded again."); }}>Delete and allow re-upload</button>
          </div>}
          {section !== "f5" && activePreview.screening && <div className="candidate-history"><h3>Feature 1 screening · {activePreview.screening.suitability_score}% suitability</h3><p>{activePreview.screening.explanation}</p><p><strong>{activePreview.screening.mandatory_pass ? "Mandatory requirement gate passed (met or partially met)" : "Screened out by mandatory requirement gate"}</strong> · {activePreview.screening.mandatory_pass ? `Rank #${activePreview.screening.rank ?? "pending"}` : "No rank"}</p>
            {activePreview.screening.requirements.map((requirement, index) => <article className="history-event" key={`${requirement.text}-${index}`}><strong>{requirement.kind}: {requirement.text} · {stageLabel(requirement.status)}</strong><p>{requirement.explanation}</p><p><strong>CV evidence:</strong> {requirement.evidence || "No supporting evidence found."}</p></article>)}
          </div>}
          {section !== "f5" && activePreview.scoring && <div className="candidate-history"><h3>Feature 2 score · {activePreview.scoring.aggregate_score}/100</h3><p>Policy version {activePreview.scoring.policy_version} · {activePreview.scoring.model_name} · {new Date(activePreview.scoring.scored_at).toLocaleString()}</p>
            {activePreview.scoring.dimensions.map((dimension) => <article className="history-event" key={dimension.key}><strong>{scoreDimensions.find((entry) => entry.key === dimension.key)?.label ?? stageLabel(dimension.key)} · {dimension.missing ? "No evidence" : `${dimension.score}/100`} · weight {activePreview.scoring?.weights[dimension.key] ?? 0}%</strong><p>{dimension.justification}</p><p><strong>CV evidence:</strong> {dimension.evidence || "No supporting evidence found."}</p></article>)}
          </div>}
          {section === "scoring" && activePreview.stage === "f2_review" && <div className="candidate-actions review-actions"><button type="button" className="secondary" onClick={() => void prepareScoring(activePreview.id)} disabled={saving}>Rescore this CV</button></div>}
          {section === "interview" && <div className="candidate-actions review-actions"><Link to={`/interviews?applicationId=${activePreview.id}`}>Schedule interview for this candidate →</Link></div>}
          {section === "f5" && <div className="candidate-actions review-actions"><p className="muted">{activePreview.candidate_email || "No email saved"} · F1 match {activePreview.screening?.suitability_score ?? "—"}% · F2 score {activePreview.scoring?.aggregate_score ?? "—"}/100</p>{activePreview.f5_interview_id && <Link to={`/interviews/${activePreview.f5_interview_id}`}>View latest transcript →</Link>}</div>}
          {section === "review" && <div className="candidate-actions review-actions"><button type="button" onClick={() => void review("approve")} disabled={saving || (activePreview.stage === "f1_review" && !activePreview.screening?.mandatory_pass)}>{saving ? "Saving…" : activePreview.stage === "integrity_review" ? "Approve and continue parsing" : activePreview.stage === "f2_review" ? "Select for interview" : "Approve for Feature 2"}</button><button type="button" className="danger-button" onClick={() => void review("reject")} disabled={saving}>Reject CV</button>{activePreview.stage === "f1_review" && !activePreview.screening?.mandatory_pass && <p className="muted">The mandatory-requirement gate blocks progression. This CV can be rejected with a reason.</p>}</div>}
          {section === "rejected" && <div className="candidate-history"><h3>Decision history</h3>{activePreview.events.map((event, index) => <article className="history-event" key={`${event.created_at}-${index}`}><div><strong>{stageLabel(event.action)}</strong><span>{new Date(event.created_at).toLocaleString()}</span></div><p>Moved from {stageLabel(event.from_stage)} to {stageLabel(event.to_stage)}</p><p>{event.reason}</p></article>)}</div>}
        </> : <div className="preview-placeholder"><span aria-hidden="true">▧</span><p>Select a CV to inspect its details.</p></div>}
      </section>
    </div>}
    {section === "f5" && <F5AnalysisPanel applicationId={previewId} refreshKey={f5Revision} />}

    {weightDialog && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setWeightDialog(null); }}><form className="card decision-modal" onSubmit={(event) => void confirmScoring(event)} aria-labelledby="scoring-title">
      <p className="eyebrow">FEATURE 2 SCORING</p><h2 id="scoring-title">Confirm weights for {weightDialog.jobCode}</h2>
      <p className="muted">{weightDialog.policyVersion ? `Current job policy version ${weightDialog.policyVersion}.` : "Suggested from the approved job profile."} Changes become a new job policy for future batches. Existing scores keep their original weights.</p>
      {scoreDimensions.map(({ key, label }) => <label key={key} htmlFor={`weight-${key}`}>{label} (%)<input id={`weight-${key}`} type="number" min="0" max="100" step="1" value={weights[key] ?? 0} onChange={(event) => setWeights((current) => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}
      <p><strong>Total: {scoreDimensions.reduce((total, { key }) => total + (weights[key] || 0), 0)}%</strong></p>
      <div className="actions"><button type="button" className="secondary" onClick={() => setWeightDialog(null)} disabled={saving}>Cancel</button><button type="submit" disabled={saving || scoreDimensions.reduce((total, { key }) => total + (weights[key] || 0), 0) !== 100}>{saving ? "Starting…" : `Confirm and ${weightDialog.rescore ? "rescore" : "score"} ${weightDialog.applicationIds.length} CV${weightDialog.applicationIds.length === 1 ? "" : "s"}`}</button></div>
    </form></div>}

    {action && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setAction(null); }}><form className="card decision-modal" onSubmit={(event) => void submitAction(event)} aria-labelledby="decision-title">
      <p className="eyebrow">CANDIDATE DECISION</p><h2 id="decision-title">{action === "permanent_delete" ? "Permanently delete rejected CVs?" : action === "delete" ? "Move CVs to Rejected" : action === "review_reject" ? "Reject candidate CV" : "Reject selected CVs"}</h2>
      <p className="muted">{action === "permanent_delete" ? "This permanently removes the selected CV files, parsed profiles, screening results, and candidate records. The files can be uploaded again later." : "The CV and its evidence will be retained in Rejected with your reason and a history entry."}</p>
      <label htmlFor="decision-reason">Reason</label><textarea id="decision-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} rows={4} placeholder="Add a reason for this decision…" required />
      <div className="actions"><button type="button" className="secondary" onClick={() => setAction(null)} disabled={saving}>Cancel</button><button type="submit" className="danger-button" disabled={!reason.trim() || saving}>{saving ? "Saving…" : action === "permanent_delete" ? "Permanently delete" : "Move to Rejected"}</button></div>
    </form></div>}
  </div>;
}
