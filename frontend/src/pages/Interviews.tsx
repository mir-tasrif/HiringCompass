import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CandidateCv, listCandidateCvs } from "../api/candidates";
import { CalendarConnection, Interview, connectGoogleCalendar, disconnectGoogleCalendar,
  googleCalendarStatus, listInterviews, resendInvitation, rescheduleInterview, scheduleInterview } from "../api/interviews";

function localInput(iso: string) {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export default function Interviews() {
  const [searchParams] = useSearchParams();
  const [candidates, setCandidates] = useState<CandidateCv[]>([]);
  const [sessions, setSessions] = useState<Interview[]>([]);
  const [calendar, setCalendar] = useState<CalendarConnection>({ connected: false, account_email: null });
  const [applicationId, setApplicationId] = useState(searchParams.get("applicationId") ?? "");
  const [editing, setEditing] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [when, setWhen] = useState("");
  const [zone, setZone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Dhaka");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const selected = useMemo(() => candidates.find((candidate) => candidate.id === applicationId), [candidates, applicationId]);

  const refresh = async () => {
    const [nextCandidates, nextSessions] = await Promise.all([listCandidateCvs("interview"), listInterviews()]);
    setCandidates(nextCandidates); setSessions(nextSessions);
  };
  useEffect(() => {
    void Promise.all([refresh(), googleCalendarStatus()]).then(([, status]) => setCalendar(status))
      .catch(() => setError("Could not load interviews or Google Calendar status."));
    if (searchParams.get("calendar_connected") === "1") setNotice("Google Calendar connected. Chat scheduling can now check availability.");
  }, []);
  useEffect(() => {
    if (!editing && selected?.candidate_email) setEmail(selected.candidate_email);
  }, [selected?.id, selected?.candidate_email, editing]);

  const edit = (session: Interview) => {
    setEditing(session.id); setApplicationId(session.application_id); setEmail(session.candidate_email);
    setWhen(localInput(session.scheduled_at)); setZone(session.timezone_name); setError(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const resend = async (session: Interview) => {
    setBusy(true); setError(null); setNotice(null);
    try { await resendInvitation(session.id); setNotice("A new private invitation link was emailed. The previous link is inactive."); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not resend the invitation."); }
    finally { setBusy(false); }
  };
  const connectCalendar = async () => {
    setBusy(true); setError(null);
    try {
      const result = await connectGoogleCalendar();
      if (!result.authorization_url) throw new Error("Google did not provide an authorization link.");
      window.location.assign(result.authorization_url);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not connect Google Calendar."); setBusy(false); }
  };
  const disconnectCalendar = async () => {
    setBusy(true); setError(null);
    try { await disconnectGoogleCalendar(); setCalendar({ connected: false, account_email: null }); setNotice("Google Calendar disconnected."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not disconnect Google Calendar."); }
    finally { setBusy(false); }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!applicationId || !when) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const iso = new Date(when).toISOString();
      if (editing) await rescheduleInterview(editing, email, iso, zone);
      else await scheduleInterview(applicationId, email, iso, zone);
      setNotice(editing ? "Interview rescheduled. The old link was replaced and a new invitation was emailed." : "Interview scheduled and invitation emailed.");
      setEditing(null); setWhen(""); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not schedule the interview."); }
    finally { setBusy(false); }
  };

  return <div className="interviews-page">
    <header className="page-heading"><div><p className="eyebrow">INTERVIEW WORKSPACE</p><h1>Interviews</h1>
      <p className="muted">Schedule selected candidates, manage invitations, and conduct private video interviews.</p></div></header>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    <section className="card" style={{ marginBottom: "1rem", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem" }}>
      <div><p className="eyebrow">GOOGLE CALENDAR MCP</p><strong>{calendar.connected ? `Connected${calendar.account_email ? ` as ${calendar.account_email}` : ""}` : "Not connected"}</strong>
        <p className="muted">Calendar invitations contain the private interview link. SMTP is used if Calendar delivery is unavailable.</p></div>
      {calendar.connected ? <button type="button" className="secondary" disabled={busy} onClick={() => void disconnectCalendar()}>Disconnect</button>
        : <button type="button" disabled={busy} onClick={() => void connectCalendar()}>{busy ? "Opening Google…" : "Connect Google Calendar"}</button>}
    </section>
    <div className="interviews-layout">
      <section className="card interview-scheduler"><p className="eyebrow">{editing ? "RESCHEDULE" : "NEW INTERVIEW"}</p>
        <h2>{editing ? "Change the interview time" : "Invite a candidate"}</h2>
        <form onSubmit={(event) => void submit(event)}>
          <label>Selected candidate<select required value={applicationId} disabled={Boolean(editing)} onChange={(event) => setApplicationId(event.target.value)}>
            <option value="">Choose a candidate</option>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>
              {candidate.job_code} · {candidate.job_title} · {candidate.candidate_name}</option>)}
          </select></label>
          <label>Invitation email<input required type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="candidate@example.com" /></label>
          <label>Date and time<input required type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} /></label>
          <label>Timezone<input required value={zone} onChange={(event) => setZone(event.target.value)} placeholder="Asia/Dhaka" /></label>
          <p className="muted">The candidate can enter the waiting room 15 minutes early. You will admit them before the call starts.</p>
          <div className="candidate-actions"><button type="submit" disabled={busy}>{busy ? "Saving…" : editing ? "Reschedule and email" : "Schedule and email"}</button>
            {editing && <button type="button" className="secondary" onClick={() => setEditing(null)}>Cancel</button>}</div>
        </form>
      </section>
      <section className="interview-sessions"><h2>Scheduled and past interviews</h2>
        {sessions.length === 0 && <div className="card empty-state">No interviews scheduled yet.</div>}
        {sessions.map((session) => <article key={session.id} className="card interview-session-card">
          <div><p className="eyebrow">{session.job_code} · {session.status.toUpperCase()}</p>
            <h3>{session.candidate_name}</h3><p>{session.job_title}</p>
            <p className="muted">{new Date(session.scheduled_at).toLocaleString(undefined, { timeZone: session.timezone_name })} · {session.timezone_name}</p>
            <p className="muted">Invitation: {session.invitation_sent_at ? (session.invitation_delivery === "google_calendar" ? "Google Calendar MCP invitation sent" : session.invitation_delivery === "google_calendar_api" ? "Google Calendar invitation sent" : "SMTP email sent") : "delivery failed"}
              {session.calendar_sync_status === "synced" && " · Calendar synced"}{session.calendar_sync_status === "failed" && " · Calendar sync failed; SMTP fallback used"}
              {session.candidate_waiting_at && " · candidate waiting"}{session.candidate_admitted_at && " · admitted"}</p></div>
          <div className="candidate-actions"><Link className="button" to={`/interviews/${session.id}`}>{session.status === "ended" ? "View transcript" : "Open interview"}</Link>
            {session.status !== "live" && session.status !== "ended" && <><button type="button" className="secondary" onClick={() => edit(session)}>Reschedule</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => void resend(session)}>Resend link</button></>}</div>
        </article>)}
      </section>
    </div>
  </div>;
}
