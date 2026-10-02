# HiringCompass

HiringCompass is an AI-assisted recruitment platform that helps hiring teams review candidate evidence and make informed decisions. Human reviewers remain responsible for hiring decisions.

## Project status

Planning and repository foundation.

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


## Repository structure

```text
.
├── docs/
│   
├── .env.example
├── .gitignore
└── README.md
```

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

## License

License to be determined.

