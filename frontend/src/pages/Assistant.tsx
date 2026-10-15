// Recruiter chat page: manages conversations, sends decisions, and renders reusable message cards.
import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { createThread, deleteThread, getThread, listThreads, sendDecision, sendMessage, ThreadDetail, ThreadSummary } from "../api/chat";
import { postJob } from "../api/jobs";

import { Bubble, DraftCard, JobUpdateCard } from "../components/AssistantMessageCards";

// AI Assistant page: conversation list, chat window, option chips, draft card and composer.
export default function Assistant() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [input, setInput] = useState("");
  const [threadsOpen, setThreadsOpen] = useState(false);
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
      <div className="assistant-heading"><h1>Job Intelligence</h1><div className="assistant-heading-actions">
        <button type="button" onClick={() => void newChat()}>New job chat</button>
        <button type="button" className="secondary" aria-expanded={threadsOpen} aria-controls="assistant-conversations"
          onClick={() => setThreadsOpen((open) => !open)}>{threadsOpen ? "Hide job chats" : "Show job chats"}</button></div></div>
      <div className={`assistant${threadsOpen ? "" : " assistant-wide"}`}>
        <aside id="assistant-conversations" className="card threads" aria-label="Conversations" hidden={!threadsOpen}>
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
                      {p.awaiting_confirmation === "scoring" ? <button type="button" disabled={Boolean(working)} onClick={() => void send("confirm F2")}>Confirm weights and start F2</button> : null}
                      <Link className="button-link" to={p.route}>
                        {p.route.startsWith("/interviews") ? "Open Interviews" : p.review_required || p.kind === "review" ? "Open Review" : p.awaiting_confirmation === "ranking" ? "Review Parsed CVs" : p.kind === "ranking" ? "Open Feature 1 Ranking" : p.kind === "scoring" ? "Open Feature 2 Scoring" : p.kind === "integrity" ? "Open Integrity Check" : "Select CVs in Candidates"}
                      </Link>
                    </div>}
                    {p?.type === "interview_roster" && <div className="posting-actions">
                      <Link className="button-link" to={p.route}>Open selected candidates</Link>
                      {p.candidates.some((candidate) => !candidate.interview_id) && <button type="button" disabled={Boolean(working)} onClick={() => void send("schedule all remaining selected candidates")}>Schedule remaining</button>}
                    </div>}
                    {p?.type === "interview_batch" && <div className="posting-actions">
                      {p.state === "preview" && <button type="button" disabled={Boolean(working) || m.id !== last?.id} onClick={() => void send("confirm schedule")}>Confirm and send invitations</button>}
                      {p.state === "result" && <Link className="button-link" to="/interviews">Open Interviews</Link>}
                      {p.state === "result" && (p.failed_application_ids?.length ?? 0) > 0 && <button type="button" disabled={Boolean(working)} onClick={() => void send("retry failed invitations")}>Retry failed invitations</button>}
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
