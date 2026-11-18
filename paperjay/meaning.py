"""Search by meaning: the 'embed' job and scoring questions against the stored passages.

A document's text is flattened (whitespace collapsed) and cut into ~600-character passages that overlap
a little; their character spans refer to that flattened text. A model change re-embeds everything.
"""
import logging
import sqlite3

import httpx

from . import ai, embedder, jobs, vectors
from .db import transaction
from .library import Library, NotFound

log = logging.getLogger("paperjay")

EMBED_JOB = "embed"
MAX_PASSAGES = 40
BATCH = 16
INDEX = vectors.Index()


def passages(content: str, size: int = 600, max_chunks: int = MAX_PASSAGES) -> list[tuple[int, int, str]]:
    """(start, end, text) passages on whitespace boundaries, overlapping by ~80 characters."""
    flat = " ".join(content.split())
    out, start = [], 0
    while start < len(flat) and len(out) < max_chunks:
        end = min(len(flat), start + size)
        if end < len(flat):
            cut = flat.rfind(" ", start + size // 2, end)
            end = cut if cut > 0 else end
        out.append((start, end, flat[start:end]))
        start = max(end - 80, start + 1) if end < len(flat) else end
    return out


def indexed(con: sqlite3.Connection, model: str) -> set[int]:
    return vectors.indexed_documents(con, model)


def make_embed() -> jobs.Handler:
    def embed(con: sqlite3.Connection, job: dict) -> None:
        cfg = ai.Config.load(con)
        if not cfg.meaning_on:
            return
        if ai.people_waiting():
            raise jobs.Defer(60)
        try:
            doc = Library(con).get(job["document_id"])
        except NotFound:
            return
        if doc["deleted_at"] or doc["status"] != "ready":
            return
        parts = passages(doc["content"] or "")
        vecs = []
        try:
            for i in range(0, len(parts), BATCH):
                vecs.extend(ai.embed(cfg, [p[2] for p in parts[i:i + BATCH]], wait=5))
        except TimeoutError:
            raise jobs.Defer(30)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise jobs.Defer(600, f"AI server unreachable: {e}")
        except (ai.ModelMissing, embedder.Unavailable) as e:
            raise jobs.Defer(3600, str(e))
        with transaction(con):
            vectors.put(con, doc["id"], cfg.embed_key, [(s, e, v) for (s, e, _), v in zip(parts, vecs)])

    return embed


def scores(con: sqlite3.Connection, question: str) -> dict[int, float]:
    """Best-passage similarity per document; {} when search by meaning is off or the AI can't be reached
    quickly (a question never waits long for it)."""
    cfg = ai.Config.load(con)
    if not cfg.meaning_on:
        return {}
    try:
        q = ai.embed(cfg, [question], timeout=5, wait=3, kind="query")[0]
        hits = INDEX.search(con, cfg.embed_key, q, k=50)
    except Exception as e:
        log.info("search by meaning skipped: %s", e)
        return {}
    return {h["document_id"]: h["score"] for h in hits}
