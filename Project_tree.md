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