"""
jack/knowledge.py — Knowledge ingestion and retrieval for Jack's Tier 2.

Handles:
  - Chunking text sources into embeddable pieces
  - Generating embeddings (OpenAI ada-002 or sentence-transformers)
  - Storing chunks + embeddings in PostgreSQL/pgvector
  - Similarity search for context retrieval

All knowledge chunks carry source attribution. No anonymous knowledge.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from .db import JackDB


def _vec(embedding: List[float]) -> str:
    """Serialize a float list as a pgvector string literal: '[0.1,0.2,...]'"""
    return "[" + ",".join(str(v) for v in embedding) + "]"

logger = logging.getLogger("jack.knowledge")


# =============================================================================
# Text chunking
# =============================================================================

def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 100,
    separator: str = "\n\n",
) -> List[str]:
    """
    Split text into overlapping chunks suitable for embedding.

    Tries to split on paragraph boundaries first, then falls back to
    sentence boundaries, then hard character splits.
    """
    # First, split on double newlines (paragraphs)
    paragraphs = [p.strip() for p in text.split(separator) if p.strip()]

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= chunk_size:
            current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            # If a single paragraph exceeds chunk_size, split it by sentences
            if len(para) > chunk_size:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                sub_chunk = ""
                for sent in sentences:
                    if len(sub_chunk) + len(sent) + 1 <= chunk_size:
                        sub_chunk = f"{sub_chunk} {sent}" if sub_chunk else sent
                    else:
                        if sub_chunk:
                            chunks.append(sub_chunk.strip())
                        sub_chunk = sent
                current_chunk = sub_chunk
            else:
                current_chunk = para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # Add overlap: prepend last N chars of previous chunk to each chunk
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(f"...{prev_tail}\n\n{chunks[i]}")
        chunks = overlapped

    return chunks


# =============================================================================
# Embedding generation
# =============================================================================

class EmbeddingProvider:
    """Generate embeddings via OpenAI or sentence-transformers."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        embed_cfg = cfg.get("embeddings", {})
        self.provider = embed_cfg.get("provider", "openai")
        self.dimension = int(embed_cfg.get("dimension", 1536))

        if self.provider == "openai":
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY required for knowledge embeddings")
            self._client = OpenAI(api_key=api_key)
            self._model = embed_cfg.get("openai_model", "text-embedding-ada-002")
            logger.info("Knowledge embeddings: OpenAI %s (%dd)", self._model, self.dimension)
        else:
            from sentence_transformers import SentenceTransformer
            st_model = embed_cfg.get("st_model", "all-MiniLM-L6-v2")
            self._st = SentenceTransformer(st_model)
            self.dimension = self._st.get_sentence_embedding_dimension()
            logger.info("Knowledge embeddings: sentence-transformers %s (%dd)", st_model, self.dimension)

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of text strings."""
        if self.provider == "openai":
            return self._embed_openai(texts)
        return self._embed_st(texts)

    def embed_one(self, text: str) -> List[float]:
        """Generate an embedding for a single text string."""
        return self.embed([text])[0]

    def _embed_openai(self, texts: List[str]) -> List[List[float]]:
        # OpenAI batch limit is ~2048 inputs; chunk if needed
        all_embeddings = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self._client.embeddings.create(
                input=batch,
                model=self._model,
            )
            all_embeddings.extend([d.embedding for d in response.data])
        return all_embeddings

    def _embed_st(self, texts: List[str]) -> List[List[float]]:
        embeddings = self._st.encode(texts, show_progress_bar=len(texts) > 50)
        return [e.tolist() for e in embeddings]


# =============================================================================
# Knowledge store (pgvector)
# =============================================================================

class KnowledgeStore:
    """
    Interface to the knowledge_sources and knowledge_chunks tables.
    Handles ingestion and similarity retrieval.
    """

    def __init__(self, db_cfg: Dict[str, Any], embedder: EmbeddingProvider) -> None:
        self.embedder = embedder
        self._conn_params = {
            "host": db_cfg.get("host", "localhost"),
            "port": int(db_cfg.get("port", 5432)),
            "dbname": db_cfg.get("dbname", "kiro"),
            "user": db_cfg.get("user", "kiro"),
            "password": db_cfg.get("password", ""),
        }

    def _conn(self):
        return psycopg2.connect(**self._conn_params)

    # ── Ingestion ─────────────────────────────────────────────────────────

    def ingest_source(
        self,
        source_id: int,
        text: str,
        topic_tags: List[str],
        chapter: str = "",
        page_ref: str = "",
        chunk_size: int = 800,
    ) -> int:
        """
        Chunk a text source, generate embeddings, and store in knowledge_chunks.

        Returns the number of chunks created.
        """
        chunks = chunk_text(text, chunk_size=chunk_size)
        if not chunks:
            logger.warning("No chunks generated for source_id=%d", source_id)
            return 0

        logger.info("Generating embeddings for %d chunks (source_id=%d)...", len(chunks), source_id)
        embeddings = self.embedder.embed(chunks)

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for chunk_text_item, embedding in zip(chunks, embeddings):
                    cur.execute(
                        """
                        INSERT INTO knowledge_chunks
                            (source_id, content, topic_tags, chapter, page_ref, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s::vector)
                        """,
                        (source_id, chunk_text_item, topic_tags, chapter, page_ref, _vec(embedding)),
                    )

                # Mark source as ingested
                cur.execute(
                    "UPDATE knowledge_sources SET ingested = TRUE, ingested_at = NOW() WHERE id = %s",
                    (source_id,),
                )
            conn.commit()
            logger.info("Ingested %d chunks for source_id=%d", len(chunks), source_id)
            return len(chunks)
        except Exception as exc:
            conn.rollback()
            logger.error("Ingestion failed for source_id=%d: %s", source_id, exc)
            raise
        finally:
            conn.close()

    def list_sources(self, ingested_only: bool = False) -> List[Dict[str, Any]]:
        """List all knowledge sources."""
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if ingested_only:
                    cur.execute("SELECT * FROM knowledge_sources WHERE ingested = TRUE ORDER BY id")
                else:
                    cur.execute("SELECT * FROM knowledge_sources ORDER BY id")
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # ── Retrieval ─────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.70,
        topic_filter: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Similarity search over knowledge_chunks using pgvector.

        Returns top-K chunks with source attribution, sorted by relevance.
        """
        query_embedding = self.embedder.embed_one(query)

        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if topic_filter:
                    # Filter by topic tags (any overlap)
                    cur.execute(
                        """
                        SELECT
                            kc.id,
                            kc.content,
                            kc.topic_tags,
                            kc.chapter,
                            kc.page_ref,
                            ks.name AS source_name,
                            ks.author AS source_author,
                            ks.base_confidence,
                            1 - (kc.embedding <=> %s::vector) AS similarity
                        FROM knowledge_chunks kc
                        JOIN knowledge_sources ks ON kc.source_id = ks.id
                        WHERE kc.topic_tags && %s::text[]
                        ORDER BY kc.embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (_vec(query_embedding), topic_filter, _vec(query_embedding), top_k),
                    )
                else:
                    cur.execute(
                        """
                        SELECT
                            kc.id,
                            kc.content,
                            kc.topic_tags,
                            kc.chapter,
                            kc.page_ref,
                            ks.name AS source_name,
                            ks.author AS source_author,
                            ks.base_confidence,
                            1 - (kc.embedding <=> %s::vector) AS similarity
                        FROM knowledge_chunks kc
                        JOIN knowledge_sources ks ON kc.source_id = ks.id
                        ORDER BY kc.embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (_vec(query_embedding), _vec(query_embedding), top_k),
                    )

                results = [dict(r) for r in cur.fetchall()]

            # Filter by minimum similarity
            return [r for r in results if float(r["similarity"]) >= min_similarity]
        finally:
            conn.close()

    def search_for_prompt(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.70,
    ) -> str:
        """
        Search and format results as a text block for system prompt injection.
        Includes source attribution on every chunk.
        """
        results = self.search(query, top_k=top_k, min_similarity=min_similarity)

        if not results:
            return ""

        lines = []
        for r in results:
            source = r["source_name"]
            author = r.get("source_author", "")
            confidence = r.get("base_confidence", "medium")
            ref = ""
            if r.get("chapter"):
                ref += f", ch: {r['chapter']}"
            if r.get("page_ref"):
                ref += f", p.{r['page_ref']}"

            attribution = f"[{source}"
            if author:
                attribution += f" — {author}"
            attribution += f" | confidence: {confidence}{ref}]"

            lines.append(f"{r['content']}\n{attribution}")

        return "\n\n---\n\n".join(lines)
