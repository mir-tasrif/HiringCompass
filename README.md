# HiringCompass

HiringCompass is an AI-assisted recruitment platform that helps hiring teams review candidate evidence and make informed decisions. Human reviewers remain responsible for hiring decisions.

## Run command:

.\scripts\start-interview-demo.ps1

With `LIVEKIT_URL` set to a LiveKit Cloud `wss://` address and its API credentials in `.env`, this command verifies the media service, refreshes the public invitation address, and starts the local app. See [interview setup](docs/interviews-local-setup.md). For the older same-Wi-Fi LiveKit mode, pass `-LanIp` with the current Wi-Fi IPv4 address.

## Project status

Job intelligence is implemented: normal conversation, guided job drafting, company RAG, draft review, and continued conversation after approval. CV intake, F11 integrity review, parsing, F1 ranking, F2 scoring, and recruiter review are available. Interviews use LiveKit Cloud for remote video; AssemblyAI streaming or Groq Whisper can provide transcription. Assessment remains under development.

## Company knowledge for job descriptions

Put company documents in `backend/app/rag/corpora/company/`. Supported formats are `.pdf`, `.md`, and `.txt`. PDFs preserve page labels; scanned pages use the English Tesseract OCR included in the backend image. Password-protected or unreadable documents are logged and skipped while other documents continue.

The job-drafting workflow checks this folder before retrieving company knowledge and indexes new content automatically. The folder is mounted into the API container, so adding documents does not require rebuilding the image. Identical content is not indexed twice. Existing indexed versions remain available; deleting a file from the folder does not delete its indexed copy.

To index documents immediately, including before asking general company questions, run:

```sh
docker compose exec api python -m app.rag.ingest --namespace company
```

Review the output for failed files. Retrieved company information and previously approved JDs inform drafting; previous JDs provide style references rather than new requirements. The sample company files are synthetic and should be replaced with approved company material for your demo.

The assistant responds normally to greetings and questions. Job clarification starts when you request a JD. You can discuss a waiting draft without regenerating it, continue after approval, or explicitly request a revision. Revisions require approval before replacing the active version.

## CV integrity, parsing, and ranking

Upload CVs under an approved job in the Jobs page. In Candidates, select up to 20 uploaded CVs and start **Proceed to CV integrity check and parsing**. A durable worker extracts PDF text, uses English Tesseract OCR for pages without usable text, checks for hidden/tiny/white text, unusual Unicode, and instruction-like content, then sends flagged documents to Review. OCR failures move to Rejected with a retained reason and event history. Clear CVs are parsed into education, experience, skills, certifications, and projects.

After parsing, select up to 20 CVs for **Feature 1 ranking**. Ranking compares each profile with its job's mandatory and preferred requirements and retrieved company hiring knowledge. Each requirement receives a Met, Partially Met, or Unmet classification with evidence. Candidates are ranked within the selected batch and job. Missing mandatory requirements trigger the screening gate; recruiters can reject candidates in Review, while only candidates that pass the gate can proceed to Feature 2 Scoring.

The worker runs as part of `docker compose up --build`. Progress and independent technical retries are stored in the database. In a linked job chat, explicitly name candidate labels (for example, `candidate001`) to start supported work. After parsing, the assistant asks before starting F1; human review decisions remain in the Candidates page.

## Local video interviews

F2-approved candidates appear in **Candidates → Selected for Interview**. Recruiters can schedule or reschedule a private interview, correct its invitation email, review generated questions, admit the candidate, and conduct a LiveKit video call. AssemblyAI receives each participant's separate microphone stream through the app when `STT_PROVIDER=assemblyai`; Groq Whisper remains available for short-segment transcription. Only role-labeled transcript turns are saved by the app. The recruiter can review saved turns and ask the private interview assistant for questions grounded in the job, CV, company knowledge, and conversation so far.

For a same-Wi-Fi demonstration without certificate installation, start the temporary HTTPS interview tunnel described in [interviews-local-setup.md](docs/interviews-local-setup.md). The guide also covers the LAN-only certificate option.

## Solution overview

The architecture plans six LangGraph workflows. Five graph components currently run: a read-only LG1 chat intent graph, LG2 job intelligence, LG3/F11 integrity, LG3/F1 requirement screening, and LG4/F2 scoring. The remaining stages use durable service and worker code or are still planned. See the [feature-to-code map](docs/feature-code-map.md) to trace each implemented workflow from screen to database:

1. **Central chatbot** — chat intent classification is a checkpointed graph; tool actions and confirmations still run in services.
2. **Job intelligence** — drafts job profiles and scoring rubrics, then waits for approval before activation.
3. **Application screening** — F11 integrity checks and F1 requirement assessment run in checkpointed LG3 graphs. CV extraction and recruiter review remain in services and the worker.
4. **Scoring** — calculates F2 scores and supports interview selection.
5. **Interview intelligence** — the latest completed transcript can be handed off to F5; the analysis graph is planned.
6. **Consolidated assessment** — combining screening, scoring, and interview evidence remains planned.

Each candidate workflow is isolated by job and candidate identifiers. CV batches, per-CV status, extracted profiles, integrity findings, screening evidence, and review events persist in PostgreSQL so processing can continue after navigation or service restarts.

## Planned technology

- Python and FastAPI
- LangGraph for workflow orchestration
- PostgreSQL for application data and workflow checkpoints
- pgvector for retrieval where needed
- LiveKit WebRTC with AssemblyAI streaming or Groq Whisper for transcription
- Docker Compose for local development


## Local setup

Application setup instructions will be added when the backend and frontend are implemented. Do not put credentials in source control. Copy `.env.example` to `.env` and provide local values when services are configured.

## Privacy and safety

Candidate documents and interview data are sensitive. Restrict access, avoid logging document contents or secrets, and follow the project data-retention requirements. AI-generated assessments support human review; they do not make the final hiring decision.

## Development plan

1. Establish backend, frontend, database, configuration, and persistent storage.
2. Implement shared LangGraph state, workflow handoffs, and checkpoint recovery.
3. Build job intelligence and pre-interview workflows.
4. Add interview and transcription services, then interview analysis and consolidated assessment.
5. Complete security, reliability, performance, end-to-end, and clean-machine release checks.



## Repository structure

The maintained [feature-to-code map](docs/feature-code-map.md) links each screen to its API, service or graph, data, and external provider. The [presentation walkthrough](docs/presentation-walkthrough.md) follows the main workflows end to end.

| Area | Location |
| --- | --- |
| Web pages and reusable UI | `frontend/src/pages/`, `frontend/src/components/` |
| Web API clients | `frontend/src/api/` |
| HTTP routers and contracts | `backend/app/api/routers/`, `backend/app/api/schemas/` |
| Business operations | `backend/app/services/` |
| Implemented LangGraphs | `backend/app/graphs/chat_intent/`, `backend/app/graphs/lg2_job_intelligence/`, `backend/app/graphs/lg3_screening/`, `backend/app/graphs/f2_scoring/` |
| System prompts and user-message builders | `backend/app/prompts/` |
| Database models and migrations | `backend/app/db/models.py`, `backend/migrations/versions/` |
| CV worker and backend tests | `backend/app/worker/`, `backend/tests/` |
| Startup and interview setup | `docker-compose.yml`, `scripts/` |

LG5 interview analysis and LG6 consolidated assessment are planned. The current F5 implementation hands off the latest completed transcript for later analysis.
