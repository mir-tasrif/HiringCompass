// Chat message cards: plain messages, job activity, and draft job descriptions.
import { Link } from "react-router-dom";
import type { ChatMessage } from "../api/chat";
import FormattedJd from "./FormattedJd";
import FormattedMessage from "./FormattedMessage";

// Plain assistant or user bubble.
export function Bubble({ message, children }: { message: ChatMessage; children?: React.ReactNode }) {
  return (
    <div className={`bubble ${message.role}`}>
      <FormattedMessage text={message.content} />
      {children}
    </div>
  );
}

export function JobUpdateCard({
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
            {job.cv_pipeline && <li><strong>CV pipeline:</strong> {job.cv_pipeline.uploaded} uploaded · {job.cv_pipeline.integrity_check} checking · {job.cv_pipeline.integrity_review} integrity review · {job.cv_pipeline.parsed} parsed · {job.cv_pipeline.ranking} ranking · {job.cv_pipeline.f1_review} F1 review · {job.cv_pipeline.scoring} F2 ready · {job.cv_pipeline.scoring_processing ?? 0} scoring · {job.cv_pipeline.f2_review ?? 0} F2 review · {job.cv_pipeline.interview_selection ?? 0} selected for interview · {job.cv_pipeline.rejected} rejected</li>}
          </ul>
          <div className="posting-actions">
            {!job.archived && job.posting_status !== "posted" && <button type="button" disabled={!job.can_post || working || postingJobId !== null} onClick={() => onPost(job.job_id)}>
              {postingJobId === job.job_id ? "Posting…" : job.posting_status === "pending" ? "Discord post in progress" : job.posting_status === "failed" ? "Retry Discord post" : "Post to Discord"}
            </button>}
            {!job.posting_configured && job.posting_status !== "posted" && <span className="muted">Discord posting is not configured.</span>}
            {job.cv_submissions > 0 && <>
              {!job.archived && (job.cv_pipeline?.uploaded ?? 0) > 0 && <Link className="button-link" to={`/candidates?jobId=${encodeURIComponent(job.job_id)}&section=uploaded`}>Continue integrity check & parsing</Link>}
              {!job.archived && (job.cv_pipeline?.parsed ?? 0) > 0 && <Link className="button-link" to={`/candidates?jobId=${encodeURIComponent(job.job_id)}&section=parsed`}>Select CVs for Feature 1</Link>}
              {(job.archived || (job.cv_pipeline?.integrity_review ?? 0) > 0 || (job.cv_pipeline?.f1_review ?? 0) > 0 || (job.cv_pipeline?.f2_review ?? 0) > 0) && <Link className="button-link" to={`/candidates?jobId=${encodeURIComponent(job.job_id)}&section=${job.archived ? "uploaded" : "review"}`}>{job.archived ? "View candidate history" : "Review candidates"}</Link>}
              {(job.cv_pipeline?.interview_selection ?? 0) > 0 && <Link className="button-link" to={`/candidates?jobId=${encodeURIComponent(job.job_id)}&section=interview`}>View selected candidates</Link>}
            </>}
          </div>
        </article>
      ))}
    </div>
  );
}

// The draft job description with its Approve / Regenerate buttons (active only for the newest draft).
export function DraftCard(props: {
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

