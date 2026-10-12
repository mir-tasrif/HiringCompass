import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import InterviewCall from "../components/InterviewCall";
import { enterWaitingRoom, guestJoin, guestStatus, GuestStatus, JoinCredentials } from "../api/interviews";

export default function InterviewGuest() {
  const { invitation = "" } = useParams();
  const [status, setStatus] = useState<GuestStatus | null>(null);
  const [joined, setJoined] = useState<JoinCredentials | null>(null);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = () => void guestStatus(invitation).then((next) => {
      if (active) setStatus(next);
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Invitation unavailable."); });
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [invitation]);

  const wait = async () => {
    if (!consent) return;
    setBusy(true); setError(null);
    try { setStatus(await enterWaitingRoom(invitation)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not enter the waiting room."); }
    finally { setBusy(false); }
  };
  const join = async () => {
    setBusy(true); setError(null);
    try { setJoined(await guestJoin(invitation)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not join the interview."); }
    finally { setBusy(false); }
  };

  return <main className="interview-guest-page">
    <header><strong>HiringCompass</strong><span>Private interview invitation</span></header>
    <section className="card interview-guest-card">
      <p className="eyebrow">VIDEO INTERVIEW</p>
      <h1>{status?.job_title ?? "Interview invitation"}</h1>
      {status && <><p>Candidate: <strong>{status.candidate_name}</strong></p>
        <p>Scheduled: {new Date(status.scheduled_at).toLocaleString(undefined, { timeZone: status.timezone_name })} · {status.timezone_name}</p></>}
      {error && <p className="error" role="alert">{error}</p>}
      {status?.status === "ended" ? <p>This interview has ended. Thank you for joining.</p>
        : joined ? <InterviewCall credentials={joined} identity={invitation} role="candidate" onLeave={() => setJoined(null)} />
        : status?.status === "live" && status.admitted ? <button disabled={busy} onClick={() => void join()}>Join the video call</button>
        : status?.admitted ? <p className="notice">You have been admitted. Waiting for the recruiter to start the call…</p>
        : status?.status === "waiting" ? <p className="notice">You are in the waiting room. The recruiter will admit you.</p>
        : status ? <><label className="interview-consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          I understand that speech during this interview will be transcribed. Only transcript text will be saved; raw audio and video will not be retained.</label>
          <button disabled={!consent || busy} onClick={() => void wait()}>Enter waiting room</button></> : <p>Opening invitation…</p>}
    </section>
  </main>;
}
