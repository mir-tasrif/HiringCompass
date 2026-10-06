"""CLI: ingest a folder of .md/.txt files into a knowledge namespace.

Usage (inside the api container):
  python -m app.rag.ingest --namespace company
  python -m app.rag.ingest --namespace technical --dir /path/to/docs
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import SessionLocal
from app.llm.client import get_llm_client
from app.llm.embeddings import make_embed_fn
from app.rag.store import KnowledgeStore

_SUBDIR = {"company": "company", "technical": "technical"}


# Ingest every .md/.txt file in `directory`; returns (files_seen, files_created).
async def ingest_directory(namespace_key: str, directory: Path) -> tuple[int, int]:
    settings = get_settings()
    namespace = settings.rag_namespace_company if namespace_key == "company" else settings.rag_namespace_technical
    store = KnowledgeStore(make_embed_fn(get_llm_client(), settings), settings)
    seen = created = 0
    async with SessionLocal() as session:
        for path in sorted(p for p in directory.glob("*") if p.suffix.lower() in {".md", ".txt"}):
            seen += 1
            _, was_created = await store.ingest_document(
                session, namespace=namespace, title=path.stem.replace("_", " "), text=path.read_text(encoding="utf-8"),
                source_uri=f"file:{path.name}", version=path.stem,
            )
            created += int(was_created)
    return seen, created


# Entry point for `python -m app.rag.ingest`.
def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest documents into a knowledge namespace")
    parser.add_argument("--namespace", choices=sorted(_SUBDIR), required=True)
    parser.add_argument("--dir", type=Path, default=None, help="folder to read (default: corpora/<namespace>)")
    args = parser.parse_args()
    settings = get_settings()
    setup_logging("ingest", settings)
    directory = args.dir or settings.rag_corpus_dir / _SUBDIR[args.namespace]
    seen, created = asyncio.run(ingest_directory(args.namespace, directory))
    print(f"{args.namespace}: {seen} file(s) found, {created} newly ingested")


if __name__ == "__main__":
    main()