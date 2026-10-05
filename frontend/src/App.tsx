import { Status, useHealth } from "./hooks/useHealth";

const LABEL: Record<Status, string> = { checking: "Checking…", up: "Operational", down: "Unavailable" };

// One row in the system status card.
function StatusRow({ name, status }: { name: string; status: Status }) {
  return (
    <li className="status-row">
      <span>{name}</span>
      <span className={`pill pill-${status}`} role="status">
        {LABEL[status]}
      </span>
    </li>
  );
}

// Application shell: branded header, system status, and the area where feature pages will mount.
export default function App() {
  const { api, db, env, checkedAt, refresh } = useHealth();

  return (
    <div className="shell">
      <header className="header">
        <h1>HiringCompass</h1>
        <p>An AI recruitment assistant that guides hiring teams with evidence-backed assessments, keeping humans in control of every decision.</p>
      </header>
      <main className="content">
        <section className="card" aria-labelledby="status-title">
          <h2 id="status-title">System status</h2>
          <ul className="status-list">
            <StatusRow name="API" status={api} />
            <StatusRow name="Database" status={db} />
          </ul>
          <p className="muted">
            {env ? `Environment: ${env}. ` : ""}
            {checkedAt ? `Last checked ${checkedAt.toLocaleTimeString()}.` : "Waiting for first check."}
          </p>
          <button type="button" onClick={refresh}>
            Check again
          </button>
        </section>
      </main>
    </div>
  );
}