import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import FormattedJd from "../components/FormattedJd";
import FormattedMessage from "../components/FormattedMessage";
import { ChatMessage, createThread, deleteThread, getThread, listThreads, sendDecision, sendMessage, ThreadDetail, ThreadSummary } from "../api/chat";
import { postJob } from "../api/jobs";

// Plain assistant or user bubble.
function Bubble({ message, children }: { message: ChatMessage; children?: React.ReactNode }) {
  return (
    <div className={`bubble ${message.role}`}>
      <FormattedMessage text={message.content} />
      {children}
    </div>
  );
}

function JobUpdateCard({
  message,
  working,
  postingJobId,
  onPost,
}: {
  message: ChatMessage;
  working: boolean;
  postingJobId: string | null;
  onPost: (jobId: string) => void;
}) {
  const payload = message.payload;
  if (!payload || payload.type !== "job_update") return null;

  return (
    <div className="bubble assistant wide job-update-card">
      <FormattedMessage text={message.content} />
      {payload.jobs.length === 0 ? <p>No approved jobs to show.</p> : payload.jobs.map((job) => (
        <article className="job-update-item" key={job.job_id}>
          <h3>{job.title}</h3>
          <ul>
            <li><strong>Job ID:</strong> {job.job_id}</li>
            <li><strong>Job code:</strong> {job.job_code || "Not assigned"}</li>
            <li><strong>Status:</strong> {job.archived ? "Expired" : "Approved"} · Version {job.version}</li>
            <li><strong>Discord:</strong> {job.posting_status.replace(/_/g, " ")}</li>
            <li><strong>Uploaded CVs:</strong> {job.cv_submissions}</li>
            {job.cv_pipeline && <li><strong>CV pipeline:</strong> {job.cv_pipeline.uploaded} uploaded · {job.cv_pipeline.integrity_check} checking · {job.cv_pipeline.integrity_review} integrity review · {job.cv_pipeline.parsed} parsed · {job.cv_pipeline.ranking} ranking · {job.cv_pipeline.f1_review} F1 review · {job.cv_pipeline.scoring} scoring · {job.cv_pipeline.rejected} rejected</li>}
          </ul>
          <div className="posting-actions">
            {!job.archived && job.posting_status !== "posted" && <button type="button" disabled={!job.can_post || working || postingJobId !== null} onClick={() => onPost(job.job_id)}>
              {postingJobId === job.job_id ? "Posting…" : job.posting_status === "pending" ? "Discord post in progress" : job.posting_status === "failed" ? "Retry Discord post" : "Post to Discord"}
            </button>}
            {!job.posting_configured && job.posting_status !== "posted" && <span className="muted">Discord posting is not configured.</span>}
            {job.cv_submissions > 0 && <>
              {!job.archived && (job.cv_pipeline?.uploaded ?? 0) > 0 && <Link className="button-link" to={`/candidates?jobId=${encodeURIComponent(job.job_id)}&section=uploaded`}>Continue integrity check & parsing</Link>}
              {!job.archived && (job.cv_pipeline?.parsed ?? 0) > 0 && <Link className="button-link" to={`/candidates?jobId=${encodeURIComponent(job.job_id)}&section=parsed`}>Select CVs for Feature 1</Link>}
              {(job.archived || (job.cv_pipeline?.integrity_review ?? 0) > 0 || (job.cv_pipeline?.f1_review ?? 0) > 0) && <Link className="button-link" to={`/candidates?jobId=${encodeURIComponent(job.job_id)}&section=${job.archived ? "uploaded" : (job.cv_pipeline?.integrity_review ?? 0) > 0 || (job.cv_pipeline?.f1_review ?? 0) > 0 ? "review" : "uploaded"}`}>{job.archived ? "View candidate history" : "Review candidates"}</Link>}
            </>}
          </div>
        </article>
      ))}
    </div>
  );
}

// The draft job description with its Approve / Regenerate buttons (active only for the newest draft).
function DraftCard(props: {
  message: ChatMessage;
  active: boolean;
  onDecide: (option: "approve" | "regenerate") => void;
}) {
  const payload = props.message.payload;
  if (!payload || payload.type !== "draft") return null;

  const sources = Array.isArray(payload.sources)
    ? payload.sources.filter(
        (source): source is string => typeof source === "string",
      )
    : [];
  const profile = payload.profile;
  const required = profile?.requirements?.filter((item) => item.kind === "mandatory") ?? [];
  const preferred = profile?.requirements?.filter((item) => item.kind === "preferred") ?? [];

  const bullets = (heading: string, items: string[]) => items.length > 0 && (
    <section className="jd-section">
      <h4>{heading}</h4>
      <ul>{items.map((item, index) => <li key={`${heading}-${index}`}>{item}</li>)}</ul>
    </section>
  );

  return (
    <div className="bubble assistant wide">
      <FormattedMessage text={props.message.content} />
      <section
        className="jd-card"
        aria-label={`Draft job description: ${payload.title}`}
      >
        <h3>{payload.title}</h3>
        {profile ? (
          <div className="jd-body">
            {payload.company_name && <p className="jd-company">{payload.company_name}</p>}
            {profile.about_company && <section className="jd-section"><h4>About {payload.company_name || "the company"}</h4><p>{profile.about_company}</p></section>}
            {profile.summary && <p>{profile.summary}</p>}
            {profile.employment_type && <p><strong>Employment type:</strong> {profile.employment_type}</p>}
            {profile.location && <p><strong>Location:</strong> {profile.location}</p>}
            {bullets("Responsibilities", profile.responsibilities ?? [])}
            {bullets("Required", required.map((item) => item.text))}
            {bullets("Preferred", preferred.map((item) => item.text))}
            {bullets("Benefits", profile.benefits ?? [])}
            <p><strong>How to apply:</strong> {payload.google_form_link
              ? <a href={payload.google_form_link} target="_blank" rel="noopener noreferrer">Fill the Google form</a>
              : <span>Fill the Google form</span>}</p>
          </div>
        ) : <FormattedJd text={payload.jd_text} />}

        {sources.length > 0 && (
          <p className="muted sources">
            Based on: {sources.join(" · ")}
          </p>
        )}

        {props.active && (
          <div className="actions">
            <button
              type="button"
              onClick={() => props.onDecide("approve")}
            >
              Approve
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => props.onDecide("regenerate")}
            >
              Regenerate
            </button>
            <span className="muted">
              or type what you would like changed
            </span>
          </div>
        )}
      </section>
    </div>
  );
}

// AI Assistant page: conversation list, chat window, option chips, draft card and composer.
export default function Assistant() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [postingJobId, setPostingJobId] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const working = thread?.status === "working";

  const refreshList = useCallback(() => listThreads().then(setThreads).catch(() => undefined), []);

  // Show an error from any failed request in a friendly way.
  const fail = (err: unknown) => setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");

  // Start a new conversation and open it.
  const newChat = useCallback(async () => {
    try {
      setThread(await createThread());
      setError(null);
      void refreshList();
    } catch (err) {
      fail(err);
    }
  }, [refreshList]);

  // Open the most recent conversation on first load, or start one.
  useEffect(() => {
    (async () => {
      try {
        const existing = await listThreads();
        setThreads(existing);
        if (existing.length > 0) setThread(await getThread(existing[0].id));
        else await newChat();
      } catch (err) {
        fail(err);
      }
    })();
  }, [newChat]);

  // Poll while the assistant is working; stop when the reply has arrived.
  useEffect(() => {
    if (!thread) return;
    const timer = setInterval(async () => {
      try {
        const latest = await getThread(thread.id);
        setThread(latest);
        if ((thread.status === "working" && latest.status === "idle") || latest.messages.length > thread.messages.length) void refreshList();
      } catch (err) {
        fail(err);
      }
    }, thread.status === "working" ? 1500 : 4000);
    return () => clearInterval(timer);
  }, [thread, refreshList]);

    // Scroll to the newest message without returning a cleanup value.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread?.messages.length, working]);

  // Send text (typed or a clicked option).
  const send = async (text: string) => {
    if (!thread || working || !text.trim()) return;
    try {
      const message = await sendMessage(thread.id, text.trim());
      setThread({ ...thread, status: "working", messages: [...thread.messages, message] });
      setInput("");
      setError(null);
    } catch (err) {
      fail(err);
    }
  };

  const postActivityJob = async (jobId: string) => {
    if (!thread || working || postingJobId) return;
    setPostingJobId(jobId);
    setError(null);
    try {
      await postJob(jobId);
      await send("What is the update?");
    } catch (err) {
      fail(err);
    } finally {
      setPostingJobId(null);
    }
  };

  // Approve or regenerate the draft currently on screen.
  const decide = async (option: "approve" | "regenerate", approvalId: string, version: number) => {
    if (!thread || working) return;
    try {
      const message = await sendDecision(thread.id, option, approvalId, version);
      setThread({ ...thread, status: "working", messages: [...thread.messages, message] });
      setError(null);
    } catch (err) {
      fail(err);
    }
  };

  // Open a past conversation.
  const open = async (id: string) => {
    try {
      setThread(await getThread(id));
      setError(null);
    } catch (err) {
      fail(err);
    }
  };

  const remove = async (id: string, title: string) => {
    if (!window.confirm(`Delete the chat "${title}"? Its saved job description will remain in the database.`)) return;
    try {
      await deleteThread(id);
      const remaining = await listThreads();
      setThreads(remaining);
      if (thread?.id === id) setThread(remaining.length ? await getThread(remaining[0].id) : null);
      setError(null);
    } catch (err) {
      fail(err);
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void send(input);
  };

  // Enter sends, Shift+Enter adds a new line.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(input);
    }
  };

  const messages = thread?.messages ?? [];
  const last = messages[messages.length - 1];
  const lastDraftId = [...messages].reverse().find((m) => m.payload?.type === "draft")?.id;
  const discordPosted = messages.some((m) => m.payload?.type === "posting_result" &&
    (m.payload.status === "posted" || m.payload.status === "already_posted"));

  return (
    <>
      <h1>Job Intelligence</h1>
      <div className="assistant">
        <aside className="card threads" aria-label="Conversations">
          <button type="button" onClick={() => void newChat()}>
            New job chat
          </button>
          <ul>
            {threads.map((t) => (
              <li key={t.id} className="thread-row">
                <button type="button" className={`thread-item ${t.id === thread?.id ? "current" : ""}`} onClick={() => void open(t.id)}>
                  <span>{t.title}</span>
                  <small className="muted">{t.state === "approved" ? "Approved" : t.state === "awaiting_decision" ? "Draft ready" : "In progress"}</small>
                </button>
                <button type="button" className="delete-thread" aria-label={`Delete chat ${t.title}`} title="Delete chat" onClick={() => void remove(t.id, t.title)}>
                  <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h16M10 4h4M6 7l1 13h10l1-13M10 11v5m4-5v5" /></svg>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <section className="card chat" aria-label="Chat">
          <div className="messages" aria-live="polite">
            {messages
              .filter((m) => m.payload?.type !== "decision" || m.role === "user")
              .map((m) => {
                const p = m.payload;
                if (p?.type === "draft") {
                  return (
                    <DraftCard
                      key={m.id}
                      message={m}
                      active={m.id === lastDraftId && thread?.state === "awaiting_decision" && !working}
                      onDecide={(option) => void decide(option, p.approval_id, p.version)}
                    />
                  );
                }
                if (p?.type === "job_update") {
                  return <JobUpdateCard key={m.id} message={m} working={Boolean(working)} postingJobId={postingJobId} onPost={(jobId) => void postActivityJob(jobId)} />;
                }
                return (
                  <Bubble key={m.id} message={m}>
                    {p?.type === "question" && m.id === last?.id && !working && Array.isArray(p.options) && p.options.length > 0 && (
                      <div className="chips" role="group" aria-label="Suggested answers">
                        {p.options.map((option) => (
                          <button key={option} type="button" className="chip" onClick={() => void send(option)}>
                            {option}
                          </button>
                        ))}
                      </div>
                    )}
                    {p?.type === "approved" && <p className="muted">Job ID: {p.job_code || p.job_id} · Version {p.version}</p>}
                    {(p?.type === "posting_offer" || (p?.type === "approved" && p.posting_available) ||
                      (p?.type === "posting_result" && p.status === "failed")) && !discordPosted && (
                      <div className="posting-actions">
                        <button type="button" onClick={() => void send("Post to Discord")}>
                          {p?.type === "posting_result" ? "Retry Discord post" : "Post to Discord"}
                        </button>
                      </div>
                    )}
                    {p?.type === "candidate_work" && <div className="posting-actions">
                      {p.awaiting_confirmation === "ranking" ? <button type="button" disabled={Boolean(working)} onClick={() => void send("yes")}>Start Feature 1 Ranking</button> : null}
                      <Link className="button-link" to={p.route}>
                        {p.review_required || p.kind === "review" ? "Open Review" : p.awaiting_confirmation === "ranking" ? "Review Parsed CVs" : p.kind === "ranking" ? "Open Feature 1 Ranking" : p.kind === "integrity" ? "Open Integrity Check" : "Select CVs in Candidates"}
                      </Link>
                    </div>}
                  </Bubble>
                );
              })}
            {working && (
              <div className="bubble assistant typing" role="status">
                Thinking…
              </div>
            )}
            <div ref={endRef} />
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <form className="composer" onSubmit={onSubmit}>
            <textarea
              aria-label="Message"
              rows={2}
              value={input}
              disabled={working || !thread}
              placeholder="Ask a question, describe a role, or continue working on this job…"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <button type="submit" disabled={working || !thread || !input.trim()}>
              Send
            </button>
          </form>
        </section>
      </div>
    </>
  );
}
