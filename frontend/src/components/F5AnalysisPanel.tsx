// F5 recruiter workspace: batch selection, evidence, correction, and F6 handoff review.
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { correctF5Grouping, correctF5Turn, f5Roster, f5Runs, F5Group, F5Ready, F5Run, reviewF5, startF5 } from "../api/f5";

export default function F5AnalysisPanel({ reviewOnly = false }: { reviewOnly?: boolean }) {
  const [roster, setRoster] = useState<F5Ready[]>([]);
  const [runs, setRuns] = useState<F5Run[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [groupDraft, setGroupDraft] = useState<F5Group[] | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = async () => { const [nextRoster, nextRuns] = await Promise.all([f5Roster(), f5Runs()]); setRoster(nextRoster); setRuns(nextRuns); };
  useEffect(() => { void load().catch(() => setError("Could not load F5 analysis.")); }, []);
  useEffect(() => { const timer = window.setInterval(() => { void load().catch(() => undefined); }, 5000); return () => window.clearInterval(timer); }, []);
  const current = useMemo(() => new Map(roster.map((item) => [item.application_id, item.interview_id])), [roster]);
  const visibleRuns = runs.filter((run) => !reviewOnly || run.status === "completed" && run.review_status === "pending" && current.get(run.application_id) === run.interview_id);
  const active = visibleRuns.find((run) => run.id === activeId) ?? visibleRuns[0];
  useEffect(() => { setGroupDraft(null); setEditing(null); }, [active?.id]);
  const eligible = roster.filter((item) => ["ready", "outdated", "failed"].includes(item.analysis_status));
  const act = async (operation: () => Promise<unknown>, message: string) => {
    setBusy(true); setError(null); setNotice(null);
    try { await operation(); await load(); setNotice(message); }
    catch (err) { setError(err instanceof ApiError ? err.message : "F5 action failed."); }
    finally { setBusy(false); }
  };
  return <section className="card f5-workspace" aria-label={reviewOnly ? "F5 human review" : "F5 Q&A analysis"}>
    <div className="cv-batch-heading"><div><p className="eyebrow">F5 · INTERVIEW Q&A</p><h3>{reviewOnly ? "F5 results awaiting review" : "Analyse interview answers"}</h3></div></div>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    {!reviewOnly && <><p className="muted">Choose up to 20 candidates. Each result uses that candidate’s latest handed-off transcript and saved job requirements.</p>
      <div className="f5-roster">{eligible.map((item) => <label key={item.application_id}><input type="checkbox" checked={selected.has(item.application_id)}
        onChange={() => setSelected((current) => { const next = new Set(current); next.has(item.application_id) ? next.delete(item.application_id) : next.add(item.application_id); return next; })} />
        {item.job_code} · candidate{String(item.candidate_number).padStart(3, "0")} · {item.candidate_name} · {item.analysis_status}</label>)}</div>
      <button type="button" disabled={!selected.size || selected.size > 20 || busy} onClick={() => void act(async () => { await startF5([...selected]); setSelected(new Set()); }, "F5 analysis queued.")}>Start F5 analysis ({selected.size})</button>
    </>}
    <div className="f5-run-list">{visibleRuns.map((run) => <button type="button" className={active?.id === run.id ? "active" : "secondary"} key={run.id} onClick={() => setActiveId(run.id)}>
      {roster.find((item) => item.application_id === run.application_id)?.candidate_name ?? run.application_id.slice(0, 8)} · {run.status}{run.status === "completed" ? ` · ${run.result?.overall_score ?? "—"}/100${run.result?.provisional ? " provisional" : ""}` : ""}
      {current.get(run.application_id) !== run.interview_id ? " · outdated" : ""}
    </button>)}</div>
    {active && <div className="f5-result"><h4>Analysis · {active.status} · {active.review_status}</h4>
      {active.error && <p className="error">{active.error}</p>}
      {active.result && <><p><strong>Overall: {active.result.overall_score ?? "Unavailable"}/100</strong>{active.result.provisional ? " · Provisional: at least one technical score used AI knowledge without approved-document verification." : " · Supported by approved technical documents."}</p>
        <h4>Requirement coverage</h4><ul>{active.result.coverage.map((row, index) => <li key={index}>{row.requirement} · <strong>{row.status.replace(/_/g, " ")}</strong></li>)}</ul>
        <h4>Questions and answers</h4>{active.result.evaluations.map((item, index) => <article className="history-event" key={index}><strong>{index + 1}. {item.question}</strong><p>{item.answer || "No candidate answer recorded."}</p><small>{item.relation}{item.question_started_at ? ` · ${new Date(item.question_started_at).toLocaleTimeString()}` : ""}</small>
          {item.scores ? <><p>Relevance {item.scores.relevance} · Technical accuracy {item.scores.technical_accuracy}{item.provisional ? " (provisional)" : ""} · Completeness {item.scores.completeness} · Articulation {item.scores.articulation}</p><p>{item.justification}</p><p><strong>Transcript evidence:</strong> {item.answer_evidence}</p>{item.references?.map((ref) => <p key={ref.id}>Reference: {ref.title} · {ref.text.slice(0, 300)}</p>)}</> : <p>Not evaluated: small talk or unanswered.</p>}</article>)}
      </>}
      {!reviewOnly && <details><summary>Correct transcript text and rerun analysis</summary><p className="muted">Corrections are saved with the original text and reviewer identity. Saving queues a new analysis of this snapshot.</p>
        {active.transcript.map((turn) => <div className="history-event" key={turn.id}><strong>{turn.role} · {new Date(turn.started_at).toLocaleTimeString()}</strong>
          {editing === turn.id ? <><textarea value={editText} onChange={(event) => setEditText(event.target.value)} /><button type="button" disabled={!editText.trim() || busy} onClick={() => void act(() => correctF5Turn(active.id, turn.id, editText), "Correction saved; reanalysis queued.")}>Save correction</button></> : <><p>{turn.text}</p><button type="button" className="secondary" onClick={() => { setEditing(turn.id); setEditText(turn.text); }}>Edit</button></>}</div>)}
      </details>}
      {!reviewOnly && active.result && <details><summary>Correct Q&A grouping</summary><p className="muted">Select the interviewer question and candidate answer turns for each group. Saving records the change and reruns F5.</p>
        <button type="button" className="secondary" onClick={() => setGroupDraft(active.result!.evaluations.map((item) => ({
          question_turns: item.question_turn_ids.map((id) => active.transcript.findIndex((turn) => turn.id === id)).filter((i) => i >= 0),
          answer_turns: item.answer_turn_ids.map((id) => active.transcript.findIndex((turn) => turn.id === id)).filter((i) => i >= 0),
          relation: item.relation, evaluable: item.evaluable, requirement_indexes: active.result!.coverage.flatMap((row, index) => row.segment_indexes.includes(active.result!.evaluations.indexOf(item)) ? [index] : []),
        })))}>Edit groups</button>
        {groupDraft?.map((group, index) => <div className="history-event" key={index}><strong>Group {index + 1}</strong>
          <label>Interviewer question turns<select multiple value={group.question_turns.map(String)} onChange={(event) => { const values = [...event.currentTarget.selectedOptions].map((option) => Number(option.value)); setGroupDraft((current) => current!.map((entry, i) => i === index ? { ...entry, question_turns: values } : entry)); }}>
            {active.transcript.map((turn, i) => turn.role === "interviewer" ? <option key={turn.id} value={i}>{i + 1}. {turn.text.slice(0, 90)}</option> : null)}</select></label>
          <label>Candidate answer turns<select multiple value={group.answer_turns.map(String)} onChange={(event) => { const values = [...event.currentTarget.selectedOptions].map((option) => Number(option.value)); setGroupDraft((current) => current!.map((entry, i) => i === index ? { ...entry, answer_turns: values } : entry)); }}>
            {active.transcript.map((turn, i) => turn.role === "candidate" ? <option key={turn.id} value={i}>{i + 1}. {turn.text.slice(0, 90)}</option> : null)}</select></label>
          <button type="button" className="secondary" onClick={() => setGroupDraft((current) => current!.filter((_, i) => i !== index))}>Remove group</button>
        </div>)}
        {groupDraft && <><button type="button" className="secondary" onClick={() => setGroupDraft((current) => [...current!, { question_turns: [], answer_turns: [], relation: "question", evaluable: true, requirement_indexes: [] }])}>Add group</button>
          <button type="button" disabled={busy || groupDraft.length === 0 || groupDraft.some((group) => !group.question_turns.length)} onClick={() => void act(() => correctF5Grouping(active.id, groupDraft), "Q&A grouping saved; reanalysis queued.")}>Save grouping</button></>}
      </details>}
      {active.status === "completed" && active.review_status === "pending" && current.get(active.application_id) === active.interview_id && <div className="candidate-actions review-actions">
        <button type="button" disabled={busy} onClick={() => void act(() => reviewF5(active.id, "approve", reason), "F5 approved; candidate ready for F6.")}>Approve for F6</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => reviewF5(active.id, "reanalyse", reason), "F5 reanalysis queued.")}>Request reanalysis</button>
        <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Review reason" aria-label="F5 review reason" />
        <button type="button" className="danger-button" disabled={busy || !reason.trim()} onClick={() => void act(() => reviewF5(active.id, "reject_candidate", reason), "Candidate rejected with recorded reason.")}>Reject candidate</button>
      </div>}
    </div>}
    {reviewOnly && visibleRuns.length === 0 && <p className="muted">No F5 results await review.</p>}
  </section>;
}
