# HiringCompass

HiringCompass is an AI-assisted recruitment platform that helps hiring teams review candidate evidence and make informed decisions. Human reviewers remain responsible for hiring decisions.

## Project status

Job intelligence is implemented: normal conversation, guided job drafting, company RAG, draft review, and continued conversation after approval. Candidate screening, interviews and assessments remain under development.

## Company knowledge for job descriptions

Put company documents in `backend/app/rag/corpora/company/`. Supported formats are `.pdf`, `.md`, and `.txt`. PDFs preserve page labels; scanned pages use the English Tesseract OCR included in the backend image. Password-protected or unreadable documents are logged and skipped while other documents continue.

The job-drafting workflow checks this folder before retrieving company knowledge and indexes new content automatically. The folder is mounted into the API container, so adding documents does not require rebuilding the image. Identical content is not indexed twice. Existing indexed versions remain available; deleting a file from the folder does not delete its indexed copy.

To index documents immediately, including before asking general company questions, run:

```sh
docker compose exec api python -m app.rag.ingest --namespace company
```

Review the output for failed files. Retrieved company information and previously approved JDs inform drafting; previous JDs provide style references rather than new requirements. The sample company files are synthetic and should be replaced with approved company material for your demo.

The assistant responds normally to greetings and questions. Job clarification starts when you request a JD. You can discuss a waiting draft without regenerating it, continue after approval, or explicitly request a revision. Revisions require approval before replacing the active version. Other recruitment actions are not yet available.

## Solution overview

The system uses six LangGraph workflows:

1. **Central chatbot** — routes user intent, invokes tools, and requests confirmation when needed.
2. **Job intelligence** — drafts job profiles and scoring rubrics, then waits for approval before activation.
3. **Application screening** — extracts CV information, runs integrity checks and F1 screening, and pauses for human review.
4. **Scoring** — calculates F2 scores and supports interview selection.
5. **Interview intelligence** — analyzes interview transcripts against job requirements.
6. **Consolidated assessment** — combines screening, scoring, and interview evidence for human review.

Each candidate workflow is isolated by job and candidate identifiers. Shared persistence and LangGraph checkpoints are planned to support recovery after interruption.

## Planned technology

- Python and FastAPI
- LangGraph for workflow orchestration
- PostgreSQL for application data and workflow checkpoints
- pgvector for retrieval where needed
- WebRTC/managed RTC and Google Speech-to-Text for interview delivery and transcription
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
```
HiringCompass
├─ .dockerignore
├─ backend
│  ├─ alembic.ini
│  ├─ app
│  │  ├─ api
│  │  │  ├─ errors.py
│  │  │  ├─ routers
│  │  │  └─ __init__.py
│  │  ├─ approvals
│  │  ├─ core
│  │  │  ├─ config.py
│  │  │  ├─ errors.py
│  │  │  ├─ logging.py
│  │  │  ├─ middleware.py
│  │  │  └─ security.py
│  │  ├─ db
│  │  │  ├─ base.py
│  │  │  ├─ models
│  │  │  ├─ models.py
│  │  │  ├─ repositories
│  │  │  ├─ session.py
│  │  │  └─ __init__.py
│  │  ├─ graphs
│  │  │  ├─ common
│  │  │  │  ├─ base_state.py
│  │  │  │  ├─ checkpointer.py
│  │  │  │  ├─ handoff.py
│  │  │  │  ├─ node_guard.py
│  │  │  │  └─ __init__.py
│  │  │  ├─ lg1_chatbot
│  │  │  │  ├─ graph.py
│  │  │  │  ├─ nodes.py
│  │  │  │  ├─ routing.py
│  │  │  │  └─ state.py
│  │  │  ├─ lg2_job_intelligence
│  │  │  ├─ lg3_screening
│  │  │  │  └─ f11_subgraph.py
│  │  │  ├─ lg4_scoring
│  │  │  ├─ lg5_interview_analysis
│  │  │  ├─ lg6_assessment
│  │  │  ├─ orchestrator.py
│  │  │  └─ __init__.py
│  │  ├─ llm
│  │  │  ├─ client.py
│  │  │  ├─ embeddings.py
│  │  │  ├─ structured.py
│  │  │  └─ __init__.py
│  │  ├─ main.py
│  │  ├─ rag
│  │  │  ├─ chunking.py
│  │  │  ├─ corpora
│  │  │  │  ├─ company
│  │  │  │  │  └─ hiring_policy_sample.md
│  │  │  │  └─ technical
│  │  │  ├─ ingest.py
│  │  │  ├─ store.py
│  │  │  └─ __init__.py
│  │  ├─ realtime
│  │  │  └─ ws_audio.py
│  │  ├─ schemas
│  │  │  ├─ approvals.py
│  │  │  ├─ contracts.py
│  │  │  ├─ domain.py
│  │  │  ├─ enums.py
│  │  │  └─ __init__.py
│  │  ├─ services
│  │  │  ├─ uploads.py
│  │  │  └─ __init__.py
│  │  ├─ tools
│  │  └─ worker
│  ├─ Dockerfile
│  ├─ migrations
│  │  ├─ env.py
│  │  ├─ script.py.mako
│  │  └─ versions
│  │     ├─ 0001_base_schema.py
│  │     └─ 0002_knowledge_base.py
│  ├─ pyproject.toml
│  ├─ requirements.txt
│  └─ tests
│     ├─ test_checkpoint_resume.py
│     ├─ test_graph_skeleton.py
│     ├─ test_llm_rag.py
│     ├─ test_uploads.py
│     └─ __init__.py
├─ docker
│  └─ db
│     └─ init.sql
├─ docker-compose.yml
├─ docs
│  ├─ diagrams
│  │  ├─ HiringCompass_High_Level_Workflow.drawio.pdf
│  │  ├─ HiringCompass_LG1_central_chatbot.drawio.pdf
│  │  ├─ HiringCompass_LG2_job_intelligence.drawio.pdf
│  │  ├─ HiringCompass_LG3_application_screening.drawio.pdf
│  │  ├─ HiringCompass_LG4_scoring.drawio.pdf
│  │  ├─ HiringCompass_LG5_interview_analysis_f5.drawio.pdf
│  │  ├─ HiringCompass_LG6_assessment_f6.drawio.pdf
│  │  └─ HiringCompass_Modular_Architecture_Final.drawio.pdf
│  ├─ Project Clarification.xlsx
│  ├─ Risk Register.pdf
│  └─ WBS-HiringCompass_Mir_Tasrif_Ahmed_30217.xlsx
├─ errors
├─ frontend
│  ├─ .dockerignore
│  ├─ Dockerfile
│  ├─ index.html
│  ├─ nginx.conf
│  ├─ package.json
│  ├─ src
│  │  ├─ api
│  │  │  └─ client.ts
│  │  ├─ App.tsx
│  │  ├─ components
│  │  │  └─ ChatDock
│  │  ├─ hooks
│  │  │  └─ useHealth.ts
│  │  ├─ main.tsx
│  │  ├─ pages
│  │  └─ styles.css
│  ├─ tsconfig.json
│  └─ vite.config.ts
├─ hiringcompass_state_schemas.py
├─ logs
├─ README.md
├─ scripts
├─ setup_project.py
└─ tests
   ├─ fixtures
   │  └─ synthetic_cvs
   ├─ integration
   ├─ mock_runners
   └─ unit

```
```
HiringCompass
├─ .dockerignore
├─ backend
│  ├─ alembic.ini
│  ├─ app
│  │  ├─ api
│  │  │  ├─ deps.py
│  │  │  ├─ errors.py
│  │  │  ├─ routers
│  │  │  │  ├─ auth.py
│  │  │  │  ├─ chat.py
│  │  │  │  └─ __init__.py
│  │  │  └─ __init__.py
│  │  ├─ approvals
│  │  ├─ core
│  │  │  ├─ config.py
│  │  │  ├─ errors.py
│  │  │  ├─ logging.py
│  │  │  ├─ middleware.py
│  │  │  └─ security.py
│  │  ├─ db
│  │  │  ├─ base.py
│  │  │  ├─ models
│  │  │  ├─ models.py
│  │  │  ├─ repositories
│  │  │  ├─ session.py
│  │  │  └─ __init__.py
│  │  ├─ graphs
│  │  │  ├─ common
│  │  │  │  ├─ base_state.py
│  │  │  │  ├─ checkpointer.py
│  │  │  │  ├─ handoff.py
│  │  │  │  ├─ node_guard.py
│  │  │  │  └─ __init__.py
│  │  │  ├─ lg1_chatbot
│  │  │  │  ├─ graph.py
│  │  │  │  ├─ nodes.py
│  │  │  │  ├─ routing.py
│  │  │  │  └─ state.py
│  │  │  ├─ lg2_job_intelligence
│  │  │  │  ├─ deps.py
│  │  │  │  ├─ drafts.py
│  │  │  │  ├─ graph.py
│  │  │  │  ├─ nodes.py
│  │  │  │  ├─ posting.py
│  │  │  │  ├─ prompts.py
│  │  │  │  ├─ routing.py
│  │  │  │  ├─ state.py
│  │  │  │  └─ __init__.py
│  │  │  ├─ lg3_screening
│  │  │  │  └─ f11_subgraph.py
│  │  │  ├─ lg4_scoring
│  │  │  ├─ lg5_interview_analysis
│  │  │  ├─ lg6_assessment
│  │  │  ├─ orchestrator.py
│  │  │  └─ __init__.py
│  │  ├─ llm
│  │  │  ├─ client.py
│  │  │  ├─ embeddings.py
│  │  │  ├─ structured.py
│  │  │  └─ __init__.py
│  │  ├─ main.py
│  │  ├─ rag
│  │  │  ├─ chunking.py
│  │  │  ├─ corpora
│  │  │  │  ├─ company
│  │  │  │  │  └─ hiring_policy_sample.md
│  │  │  │  └─ technical
│  │  │  ├─ ingest.py
│  │  │  ├─ store.py
│  │  │  └─ __init__.py
│  │  ├─ realtime
│  │  │  └─ ws_audio.py
│  │  ├─ schemas
│  │  │  ├─ approvals.py
│  │  │  ├─ contracts.py
│  │  │  ├─ domain.py
│  │  │  ├─ enums.py
│  │  │  └─ __init__.py
│  │  ├─ services
│  │  │  ├─ approvals.py
│  │  │  ├─ chat.py
│  │  │  ├─ dispatch.py
│  │  │  ├─ fairness.py
│  │  │  ├─ jobs.py
│  │  │  ├─ uploads.py
│  │  │  ├─ users.py
│  │  │  └─ __init__.py
│  │  ├─ tools
│  │  └─ worker
│  ├─ Dockerfile
│  ├─ migrations
│  │  ├─ env.py
│  │  ├─ script.py.mako
│  │  └─ versions
│  │     ├─ 0001_base_schema.py
│  │     ├─ 0002_knowledge_base.py
│  │     └─ 0003_chat.py
│  ├─ pyproject.toml
│  ├─ requirements.txt
│  └─ tests
│     ├─ helpers.py
│     ├─ test_auth.py
│     ├─ test_chat.py
│     ├─ test_checkpoint_resume.py
│     ├─ test_graph_skeleton.py
│     ├─ test_lg2_job_intelligence.py
│     ├─ test_llm_rag.py
│     ├─ test_uploads.py
│     └─ __init__.py
├─ docker
│  └─ db
│     └─ init.sql
├─ docker-compose.yml
├─ docs
│  ├─ diagrams
│  │  ├─ HiringCompass_High_Level_Workflow.drawio.pdf
│  │  ├─ HiringCompass_LG1_central_chatbot.drawio.pdf
│  │  ├─ HiringCompass_LG2_job_intelligence.drawio.pdf
│  │  ├─ HiringCompass_LG3_application_screening.drawio.pdf
│  │  ├─ HiringCompass_LG4_scoring.drawio.pdf
│  │  ├─ HiringCompass_LG5_interview_analysis_f5.drawio.pdf
│  │  ├─ HiringCompass_LG6_assessment_f6.drawio.pdf
│  │  └─ HiringCompass_Modular_Architecture_Final.drawio.pdf
│  ├─ Project Clarification.xlsx
│  ├─ Risk Register.pdf
│  └─ WBS-HiringCompass_Mir_Tasrif_Ahmed_30217.xlsx
├─ errors
├─ frontend
│  ├─ .dockerignore
│  ├─ Dockerfile
│  ├─ index.html
│  ├─ nginx.conf
│  ├─ package.json
│  ├─ src
│  │  ├─ api
│  │  │  ├─ chat.ts
│  │  │  └─ client.ts
│  │  ├─ App.tsx
│  │  ├─ auth
│  │  │  └─ AuthContext.tsx
│  │  ├─ components
│  │  │  ├─ ChatDock
│  │  │  ├─ Layout.tsx
│  │  │  └─ RequireAuth.tsx
│  │  ├─ hooks
│  │  │  └─ useHealth.ts
│  │  ├─ main.tsx
│  │  ├─ pages
│  │  │  ├─ Assistant.tsx
│  │  │  ├─ ComingSoon.tsx
│  │  │  ├─ Dashboard.tsx
│  │  │  └─ Login.tsx
│  │  └─ styles.css
│  ├─ tsconfig.json
│  └─ vite.config.ts
├─ hiringcompass_state_schemas.py
├─ logs
├─ package-lock.json
├─ package.json
├─ Project_tree.md
├─ README.md
├─ scripts
├─ setup_project.py
└─ tests
   ├─ fixtures
   │  └─ synthetic_cvs
   ├─ integration
   ├─ mock_runners
   └─ unit

```
