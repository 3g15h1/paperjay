"""Search by meaning: passage embeddings kept in the database, searched in memory with numpy.

Vectors are stored as float32 blobs (the source of truth, and portable). For searching, all vectors of
one model are loaded once into a normalised float16 matrix and reused until the embeddings change, which
is tracked by a generation counter in the settings table. At 768 dimensions that is about 1.5 KB per
passage: ~75 MB for 10,000 documents of 5 passages each.
"""
import sqlite3
import threading

import numpy as np

from .db import get_setting, set_setting

_GEN_KEY = "embeddings_generation"


def bump(con: sqlite3.Connection) -> None:
    """Mark the embeddings as changed, so every Index reloads. Also used when documents enter or leave the trash."""
    set_setting(con, _GEN_KEY, str(int(get_setting(con, _GEN_KEY, "0")) + 1))


def put(con: sqlite3.Connection, document_id: int, model: str, passages: list[tuple[int, int, list[float]]]) -> None:
    """Replace one document's passages for `model`: (char_start, char_end, vector) each. Call inside a transaction."""
    con.execute("DELETE FROM embeddings WHERE document_id = ? AND model = ?", (document_id, model))
    con.executemany(
        "INSERT INTO embeddings(document_id, model, chunk, char_start, char_end, vector) VALUES (?, ?, ?, ?, ?, ?)",
        [(document_id, model, i, s, e, np.asarray(v, dtype=np.float32).tobytes())
         for i, (s, e, v) in enumerate(passages)])
    bump(con)


def delete(con: sqlite3.Connection, document_id: int) -> None:
    con.execute("DELETE FROM embeddings WHERE document_id = ?", (document_id,))
    bump(con)


def indexed_documents(con: sqlite3.Connection, model: str) -> set[int]:
    return {r[0] for r in con.execute("SELECT DISTINCT document_id FROM embeddings WHERE model = ?", (model,))}


class Index:
    """In-memory search over one model's vectors, reloaded only when they have changed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key = None
        self._docs = np.zeros(0, dtype=np.int64)
        self._spans = np.zeros((0, 2), dtype=np.int64)
        self._matrix = np.zeros((0, 0), dtype=np.float16)

    def _load(self, con: sqlite3.Connection, model: str) -> None:
        key = (model, get_setting(con, _GEN_KEY, "0"))
        if key == self._key:
            return
        rows = con.execute(
            "SELECT e.document_id, e.char_start, e.char_end, e.vector FROM embeddings e "
            "JOIN documents d ON d.id = e.document_id WHERE e.model = ? AND d.deleted_at IS NULL "
            "ORDER BY e.document_id, e.chunk", (model,)).fetchall()
        if rows:
            m = np.stack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
            m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
            self._matrix = m.astype(np.float16)
            self._docs = np.array([r[0] for r in rows], dtype=np.int64)
            self._spans = np.array([(r[1], r[2]) for r in rows], dtype=np.int64)
        else:
            self._matrix = np.zeros((0, 0), dtype=np.float16)
            self._docs = np.zeros(0, dtype=np.int64)
            self._spans = np.zeros((0, 2), dtype=np.int64)
        self._key = key

    def search(self, con: sqlite3.Connection, model: str, query_vector, k: int = 10) -> list[dict]:
        """Best-matching documents by cosine similarity, one entry per document with its best passage."""
        with self._lock:
            self._load(con, model)
            if not len(self._docs):
                return []
            q = np.asarray(query_vector, dtype=np.float32)
            if q.shape[0] != self._matrix.shape[1]:
                raise ValueError(f"query has {q.shape[0]} dimensions, the index has {self._matrix.shape[1]}")
            q /= max(float(np.linalg.norm(q)), 1e-12)
            # float16 storage, float32 maths, a block at a time so memory never doubles
            scores = np.concatenate([self._matrix[i:i + 8192].astype(np.float32) @ q
                                     for i in range(0, len(self._matrix), 8192)])
            docs, spans = self._docs, self._spans
        out, seen = [], set()
        for i in np.argsort(-scores):
            d = int(docs[i])
            if d in seen:
                continue
            seen.add(d)
            out.append({"document_id": d, "score": float(scores[i]),
                        "char_start": int(spans[i][0]), "char_end": int(spans[i][1])})
            if len(out) >= k:
                break
        return out
