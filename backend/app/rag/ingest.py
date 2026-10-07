"""Ingest Markdown, text and PDF company or technical knowledge.

Usage (inside the api container):
  python -m app.rag.ingest --namespace company
  python -m app.rag.ingest --namespace technical --dir /path/to/docs
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from app.core.config import Settings, get_settings
from app.core.errors import PermanentError
from app.core.logging import get_logger, log_exception, setup_logging
from app.db.session import SessionLocal
from app.llm.client import get_llm_client
from app.llm.embeddings import make_embed_fn
from app.rag.store import KnowledgeStore

_SUBDIR = {"company": "company", "technical": "technical"}
logger = get_logger("rag.ingest")


def read_knowledge_file(path: Path, settings: Settings) -> str:
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8")
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise PermanentError("The knowledge PDF could not be opened.") from exc
    with document:
        if document.needs_pass or document.is_encrypted:
            raise PermanentError("Password-protected knowledge PDFs are not supported.")
        pages = []
        for number, page in enumerate(document, 1):
            text = page.get_text("text", sort=True).strip()
            if not text:
                if not settings.ocr_enabled:
                    raise PermanentError("The knowledge PDF needs OCR, which is disabled.")
                with tempfile.TemporaryDirectory(prefix="hc-knowledge-") as temp:
                    image = Path(temp) / "page.png"
                    scale = min(2.0, 4000 / max(page.rect.width, page.rect.height))
                    page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(image)
                    try:
                        result = subprocess.run(
                            [settings.tesseract_cmd, str(image), "stdout", "-l", settings.ocr_language],
                            capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise PermanentError("OCR failed for the knowledge PDF.") from exc
                    text = result.stdout.strip()
                if not text:
                    raise PermanentError("A knowledge PDF page has no readable text after OCR.")
            pages.append(f"[Page {number}]\n{text}")
    if not pages:
        raise PermanentError("The knowledge PDF has no pages.")
    return "\n\n".join(pages)


# Failed documents are logged individually; valid files continue to be indexed.
async def ingest_directory(namespace_key: str, directory: Path, *, settings: Settings | None = None,
                           store: KnowledgeStore | None = None, session_factory=None) -> tuple[int, int, int]:
    settings = settings or get_settings()
    if namespace_key not in _SUBDIR:
        raise PermanentError("Unknown knowledge namespace.")
    if not directory.is_dir():
        raise PermanentError("The knowledge folder does not exist.")
    namespace = settings.rag_namespace_company if namespace_key == "company" else settings.rag_namespace_technical
    store = store or KnowledgeStore(make_embed_fn(get_llm_client(), settings), settings)
    session_factory = session_factory or SessionLocal
    seen = created = failed = 0
    async with session_factory() as session:
        for path in sorted(p for p in directory.glob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt", ".pdf"}):
            seen += 1
            try:
                text = await asyncio.to_thread(read_knowledge_file, path, settings)
                _, was_created = await store.ingest_document(
                    session, namespace=namespace, title=path.stem.replace("_", " "), text=text,
                    source_uri=f"file:{path.name}", version=path.stem,
                )
            except Exception as exc:
                await session.rollback()
                failed += 1
                log_exception(logger, "knowledge document ingestion failed", exc, knowledge_file=path.name)
                continue
            created += int(was_created)
    return seen, created, failed


# Entry point for `python -m app.rag.ingest`.
def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest documents into a knowledge namespace")
    parser.add_argument("--namespace", choices=sorted(_SUBDIR), required=True)
    parser.add_argument("--dir", type=Path, default=None, help="folder to read (default: corpora/<namespace>)")
    args = parser.parse_args()
    settings = get_settings()
    setup_logging("ingest", settings)
    directory = args.dir or settings.rag_corpus_dir / _SUBDIR[args.namespace]
    seen, created, failed = asyncio.run(ingest_directory(args.namespace, directory))
    print(f"{args.namespace}: {seen} file(s) found, {created} newly ingested, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
