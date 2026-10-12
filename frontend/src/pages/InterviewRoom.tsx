import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import InterviewCall from "../components/InterviewCall";
import { admitInterview, askInterviewAssistant, endInterview, getInterview, Interview,
  JoinCredentials, listTranscript, recruiterJoin, regenerateQuestions, startInterview,
  TranscriptTurn, updateQuestions } from "../api/interviews";

const profileOrder = ["summary", "skills", "experience", "projects", "education", "certifications"];
const profileLabel = (key: string) => key.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const profileText = (value: unknown): string => Array.isArray(value)
  ? value.map(profileText).filter(Boolean).join(", ")
  : value && typeof value === "object"
    ? Object.entries(value).map(([key, entry]) => `${profileLabel(key)}: ${profileText(entry)}`).join(" · ")
    : String(value ?? "").trim();

function ProfileSection({ name, value }: { name: string; value: unknown }) {
  if (value == null || value === "" || (Array.isArray(value) && value.length === 0)) return null;
  const entries = Array.isArray(value) ? value : name === "skills" && typeof value === "string"
    ? value.split(/[,;•]|\s·\s/).map((item) => item.trim()).filter(Boolean) : null;
  return <section className="interview-profile-field">
    <h3>{profileLabel(name)}</h3>
    {entries ? <ul>{entries.map((entry, index) => {
      if (entry && typeof entry === "object" && !Array.isArray(entry)) {
        const fields = Object.entries(entry as Record<string, unknown>);
        const heading = fields.find(([key]) => ["name", "title", "role", "degree"].includes(key));
        return <li key={index}>{heading && <strong>{profileText(heading[1])}</strong>}
          {fields.filter(([key]) => key !== heading?.[0]).map(([key, detail]) =>
            <div key={key} className="interview-profile-detail"><b>{profileLabel(key)}:</b> {profileText(detail)}</div>)}</li>;
      }
      return <li key={index}>{profileText(entry)}</li>;
    })}</ul> : <p>{profileText(value)}</p>}
  </section>;
}

export default function InterviewRoom() {
  const { sessionId = "" } = useParams();
  const [session, setSession] = useState<Interview | null>(null);
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [credentials, setCredentials] = useState<JoinCredentials | null>(null);
  const [consent, setConsent] = useState(false);
  const [questionDraft, setQuestionDraft] = useState<string[]>([]);
  const [editingQuestions, setEditingQuestions] = useState(false);
  const [assistantPrompt, setAssistantPrompt] = useState("");
  const [assistantAnswer, setAssistantAnswer] = useState("");
  const [tab, setTab] = useState<"transcript" | "assistant">("transcript");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void Promise.all([getInterview(sessionId), listTranscript(sessionId)]).then(([details, transcript]) => {
        if (!active) return;
        setSession(details); setTurns(transcript);
      }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load interview."); });
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [sessionId]);

  const act = async (operation: () => Promise<Interview>) => {
    setBusy(true); setError(null);
    try { setSession(await operation()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The interview action failed."); }
    finally { setBusy(false); }
  };
  const start = async () => {
    if (!consent) return;
    setBusy(true); setError(null);
    try {
      setSession(await startInterview(sessionId));
      setCredentials(await recruiterJoin(sessionId));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not start the interview."); }
    finally { setBusy(false); }
  };
  const join = async () => {
    setBusy(true); setError(null);
    try { setCredentials(await recruiterJoin(sessionId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not join the call."); }
    finally { setBusy(false); }
  };
  const end = async () => { setSession(await endInterview(sessionId)); setCredentials(null); };
  const saveQuestions = async () => {
    await act(() => updateQuestions(sessionId, questionDraft));
    setEditingQuestions(false);
  };
  const ask = async (event: FormEvent) => {
    event.preventDefault(); if (!assistantPrompt.trim()) return;
    setBusy(true); setError(null);
    try { setAssistantAnswer((await askInterviewAssistant(sessionId, assistantPrompt.trim())).answer); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Interview assistant is unavailable."); }
    finally { setBusy(false); }
  };

  return <div className="interview-room-page">
    <Link to="/interviews">← All interviews</Link>
    <header className="page-heading"><div><p className="eyebrow">{session?.status.toUpperCase() ?? "INTERVIEW"}</p>
      <h1>{session?.job_title ?? "Interview"}</h1><p className="muted">{session?.candidate_name} · {session && new Date(session.scheduled_at).toLocaleString(undefined, { timeZone: session.timezone_name })} {session?.timezone_name}</p></div>
      {session?.started_at && <span className="interview-live-badge">{session.status === "live" ? "● Live" : "Ended"}</span>}</header>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="interview-room-grid">
      <div className="interview-main">
        <section className="card interview-stage">
          {credentials && session?.status === "live" ? <InterviewCall credentials={credentials} identity={sessionId} role="interviewer" onEnd={end} onLeave={() => setCredentials(null)} />
          : session?.status === "ended" ? <div className="empty-state">Interview ended. The transcript remains available below.</div>
          : <div className="interview-waiting">
              <h2>{session?.candidate_waiting_at ? "Candidate is in the waiting room" : "Waiting for candidate"}</h2>
              <p className="muted">The candidate joins using the private emailed invitation and acknowledges transcription.</p>
              {session?.candidate_waiting_at && !session.candidate_admitted_at &&
                <button disabled={busy} onClick={() => void act(() => admitInterview(sessionId))}>Admit candidate</button>}
              {session?.candidate_admitted_at && session.status !== "live" && <>
                <label className="interview-consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                  I understand that both speakers will be transcribed. Only transcript text will be saved.</label>
                <button disabled={!consent || busy} onClick={() => void start()}>Start interview</button>
              </>}
              {session?.status === "live" && !credentials && <button disabled={busy} onClick={() => void join()}>Join or rejoin video call</button>}
            </div>}
        </section>
        <section className="card interview-transcript-card">
          <div className="interview-tabs"><button className={tab === "transcript" ? "active" : ""} onClick={() => setTab("transcript")}>Live transcript</button>
            <button className={tab === "assistant" ? "active" : ""} onClick={() => setTab("assistant")}>Recruiter assistant</button></div>
          {tab === "transcript" ? <div className="interview-turns">
            {turns.length === 0 && <p className="muted">Transcript turns will appear as each short audio segment is processed.</p>}
            {turns.map((turn) => <article key={turn.id} className={`interview-turn ${turn.role}`}>
              <strong>{turn.role === "candidate" ? session?.candidate_name : "You"}</strong>
              <time>{new Date(turn.started_at).toLocaleTimeString()}</time><p>{turn.text}</p></article>)}
          </div> : <div className="interview-assistant">
            <p className="muted">Suggestions use this job, the parsed CV, and the transcript captured so far. Only you can see them.</p>
            <form onSubmit={(event) => void ask(event)}><input value={assistantPrompt} onChange={(event) => setAssistantPrompt(event.target.value)}
              placeholder="What should I ask next?" /><button disabled={busy || !assistantPrompt.trim()}>Ask</button></form>
            {assistantAnswer && <div className="card interview-assistant-answer">{assistantAnswer}</div>}
          </div>}
        </section>
      </div>
      <aside className="interview-side">
        <section className="card interview-candidate-info"><p className="eyebrow">CANDIDATE PROFILE</p>
          <h2>{session?.candidate_name}</h2><p className="muted">{session?.candidate_email}</p>
          {session?.f1 && <p><strong>F1 match:</strong> {session.f1.score}%</p>}
          {session?.f2 && <p><strong>F2 score:</strong> {session.f2.score}/100</p>}
          {session?.candidate_profile && Object.entries(session.candidate_profile)
            .filter(([key]) => !["candidate_name", "email"].includes(key))
            .sort(([left], [right]) => (profileOrder.indexOf(left) < 0 ? 99 : profileOrder.indexOf(left)) -
              (profileOrder.indexOf(right) < 0 ? 99 : profileOrder.indexOf(right)))
            .map(([key, value]) => <ProfileSection key={key} name={key} value={value} />)}
        </section>
        <section className="card interview-questions"><div className="interview-questions-heading"><div><p className="eyebrow">PREPARED QUESTIONS</p><h2>Interview guide</h2></div>
          {session?.status !== "ended" && <div className="interview-question-actions">
            <button className="secondary" disabled={busy} onClick={() => void act(() => regenerateQuestions(sessionId))}>Regenerate short questions</button>
            <button className="secondary" onClick={() => {
              setQuestionDraft(session?.questions.map((item) => item.text) ?? []); setEditingQuestions(!editingQuestions);
            }}>{editingQuestions ? "Cancel" : "Edit"}</button></div>}</div>
          {editingQuestions ? <><div>{questionDraft.map((value, index) => <div key={index} className="interview-question-edit">
            <textarea value={value} onChange={(event) => setQuestionDraft((current) => current.map((item, position) => position === index ? event.target.value : item))} />
            <button className="secondary" onClick={() => setQuestionDraft((current) => current.filter((_, position) => position !== index))}>Remove</button>
          </div>)}</div><button className="secondary" onClick={() => setQuestionDraft((current) => [...current, ""])}>Add question</button>
            <button disabled={busy || !questionDraft.some((item) => item.trim())} onClick={() => void saveQuestions()}>Save questions</button></>
          : <ol>{session?.questions.map((item, index) => <li key={index}>{item.text}</li>)}</ol>}
        </section>
      </aside>
    </div>
  </div>;
}
