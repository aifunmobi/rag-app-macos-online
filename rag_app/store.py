"""SQLite-backed store for documents, chunks, conversations, and messages."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import numpy as np

from rag_app.config import DB_PATH, TOP_K


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id          INTEGER PRIMARY KEY,
                    path        TEXT UNIQUE NOT NULL,
                    content_hash TEXT NOT NULL,
                    indexed_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id          INTEGER PRIMARY KEY,
                    doc_id      INTEGER NOT NULL
                                    REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    text        TEXT NOT NULL,
                    embedding   BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id          INTEGER PRIMARY KEY,
                    title       TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY,
                    conversation_id INTEGER NOT NULL
                                        REFERENCES conversations(id) ON DELETE CASCADE,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    sources         TEXT,
                    created_at      TEXT NOT NULL
                );
            """)

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def get_doc_hash(self, path: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT content_hash FROM documents WHERE path = ?", (path,)
            ).fetchone()
        return row["content_hash"] if row else None

    def upsert_document(self, path: str, content_hash: str) -> int:
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE path = ?", (path,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE documents SET content_hash = ?, indexed_at = ? WHERE id = ?",
                    (content_hash, _now(), existing["id"]),
                )
                return existing["id"]
            cur = conn.execute(
                "INSERT INTO documents (path, content_hash, indexed_at) VALUES (?, ?, ?)",
                (path, content_hash, _now()),
            )
            return cur.lastrowid

    def replace_chunks(
        self, doc_id: int, chunks: list[tuple[int, str, list[float]]]
    ) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO chunks (doc_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
                [
                    (
                        doc_id,
                        chunk_index,
                        text,
                        np.asarray(embedding, dtype=np.float32).tobytes(),
                    )
                    for chunk_index, text, embedding in chunks
                ],
            )

    def delete_document(self, path: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM documents WHERE path = ?", (path,))

    def list_documents(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT d.path, d.content_hash, d.indexed_at,
                       COUNT(c.id) AS n_chunks
                FROM documents d
                LEFT JOIN chunks c ON c.doc_id = d.id
                GROUP BY d.id
                ORDER BY d.indexed_at DESC
            """).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def query(
        self, query_embedding: list[float], top_k: int = TOP_K
    ) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT c.chunk_index, c.text, c.embedding, d.path AS doc_path
                FROM chunks c
                JOIN documents d ON d.id = c.doc_id
            """).fetchall()

        if not rows:
            return []

        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            q_hat = q
        else:
            q_hat = q / q_norm

        embeddings = np.stack(
            [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        )
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        # Avoid divide-by-zero for zero-norm chunk embeddings
        safe_norms = np.where(norms == 0, 1.0, norms)
        normed = embeddings / safe_norms
        scores = normed @ q_hat

        # Sort descending
        order = np.argsort(scores)[::-1]
        top = order[:top_k]

        return [
            {
                "doc_path": rows[i]["doc_path"],
                "chunk_index": rows[i]["chunk_index"],
                "text": rows[i]["text"],
                "score": float(scores[i]),
            }
            for i in top
        ]

    def stats(self) -> dict:
        with self._conn() as conn:
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": docs, "chunks": chunks}

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def create_conversation(self, title: str = "New chat") -> int:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, now, now),
            )
            return cur.lastrowid

    def list_conversations(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_messages(self, conv_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, sources, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY created_at ASC",
                (conv_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["sources"] = json.loads(d["sources"]) if d["sources"] else []
            result.append(d)
        return result

    def add_message(
        self,
        conv_id: int,
        role: str,
        content: str,
        sources=None,
    ) -> int:
        now = _now()
        sources_json = json.dumps(sources) if sources is not None else json.dumps([])
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, sources, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, role, content, sources_json, now),
            )
            msg_id = cur.lastrowid
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id),
            )
        return msg_id

    def rename_conversation(self, conv_id: int, title: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, conv_id),
            )

    def delete_conversation(self, conv_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
