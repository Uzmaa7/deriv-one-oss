"""What answers a search over an agent's documents.

An agent can be given a private set of documents and a tool that searches them.
The platform needs four things from whatever provides that: somewhere to put a
namespace of documents, a way to add and remove documents, a way to search, and
a way to report per-document state so the UI can show an upload progressing from
"received" to "searchable".

Indexing is asynchronous by contract. ``index`` returns as soon as the documents
are accepted; ``document_states`` is how a caller learns they became searchable.
Providers that index synchronously simply return a finished state immediately.

The reference implementation keeps a full-text index in a local database file.
It is a genuine BM25 keyword index, not a stub, and is adequate for document
sets in the thousands. It performs no semantic matching — a deployment that
needs embeddings supplies a provider that does.
"""

from __future__ import annotations

import hashlib

import json
import logging
import os
import re
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


class DocumentState(str, Enum):
    PENDING = "pending"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


@dataclass
class Document:
    """A document offered for indexing."""

    id: str
    text: str
    title: str = ""
    uri: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class RetrievedChunk:
    """One search hit.

    ``score`` is higher-is-better but **provider-relative**: its magnitude
    depends on the scoring function and the corpus, and is not comparable across
    providers or across namespaces. Use it to order results, never to threshold
    them. Callers that need "is this good enough" must decide that from the
    text, not the number.
    """

    document_id: str
    text: str
    score: float
    title: str = ""
    uri: str = ""


@dataclass
class DocumentStatus:
    document_id: str
    state: DocumentState
    error: Optional[str] = None
    updated_at: float = 0.0


class RetrievalProvider(ABC):
    """A namespaced document index."""

    @abstractmethod
    def provision(self, namespace: str) -> None:
        """Create ``namespace``. Idempotent."""

    @abstractmethod
    def deprovision(self, namespace: str) -> None:
        """Destroy ``namespace`` and everything in it. Idempotent."""

    @abstractmethod
    def index(self, namespace: str, documents: Iterable[Document]) -> None:
        """Accept documents for indexing. May complete asynchronously."""

    @abstractmethod
    def delete_document(self, namespace: str, document_id: str) -> None:
        """Remove one document. Absent documents are not an error."""

    @abstractmethod
    def search(
        self, namespace: str, query: str, *, limit: int = 5
    ) -> list[RetrievedChunk]:
        """Best ``limit`` matches for ``query``, highest score first."""

    @abstractmethod
    def document_states(self, namespace: str) -> list[DocumentStatus]:
        """Current state of every document in ``namespace``."""


def _safe_namespace(namespace: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "_", (namespace or "").strip())
    if not cleaned:
        raise ValueError("namespace must not be empty")
    return cleaned[:128]


class LocalRetrievalProvider(RetrievalProvider):
    """Keyword search over a local full-text index.

    One database file per namespace under ``RETRIEVAL_ROOT`` (default
    ``./var/retrieval``), so deleting a namespace is deleting a file and one
    agent's index can never leak into another's.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root = Path(
            root or os.environ.get("RETRIEVAL_ROOT") or "var/retrieval"
        ).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, namespace: str) -> Path:
        return self._root / f"{_safe_namespace(namespace)}.db"

    def _connect(self, namespace: str, *, create: bool = False) -> sqlite3.Connection:
        path = self._path(namespace)
        if not create and not path.exists():
            raise KeyError(f"unknown retrieval namespace: {namespace}")
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def provision(self, namespace: str) -> None:
        with self._lock:
            conn = self._connect(namespace, create=True)
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS documents (
                        id          TEXT PRIMARY KEY,
                        title       TEXT NOT NULL DEFAULT '',
                        uri         TEXT NOT NULL DEFAULT '',
                        metadata    TEXT NOT NULL DEFAULT '{}',
                        state       TEXT NOT NULL DEFAULT 'pending',
                        error       TEXT,
                        updated_at  REAL NOT NULL DEFAULT 0,
                        content_hash TEXT DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
                    CREATE VIRTUAL TABLE IF NOT EXISTS document_text
                        USING fts5(document_id UNINDEXED, body);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def deprovision(self, namespace: str) -> None:
        with self._lock:
            self._path(namespace).unlink(missing_ok=True)

    def index(self, namespace: str, documents: Iterable[Document]) -> None:
        self.provision(namespace)
        now = time.time()
        with self._lock:
            conn = self._connect(namespace)
            try:
                for doc in documents:
                    if not doc.id or not doc.text:
                        continue

                    doc_text = doc.text.strip()
                    content_hash = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()

                    existing = conn.execute(
                        "SELECT id FROM documents WHERE content_hash = ? AND state = ?",
                        (content_hash, DocumentState.READY.value),
                    ).fetchone()

                    if existing:
                        logger.debug(
                            "Skipping duplicate document content for doc_id=%s (matches existing doc_id=%s)",
                            doc.id,
                            existing["id"],
                        )
                        continue
                    try:
                        conn.execute(
                            "DELETE FROM document_text WHERE document_id = ?", (doc.id,)
                        )
                        conn.execute(
                            "INSERT INTO document_text (document_id, body) VALUES (?, ?)",
                            (doc.id, doc_text),
                        )
                        conn.execute(
                            """
                            INSERT INTO documents
                                (id, title, uri, metadata, state, error, updated_at, content_hash)
                            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                title=excluded.title, uri=excluded.uri,
                                metadata=excluded.metadata, state=excluded.state,
                                error=NULL, updated_at=excluded.updated_at,
                                content_hash=excluded.content_hash
                            """,
                            (
                                doc.id,
                                doc.title or "",
                                doc.uri or "",
                                json.dumps(doc.metadata or {}),
                                DocumentState.READY.value,
                                now,
                                content_hash,
                            ),
                        )
                    except sqlite3.Error as exc:
                        logger.exception("failed to index document %s", doc.id)
                        conn.execute(
                            """
                            INSERT INTO documents (id, state, error, updated_at, content_hash)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                state=excluded.state, error=excluded.error,
                                updated_at=excluded.updated_at, content_hash=excluded.content_hash
                            """,
                            (doc.id, DocumentState.FAILED.value, str(exc)[:500], now, content_hash),
                        )
                conn.commit()
            finally:
                conn.close()

    def delete_document(self, namespace: str, document_id: str) -> None:
        with self._lock:
            try:
                conn = self._connect(namespace)
            except KeyError:
                return
            try:
                conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
                conn.execute(
                    "DELETE FROM document_text WHERE document_id = ?", (document_id,)
                )
                conn.commit()
            finally:
                conn.close()

    def search(
        self, namespace: str, query: str, *, limit: int = 5
    ) -> list[RetrievedChunk]:
        cleaned = self._to_match_query(query)
        if not cleaned:
            return []
        try:
            conn = self._connect(namespace)
        except KeyError:
            return []
        try:
            rows = conn.execute(
                """
                SELECT t.document_id AS document_id,
                       snippet(document_text, 1, '', '', ' … ', 40) AS excerpt,
                       bm25(document_text) AS rank,
                       COALESCE(d.title, '') AS title,
                       COALESCE(d.uri, '')   AS uri
                FROM document_text t
                LEFT JOIN documents d ON d.id = t.document_id
                WHERE document_text MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (cleaned, max(1, int(limit))),
            ).fetchall()
        except sqlite3.Error:
            logger.exception("retrieval search failed in namespace %s", namespace)
            return []
        finally:
            conn.close()

        # bm25() returns lower-is-better; expose higher-is-better instead.
        return [
            RetrievedChunk(
                document_id=row["document_id"],
                text=row["excerpt"] or "",
                score=-float(row["rank"]),
                title=row["title"] or "",
                uri=row["uri"] or "",
            )
            for row in rows
        ]

    @staticmethod
    def _to_match_query(query: str) -> str:
        """Turn user text into a safe full-text query.

        Each word is quoted, so punctuation and full-text operators in user
        input are treated as literal text rather than as syntax.
        """
        words = re.findall(r"\w+", query or "", flags=re.UNICODE)
        return " OR ".join(f'"{w}"' for w in words if len(w) > 1)

    def document_states(self, namespace: str) -> list[DocumentStatus]:
        try:
            conn = self._connect(namespace)
        except KeyError:
            return []
        try:
            rows = conn.execute(
                "SELECT id, state, error, updated_at FROM documents"
            ).fetchall()
        finally:
            conn.close()
        return [
            DocumentStatus(
                document_id=row["id"],
                state=DocumentState(row["state"]),
                error=row["error"],
                updated_at=row["updated_at"] or 0.0,
            )
            for row in rows
        ]
