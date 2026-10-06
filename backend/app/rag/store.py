"""pgvector knowledge store: idempotent ingestion and namespace-isolated semantic search."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import PermanentError
from app.core.logging import get_logger
from app.db.models import KnowledgeChunk, KnowledgeSource
from app.llm.embeddings import EmbedFn
from app.rag.chunking import chunk_text
from app.schemas.contracts import EvidenceRef

logger = get_logger("rag.store")


# One retrieved passage with its provenance (source, version) and similarity score (1 = identical).
@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    namespace: str
    title: str
    source_uri: str | None
    version: str | None
    content: str
    score: float

    # Convert to an evidence reference for traceable conclusions.
    def to_evidence(self) -> EvidenceRef:
        return EvidenceRef(source="knowledge", ref_id=str(self.chunk_id), snippet=self.content[:500])


# Knowledge store bound to one embedding function; company and technical namespaces never mix.
class KnowledgeStore:
    def __init__(self, embed: EmbedFn, settings: Settings | None = None) -> None:
        self._embed = embed
        self._settings = settings or get_settings()
        self._namespaces = {self._settings.rag_namespace_company, self._settings.rag_namespace_technical}

    # Reject namespaces that are not configured.
    def _check_namespace(self, namespace: str) -> None:
        if namespace not in self._namespaces:
            raise PermanentError("Unknown knowledge namespace.")

    # Embed and store a document; re-ingesting identical text in a namespace is a no-op.
    # Returns (source_id, created).
    async def ingest_document(self, session: AsyncSession, *, namespace: str, title: str, text: str,
                              source_uri: str | None = None, version: str | None = None) -> tuple[uuid.UUID, bool]:
        self._check_namespace(namespace)
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = await session.scalar(
            select(KnowledgeSource.id).where(KnowledgeSource.namespace == namespace, KnowledgeSource.checksum == checksum)
        )
        if existing:
            return existing, False
        chunks = chunk_text(text)
        if not chunks:
            raise PermanentError("The document has no text to index.")
        vectors = await self._embed(chunks)
        if len(vectors) != len(chunks) or any(len(v) != 768 for v in vectors):
            raise PermanentError("Embedding size does not match the knowledge table (768).")
        source = KnowledgeSource(namespace=namespace, title=title, source_uri=source_uri, version=version, checksum=checksum)
        session.add(source)
        await session.flush()
        session.add_all(
            KnowledgeChunk(source_id=source.id, namespace=namespace, chunk_index=i, content=c, embedding=v)
            for i, (c, v) in enumerate(zip(chunks, vectors))
        )
        await session.commit()
        logger.info("document ingested", extra={"namespace": namespace, "chunks": len(chunks)})
        return source.id, True

    # Semantic search inside one namespace; returns the best matches first.
    async def search(self, session: AsyncSession, *, namespace: str, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        self._check_namespace(namespace)
        (vector,) = await self._embed([query])
        distance = KnowledgeChunk.embedding.cosine_distance(vector).label("distance")
        stmt = (
            select(KnowledgeChunk, KnowledgeSource, distance)
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
            .where(KnowledgeChunk.namespace == namespace)
            .order_by(distance)
            .limit(top_k or self._settings.rag_top_k)
        )
        rows = (await session.execute(stmt)).all()
        return [
            RetrievedChunk(c.id, s.id, namespace, s.title, s.source_uri, s.version, c.content, round(1.0 - float(d), 4))
            for c, s, d in rows
        ]

    # Remove a source and (via cascade) all of its chunks.
    async def delete_source(self, session: AsyncSession, source_id: uuid.UUID) -> None:
        source = await session.get(KnowledgeSource, source_id)
        if source:
            await session.delete(source)
            await session.commit()