"""
memory/vectorstore.py — ChromaDB wrapper for Kiro's L1 short-term vector memory.

Every conversation turn and explicit fact gets embedded via sentence-transformers
(all-MiniLM-L6-v2, ~80MB, runs on CPU) and stored in a persistent ChromaDB collection.
Retrieval is cosine similarity top-K.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List


class VectorStore:
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger) -> None:
        self.logger = logger
        self.path = cfg.get("path", "./data/chroma")
        embedding_provider = cfg.get("embedding_provider", "sentence_transformers")
        embedding_model = cfg.get("embedding_model", "all-MiniLM-L6-v2")

        import chromadb
        from chromadb.utils import embedding_functions

        self._client = chromadb.PersistentClient(path=self.path)

        if embedding_provider == "sentence_transformers":
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=embedding_model
            )
        else:
            raise ValueError(f"Unsupported embedding provider: {embedding_provider}")

        self._collection = self._client.get_or_create_collection(
            name="kiro_memory",
            embedding_function=ef,
        )
        self.logger.info(
            "VectorStore ready: %d docs in collection (path=%s)",
            self._collection.count(),
            self.path,
        )

    def add(self, text: str, metadata: Dict[str, Any] | None = None) -> None:
        doc_id = hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()
        self._collection.add(
            documents=[text],
            metadatas=[metadata or {}],
            ids=[doc_id],
        )

    def query(self, query_text: str, n_results: int = 5) -> List[str]:
        count = self._collection.count()
        if count == 0:
            return []
        results = self._collection.query(
            query_texts=[query_text],
            n_results=min(n_results, count),
        )
        return results.get("documents", [[]])[0]
