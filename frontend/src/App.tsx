import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import ComingSoon from "./pages/ComingSoon";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";

// Route table: public login, and every other page inside the authenticated layout.
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="jobs" element={<ComingSoon title="Jobs" description="Approved job descriptions will be listed and managed here." />} />
        <Route path="candidates" element={<ComingSoon title="Candidates" description="Uploaded CVs, screening results and rankings will appear here." />} />
        <Route path="interviews" element={<ComingSoon title="Interviews" description="Scheduled and live video interviews will appear here." />} />
        <Route path="reviews" element={<ComingSoon title="Reviews" description="Pending human-review decisions will be queued here." />} />
        <Route path="reports" element={<ComingSoon title="Reports" description="Candidate assessments and comparisons will appear here." />} />
        <Route path="assistant" element={<ComingSoon title="Job Intelligence" description="The AI assistant for drafting and posting jobs is built in the next step." />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}