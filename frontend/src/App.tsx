// Route table: load feature pages separately so their code is easy to locate and shipped on demand.
import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
const ComingSoon = lazy(() => import("./pages/ComingSoon"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Login = lazy(() => import("./pages/Login"));
const Assistant = lazy(() => import("./pages/Assistant"));
const Jobs = lazy(() => import("./pages/Jobs"));
const JobDetailPage = lazy(() => import("./pages/JobDetail"));
const JobSubmissions = lazy(() => import("./pages/JobSubmissions"));
const Candidates = lazy(() => import("./pages/Candidates"));
const Interviews = lazy(() => import("./pages/Interviews"));
const InterviewRoom = lazy(() => import("./pages/InterviewRoom"));
const InterviewGuest = lazy(() => import("./pages/InterviewGuest"));

// Route table: public login, and every other page inside the authenticated layout.
export default function App() {
  return (
    <Suspense fallback={<p role="status">Loading page…</p>}><Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/interview/:invitation" element={<InterviewGuest />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="jobs/:jobId" element={<JobDetailPage />} />
        <Route path="jobs/:jobId/updates" element={<JobSubmissions />} />
        <Route path="candidates" element={<Candidates />} />
        <Route path="interviews" element={<Interviews />} />
        <Route path="interviews/:sessionId" element={<InterviewRoom />} />
        <Route path="reviews" element={<ComingSoon title="Reviews" description="Pending human-review decisions will be queued here." />} />
        <Route path="reports" element={<ComingSoon title="Reports" description="Candidate assessments and comparisons will appear here." />} />
        <Route path="assistant" element={<Assistant />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes></Suspense>
  );
}
