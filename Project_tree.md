# Current project map

The former static file listing became outdated as the application grew. Use [docs/feature-code-map.md](docs/feature-code-map.md) to find each functionality and [docs/presentation-walkthrough.md](docs/presentation-walkthrough.md) to explain its execution path.

| Directory | Responsibility |
| --- | --- |
| `frontend/src/pages/` | Recruiter and candidate screens |
| `frontend/src/components/` | Reusable chat, interview, and layout components |
| `frontend/src/api/` | Browser-side request functions and types |
| `backend/app/api/routers/` | HTTP entry points and authorization |
| `backend/app/api/schemas/` | Request and response contracts |
| `backend/app/services/` | Business operations and external integrations |
| `backend/app/graphs/` | Implemented LangGraph workflows and orchestration |
| `backend/app/prompts/` | System prompts and feature message builders |
| `backend/app/db/` | Database models and session setup |
| `backend/app/worker/` | Background CV-processing worker |
| `backend/migrations/versions/` | PostgreSQL schema history |
| `backend/tests/` | Automated backend checks |
| `docs/diagrams/` | Original architecture plans; LG5 and LG6 remain planned |
