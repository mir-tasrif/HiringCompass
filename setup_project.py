import os
from pathlib import Path

# List of all directories to create
directories = [
    "logs",
    "errors",
    "docs",
    "scripts",
    "backend/migrations",
    "backend/app/core",
    "backend/app/schemas",
    "backend/app/db/models",
    "backend/app/db/repositories",
    "backend/app/llm",
    "backend/app/rag/corpora/company",
    "backend/app/rag/corpora/technical",
    "backend/app/services",
    "backend/app/tools",
    "backend/app/approvals",
    "backend/app/worker",
    "backend/app/graphs/common",
    "backend/app/graphs/lg1_chatbot",
    "backend/app/graphs/lg2_job_intelligence",
    "backend/app/graphs/lg3_screening",
    "backend/app/graphs/lg4_scoring",
    "backend/app/graphs/lg5_interview_analysis",
    "backend/app/graphs/lg6_assessment",
    "backend/app/api/routers",
    "backend/app/realtime",
    "frontend/src/pages",
    "frontend/src/components/ChatDock",
    "frontend/src/api",
    "frontend/src/hooks",
    "tests/unit",
    "tests/integration",
    "tests/mock_runners",
    "tests/fixtures/synthetic_cvs",
]

# List of essential placeholder files to touch
files = [
    "docker-compose.yml",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    "README.md",
    "backend/Dockerfile",
    "backend/pyproject.toml",
    "backend/app/main.py",
    "backend/app/core/config.py",
    "backend/app/core/logging.py",
    "backend/app/core/errors.py",
    "backend/app/core/middleware.py",
    "backend/app/core/security.py",
    "backend/app/schemas/enums.py",
    "backend/app/schemas/domain.py",
    "backend/app/schemas/approvals.py",
    "backend/app/db/session.py",
    "backend/app/llm/client.py",
    "backend/app/llm/structured_output.py",
    "backend/app/llm/embeddings.py",
    "backend/app/rag/store.py",
    "backend/app/rag/ingest.py",
    "backend/app/graphs/common/checkpointer.py",
    "backend/app/graphs/common/base_state.py",
    "backend/app/graphs/common/node_guard.py",
    "backend/app/graphs/lg1_chatbot/state.py",
    "backend/app/graphs/lg1_chatbot/nodes.py",
    "backend/app/graphs/lg1_chatbot/routing.py",
    "backend/app/graphs/lg1_chatbot/graph.py",
    "backend/app/graphs/lg3_screening/f11_subgraph.py",
    "backend/app/realtime/ws_audio.py",
]

# Set base directory to the current working directory
base_dir = Path(".")

# Create directories
for d in directories:
    (base_dir / d).mkdir(parents=True, exist_ok=True)

# Create empty files
for f in files:
    file_path = base_dir / f
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.touch(exist_ok=True)

print("Project structure created directly in the current directory!")