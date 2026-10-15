# HiringCompass feature-to-code map

This map follows the currently implemented paths. Start at the page, then follow its API router, service or graph, and persisted data. The architecture diagrams describe the original plan; LG5 analysis and LG6 assessment are not yet implemented as LangGraphs.

| Functionality | Screen | API entry | Main implementation | Data and external systems |
| --- | --- | --- | --- | --- |
| Sign in and session | `frontend/src/pages/Login.tsx`, `frontend/src/api/client.ts` | `backend/app/api/routers/auth.py` | `backend/app/services/users.py`, `backend/app/core/security.py` | `User`; password hash and authentication token |
| Job drafting and approval | `frontend/src/pages/Assistant.tsx`, `frontend/src/components/AssistantMessageCards.tsx` | `backend/app/api/routers/chat.py` | `backend/app/services/chat_threads.py`, `backend/app/services/chat_candidate_actions.py`, `backend/app/services/chat_job_status.py`, `backend/app/services/chat.py`, `backend/app/graphs/chat_intent/graph.py`, `backend/app/graphs/lg2_job_intelligence/` | `ChatThread`, `ChatMessage`, `Job`, `JobVersion`, `Approval`; LLM and company RAG |
| Approved jobs and CV intake | `frontend/src/pages/Jobs.tsx` | `backend/app/api/routers/jobs.py` | `backend/app/services/jobs.py`, `backend/app/services/cv_tasks.py` | `Job`, `Application`, `Document`, `CvBatch`; stored PDF/DOCX |
| F11 integrity and CV parsing | `frontend/src/pages/Candidates.tsx`, `frontend/src/components/CandidateStageNav.tsx` | `backend/app/api/routers/candidates.py` | `backend/app/services/candidate_queries.py`, `backend/app/graphs/lg3_screening/f11_integrity_graph.py`, `backend/app/services/f11_integrity.py`, `backend/app/services/cv_pipeline.py`, `backend/app/worker/cv_runner.py` | `CandidateProfile`, `CvBatchItem`, stage events; LLM for profile extraction |
| F1 requirement screening | `frontend/src/pages/Candidates.tsx` | `backend/app/api/routers/candidates.py` | `backend/app/graphs/lg3_screening/f1_screening_graph.py`, `backend/app/services/f1_screening_results.py` | `CandidateScreening`, job requirements; LLM |
| F2 score and recruiter review | `frontend/src/pages/Candidates.tsx` | `backend/app/api/routers/candidates.py` | `backend/app/graphs/f2_scoring/f2_scoring_graph.py`, `backend/app/services/f2_scoring_results.py`, `backend/app/services/f2_policy.py` | `CandidateScore`, `ApplicationEvent`; LLM |
| Chat-driven interview scheduling | `frontend/src/pages/Assistant.tsx` | `backend/app/api/routers/chat.py` | `backend/app/services/interview_chat.py`, `backend/app/services/interview_scheduling.py` | `InterviewSession`; Google Calendar, SMTP fallback |
| Manual scheduling and invitations | `frontend/src/pages/Interviews.tsx` | `backend/app/api/routers/interviews.py`, `backend/app/api/routers/calendar_connection.py` | `backend/app/services/interviews.py`, `backend/app/services/google_calendar_auth.py`, `backend/app/services/google_calendar_mcp.py` | `InterviewSession`, `GoogleCalendarConnection`; Calendar MCP/API, SMTP, public web URL |
| Private video interview | `frontend/src/pages/InterviewRoom.tsx`, `frontend/src/components/InterviewCall.tsx` | `backend/app/api/routers/interviews.py` | `backend/app/services/interviews.py` | LiveKit Cloud room/token; interview status and admission |
| English transcription and interview assistant | `frontend/src/components/assemblyaiStream.ts`, `frontend/src/pages/InterviewRoom.tsx` | `backend/app/api/routers/interviews.py`, `backend/app/api/routers/interview_stream.py` | `backend/app/services/interviews.py`, `backend/app/services/interview_ai.py`, interview prompts in `backend/app/prompts/system.py` | `InterviewTurn`; AssemblyAI or configured fallback STT, LLM assistant |
| F5 transcript handoff | `frontend/src/pages/Interviews.tsx`, `frontend/src/pages/Candidates.tsx` | `backend/app/api/routers/interviews.py`, `backend/app/api/routers/candidates.py` | `backend/app/services/f5_handoff.py` | `F5Handoff`, latest completed interview transcript; full F5 analysis is future work |

The API contract classes for candidate and interview endpoints live in `backend/app/api/schemas/candidates.py` and `backend/app/api/schemas/interviews.py`. Database entities are currently declared in `backend/app/db/models.py`. Graph registration and durable execution are in `backend/app/graphs/orchestrator.py`; graph construction happens at API startup in `backend/app/main.py` and in the CV worker at `backend/app/worker/cv_runner.py`. System prompt text and versions are in `backend/app/prompts/system.py`; CV profile, F1, and F2 user-message builders are in `backend/app/prompts/user_messages.py`.

## How to trace a feature during a demonstration

1. Show the page and its `frontend/src/api/` call.
2. Show the matching function in `backend/app/api/routers/` and its request/response contract.
3. Follow the called service or graph node. Explain its input, decision, side effect, and failure path.
4. Show the database model and, where applicable, the external provider call.
5. Run the focused test from `backend/tests/`, then show the resulting UI state.

The router URL and response shape are the stable boundary for internal refactoring. The Calendar invitation fallback, private join token, CV stage transitions, and LiveKit/transcription lifecycle need focused regression checks whenever their internals move.
