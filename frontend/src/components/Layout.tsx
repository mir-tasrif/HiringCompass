import { NavLink, Outlet } from "react-router-dom";
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
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <span className="brand">HiringCompass</span>
          <nav className="nav" aria-label="Main">
            {TABS.map((tab) => (
              <NavLink key={tab.to} to={tab.to} end={tab.end} className={({ isActive }) => (isActive ? "active" : undefined)}>
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