import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import InterviewCall, { InterviewCallHandle } from "../components/InterviewCall";
import FormattedMessage from "../components/FormattedMessage";
import { admitInterview, askInterviewAssistant, endInterview, getInterview, Interview,
  JoinCredentials, listTranscript, recruiterJoin, regenerateQuestions, startInterview,
  TranscriptTurn, updateQuestions } from "../api/interviews";

const profileOrder = ["summary", "skills", "experience", "projects", "education", "certifications"];
const dimensionLabels: Record<string, string> = {
  technical_projects: "Technical skills & projects",
  relevant_experience: "Relevant experience",
  certifications_soft_skills: "Certifications & soft skills",
  education_qualifications: "Education & qualifications",
};
type AssistantMessage = { id: string; role: "recruiter" | "assistant"; text: string; pending?: boolean };
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

function displayCandidateName(session: Interview | null): string {
  if (!session) return "Candidate";
  const parsedName = session.candidate_profile?.candidate_name;
  if (typeof parsedName === "string" && parsedName.trim()) return parsedName.trim();
  if (session.candidate_name && !/^candidate\d+$/i.test(session.candidate_name.trim())) return session.candidate_name;
  return `candidate${String(session.candidate_number).padStart(3, "0")}`;
}

export default function InterviewRoom() {
  const { sessionId = "" } = useParams();
  const [session, setSession] = useState<Interview | null>(null);
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [credentials, setCredentials] = useState<JoinCredentials | null>(null);
  const callRef = useRef<InterviewCallHandle>(null);
  const [callState, setCallState] = useState({ connection: "Connecting…", mic: true, camera: true, ending: false });
  const updateCallState = useCallback((state: typeof callState) => setCallState(state), []);
  const [consent, setConsent] = useState(false);
  const [questionDraft, setQuestionDraft] = useState<string[]>([]);
  const [editingQuestions, setEditingQuestions] = useState(false);
  const [assistantPrompt, setAssistantPrompt] = useState("");
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([]);
  const assistantMessagesRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<"transcript" | "assistant">("transcript");
  const [busy, setBusy] = useState(false);
  const [ending, setEnding] = useState(false);
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
  const end = async () => {
    setEnding(true); setError(null);
    try { setSession(await endInterview(sessionId)); setCredentials(null); }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not end the interview. Try again.");
      throw reason;
    } finally { setEnding(false); }
  };
  const endCall = () => callRef.current ? callRef.current.end() : end();
  const saveQuestions = async () => {
    await act(() => updateQuestions(sessionId, questionDraft));
    setEditingQuestions(false);
  };
  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const prompt = assistantPrompt.trim();
    if (!prompt || busy) return;
    const messageId = `${Date.now()}-${Math.random()}`;
    const responseId = `${messageId}-reply`;
    setAssistantMessages((current) => [...current,
      { id: messageId, role: "recruiter", text: prompt },
      { id: responseId, role: "assistant", text: "", pending: true },
    ]);
    setAssistantPrompt("");
    setBusy(true); setError(null);
    try {
      const response = await askInterviewAssistant(sessionId, prompt);
      setAssistantMessages((current) => current.map((message) => message.id === responseId
        ? { ...message, text: response.answer, pending: false } : message));
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Interview assistant is unavailable.";
      setAssistantMessages((current) => current.map((entry) => entry.id === responseId
        ? { ...entry, text: `I couldn't prepare a suggestion: ${message}`, pending: false } : entry));
    }
    finally { setBusy(false); }
  };

  useEffect(() => {
    const container = assistantMessagesRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [assistantMessages, tab]);

  const candidateName = displayCandidateName(session);

  const regenerateQuestionSet = async () => {
    setBusy(true); setError(null);
    try { setSession(await regenerateQuestions(sessionId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not regenerate interview questions."); }
    finally { setBusy(false); }
  };

  return <div className="interview-room-page">
    <div className="interview-room-grid">
      <aside className="card interview-control-rail" aria-label="Interview details and call controls">
        <Link to="/interviews">← Interviews</Link>
        <div className="interview-rail-details"><span className="eyebrow">INTERVIEW</span>
          <h1>{session?.job_title ?? "Interview"}</h1>
          <p>{candidateName}</p>
          {session && <time dateTime={session.scheduled_at}>{new Date(session.scheduled_at).toLocaleString(undefined, { timeZone: session.timezone_name })}</time>}
          {session?.started_at && <span className="interview-live-badge">{session.status === "live" ? "● Live" : "Ended"}</span>}
        </div>
        {session?.status === "live" && <div className="interview-rail-controls">
          <p className="muted" role="status">{credentials ? callState.connection : "Outside the call · interview live"}</p>
          {credentials ? <>
            <button type="button" className="secondary" onClick={() => void callRef.current?.toggleMic()}>{callState.mic ? "Mute" : "Unmute"}</button>
            <button type="button" className="secondary" onClick={() => void callRef.current?.toggleCamera()}>{callState.camera ? "Camera off" : "Camera on"}</button>
            <button type="button" className="secondary" onClick={() => void callRef.current?.leave()}>{callState.connection.startsWith("Disconnected") ? "Rejoin options" : "Leave call"}</button>
          </> : <button type="button" disabled={busy} onClick={() => void join()}>Rejoin call</button>}
          <button type="button" className="danger-button" disabled={ending || callState.ending}
            onClick={() => void endCall().catch(() => {})}>{ending || callState.ending ? "Ending…" : "End interview"}</button>
        </div>}
        {error && <p className="error" role="alert">{error}</p>}
      </aside>
      <div className="interview-main">
        <section className="card interview-stage">
          {credentials && session?.status === "live" ? <InterviewCall ref={callRef} credentials={credentials} identity={sessionId} role="interviewer" onEnd={end} onLeave={() => setCredentials(null)} onCallStateChange={updateCallState} />
          : session?.status === "ended" ? <div className="empty-state">Interview ended. The transcript remains available below.</div>
          : <div className="interview-waiting">
              <h2>{session?.status === "live" ? "Interview is live · rejoin your call"
                : session?.candidate_admitted_at ? "Candidate admitted · ready to start"
                : session?.candidate_waiting_at ? "Candidate is in the waiting room" : "Waiting for candidate"}</h2>
              <p className="muted">{session?.status === "live"
                ? "You have left the video call or refreshed this page. Rejoin to continue; the saved transcript remains below."
                : "Admit the candidate, acknowledge transcription, and start the interview."}</p>
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
            {turns.length === 0 && <p className="muted">Transcript turns will appear as speech is processed.</p>}
            {turns.map((turn) => <article key={turn.id} className={`interview-turn ${turn.role}`}>
              <strong>{turn.role === "candidate" ? candidateName : "You"}</strong>
              <time>{new Date(turn.started_at).toLocaleTimeString()}</time><p>{turn.text}</p></article>)}
          </div> : <div className="interview-assistant">
            <p className="muted">Ask for information from the CV, job, company knowledge, or transcript, or request a suggested next interview question. Only you can see this chat.</p>
            <div className="interview-assistant-messages" ref={assistantMessagesRef} role="log" aria-live="polite" aria-label="Recruiter assistant chat">
              {assistantMessages.length === 0 && <p className="muted interview-assistant-empty">Ask a question or request a suggested follow-up based on the candidate and interview.</p>}
              {assistantMessages.map((message) => <article key={message.id} className={`interview-assistant-message ${message.role}`}>
                <strong>{message.role === "recruiter" ? "You" : "Recruiter assistant"}</strong>
                {message.pending || message.role === "recruiter"
                  ? <p>{message.pending ? "Thinking…" : message.text}</p>
                  : <FormattedMessage text={message.text} />}
              </article>)}
            </div>
            <form onSubmit={(event) => void ask(event)}><input value={assistantPrompt} onChange={(event) => setAssistantPrompt(event.target.value)}
              placeholder="Ask the assistant or request a question…" /><button disabled={busy || !assistantPrompt.trim()}>{busy ? "Thinking…" : "Ask"}</button></form>
          </div>}
        </section>
      </div>
      <aside className="interview-side">
        <section className="card interview-candidate-info"><p className="eyebrow">CANDIDATE PROFILE</p>
          <h2>{candidateName}</h2><p className="muted">{session?.candidate_email}</p>
          {session?.f1 && <p><strong>F1 match:</strong> {session.f1.score}%</p>}
          {session?.f2 && <p><strong>F2 score:</strong> {session.f2.score}/100</p>}
          {session?.f2?.dimensions?.length ? <section className="interview-score-dimensions" aria-label="Feature 2 scoring dimensions">
            <h3>F2 score breakdown</h3>
            {session.f2.dimensions.map((dimension) => {
              const value = Math.max(0, Math.min(100, Number(dimension.score) || 0));
              const label = dimensionLabels[dimension.key] ?? profileLabel(dimension.key);
              return <div className="interview-score-dimension" key={dimension.key}>
                <div className="interview-score-dimension-label"><span>{label}</span>
                  <strong>{dimension.missing ? "No evidence" : `${value}/100`}</strong></div>
                <div className="interview-score-track" role="meter" aria-label={`${label}: ${dimension.missing ? "No evidence" : `${value} out of 100`}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={dimension.missing ? 0 : value}>
                  <span style={{ width: `${dimension.missing ? 0 : value}%` }} />
                </div>
                <p>{dimension.justification}</p>
              </div>;
            })}
          </section> : null}
          {session?.candidate_profile && Object.entries(session.candidate_profile)
            .filter(([key]) => !["candidate_name", "email"].includes(key))
            .sort(([left], [right]) => (profileOrder.indexOf(left) < 0 ? 99 : profileOrder.indexOf(left)) -
              (profileOrder.indexOf(right) < 0 ? 99 : profileOrder.indexOf(right)))
            .map(([key, value]) => <ProfileSection key={key} name={key} value={value} />)}
        </section>
        <section className="card interview-questions"><div className="interview-questions-heading"><div><p className="eyebrow">PREPARED QUESTIONS</p><h2>Interview guide</h2></div>
          {session?.status !== "ended" && <div className="interview-question-actions">
            <button className="secondary" disabled={busy} onClick={() => void regenerateQuestionSet()}>{busy ? "Generating…" : "Regenerate short questions"}</button>
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
