import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const TABS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/jobs", label: "Jobs" },
  { to: "/candidates", label: "Candidates" },
  { to: "/interviews", label: "Interviews" },
  { to: "/reviews", label: "Reviews" },
  { to: "/reports", label: "Reports" },
  { to: "/assistant", label: "AI Assistant" },
];

// Signed-in shell: brand, navigation tabs, user menu and the page outlet.
export default function Layout() {
  const { user, logout } = useAuth();
  const interviewRoom = /^\/interviews\/[^/]+\/?$/.test(useLocation().pathname);
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className={`app${interviewRoom ? " interview-shell" : ""}`}>
      {interviewRoom && <button type="button" className="interview-menu-toggle secondary"
        aria-controls="primary-navigation" aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}>☰ <span>Menu</span></button>}
      <header className={`topbar${interviewRoom && menuOpen ? " interview-menu-open" : ""}`}
        onMouseEnter={interviewRoom ? () => setMenuOpen(true) : undefined}
        onMouseLeave={interviewRoom ? () => setMenuOpen(false) : undefined}
        onFocusCapture={interviewRoom ? () => setMenuOpen(true) : undefined}
        onKeyDown={interviewRoom ? (event) => { if (event.key === "Escape") {
          setMenuOpen(false);
          document.querySelector<HTMLButtonElement>(".interview-menu-toggle")?.focus();
        } } : undefined}>
        <div className="topbar-inner">
          <span className="brand">HiringCompass</span>
          <nav id="primary-navigation" className="nav" aria-label="Main">
            {TABS.map((tab) => (
              <NavLink key={tab.to} to={tab.to} end={tab.end} onClick={() => setMenuOpen(false)} className={({ isActive }) => (isActive ? "active" : undefined)}>
                {tab.label}
              </NavLink>
            ))}
          </nav>
          <div className="user">
            <span className="muted">{user?.email}</span>
            <button type="button" className="secondary" onClick={logout}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </div>
  );
}
