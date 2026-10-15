# Code walkthrough for a HiringCompass demonstration

Use this alongside [the feature-to-code map](feature-code-map.md). Each step names the screen, the request boundary, the business logic, and the saved result. Code moves should update these paths without changing the user-visible flow.

## 1. Recruiter creates a job through chat

1. `frontend/src/pages/Assistant.tsx` creates or opens a thread and sends a message through `frontend/src/api/chat.ts`.
2. `backend/app/api/routers/chat.py` authenticates the recruiter. `backend/app/services/chat_threads.py` creates and reads the thread; `backend/app/services/chat.py` coordinates a turn and records its reply. `backend/app/services/chat_candidate_actions.py` handles chat requests to start CV work; `backend/app/services/chat_job_status.py` summarizes posting and pipeline status.
3. `backend/app/graphs/chat_intent/graph.py` classifies the request. The LG2 files in `backend/app/graphs/lg2_job_intelligence/` collect details, prepare the draft, and wait for approval. System prompts are versioned in `backend/app/prompts/system.py`.
4. The draft and approval are stored with the chat, job, job version, and approval records in `backend/app/db/models.py`. `frontend/src/components/AssistantMessageCards.tsx` renders the draft and the approval controls.

## 2. Recruiter uploads and screens CVs

1. `frontend/src/pages/Jobs.tsx` and `frontend/src/pages/Candidates.tsx` call the jobs and candidates API modules.
2. `backend/app/api/routers/jobs.py` accepts submissions. `backend/app/api/routers/candidates.py` starts and reviews batches; `backend/app/services/candidate_queries.py` builds the read-only pipeline list. Candidate request/response shapes are in `backend/app/api/schemas/candidates.py`.
3. `backend/app/worker/cv_runner.py` registers F11, F1, and F2 graphs and starts `backend/app/services/cv_tasks.py`. F11 checks document integrity, CV parsing extracts a profile, F1 compares requirements with CV evidence, and F2 scores four dimensions. The corresponding graph and result modules are identified in the feature map.
4. The CV-related LLM user messages are assembled in `backend/app/prompts/user_messages.py`; system prompts and versions are in `backend/app/prompts/system.py`. Batch items, profiles, screening, scoring, and review events persist in PostgreSQL.

## 3. Recruiter schedules an interview

1. The recruiter can use `frontend/src/pages/Interviews.tsx` or issue a chat instruction in `frontend/src/pages/Assistant.tsx`.
2. Manual requests enter `backend/app/api/routers/interviews.py`; chat requests reach `backend/app/services/interview_chat.py` and `backend/app/services/interview_scheduling.py`.
3. `backend/app/services/interviews.py` creates the private invitation and performs delivery. Calendar connection routes are in `backend/app/api/routers/calendar_connection.py`, OAuth/token handling is in `backend/app/services/google_calendar_auth.py`, and MCP event calls and Google API fallback are in `backend/app/services/google_calendar_mcp.py`. SMTP delivery remains the fallback when Calendar invitation delivery fails.
4. The interview record, invitation state, and Calendar connection live in the database. The candidate receives the private public-web join URL.

## 4. Candidate and recruiter conduct the interview

1. `frontend/src/pages/InterviewGuest.tsx` opens the invitation; `frontend/src/pages/InterviewRoom.tsx` shows the recruiter's room. Both use `frontend/src/components/InterviewCall.tsx` for LiveKit media and local microphone handling.
2. `backend/app/api/routers/interviews.py` validates the invitation, controls the waiting room and admission, and issues a LiveKit token. LiveKit carries video and audio; the Cloudflare web tunnel serves the web invitation and API.
3. `frontend/src/components/assemblyaiStream.ts` handles AssemblyAI streaming when configured. Audio/transcript endpoints are in the interview routers; turns are saved as `InterviewTurn` records.
4. `backend/app/services/interview_ai.py` generates the prepared guide and answers the recruiter's private questions from job, CV, company knowledge, and transcript context.

## 5. Recruiter sends the latest transcript to F5

1. `frontend/src/pages/Interviews.tsx` offers transcript viewing and handoff. The handoff endpoint is in `backend/app/api/routers/interviews.py`.
2. `backend/app/services/f5_handoff.py` identifies the latest completed interview transcript. `frontend/src/pages/Candidates.tsx` shows the candidate as ready for F5.
3. `frontend/src/components/F5AnalysisPanel.tsx` starts a multi-candidate F5 batch; the same operation can be requested through chat with a confirmation preview. `backend/app/services/f5_worker.py` runs the independent graph in `backend/app/graphs/f5_interview_analysis/graph.py`.
4. The graph segments Q&A, maps job requirements, retrieves approved technical references, and records four dimensions with transcript evidence. Missing approved references produce a labelled provisional overall score. Review can approve for F6, request reanalysis, or reject the candidate. LG6 assessment remains planned.

## Preserving behavior while reorganizing

Keep API URLs, request/response fields, database schema, graph state keys, prompt versions, invitation token behavior, and external-provider fallbacks unchanged during code moves. Run `docker compose run --rm --no-deps --volume "${PWD}/backend:/app" api python -m pytest tests -q` from the project root and `npm run build` from `frontend/`. For Calendar, invitation email, LiveKit media, and live transcription, also perform a manual two-network interview check because unit tests cannot establish real provider connectivity.
