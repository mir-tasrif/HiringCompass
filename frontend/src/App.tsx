import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import ComingSoon from "./pages/ComingSoon";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Assistant from "./pages/Assistant";
import Jobs from "./pages/Jobs";
import JobDetailPage from "./pages/JobDetail";
import JobSubmissions from "./pages/JobSubmissions";
import Candidates from "./pages/Candidates";
import Interviews from "./pages/Interviews";
import InterviewRoom from "./pages/InterviewRoom";
import InterviewGuest from "./pages/InterviewGuest";

// Route table: public login, and every other page inside the authenticated layout.
export default function App() {
  return (
    <Routes>
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
    </Routes>
  );
}
