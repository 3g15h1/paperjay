"""Thumbs up/down on answers, kept in the feedback table. A later vote or comment with the same id
(the answer's id, made by the browser) updates the earlier one."""
import json
import sqlite3

from .db import transaction

LIMITS = {"id": 64, "q": 500, "context_q": 1000, "comment": 1000, "answer": 2000, "mode": 20}


def clean(payload: dict) -> dict:
    """Keep known fields, capped in size; raises ValueError for a bad vote."""
    vote = payload.get("vote")
    if vote not in ("up", "down"):
        raise ValueError("vote must be 'up' or 'down'")
    out = {"vote": vote}
    for k, n in LIMITS.items():
        v = payload.get(k)
        if v not in (None, ""):
            out[k] = str(v)[:n]
    if not out.get("id") or not out.get("q"):
        raise ValueError("id and q are required")
    doc = payload.get("doc")
    if isinstance(doc, int):
        out["doc"] = doc
    out["sources"] = [{"id": int(s.get("id")), "title": str(s.get("title", ""))[:200]}
                      for s in (payload.get("sources") or [])[:10]
                      if isinstance(s, dict) and str(s.get("id", "")).isdigit()]
    out["missing"] = [str(w)[:40] for w in (payload.get("missing") or [])[:10]]
    return out


def save(con: sqlite3.Connection, e: dict, user_id: int | None = None) -> None:
    with transaction(con):
        con.execute(
            "INSERT INTO feedback(id, user_id, vote, question, context_q, answer, mode, document_id, sources, missing, "
            "comment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET vote = excluded.vote, "
            "comment = COALESCE(excluded.comment, feedback.comment), answer = COALESCE(excluded.answer, feedback.answer)",
            (e["id"], user_id, e["vote"], e["q"], e.get("context_q"), e.get("answer"), e.get("mode"), e.get("doc"),
             json.dumps(e["sources"]), json.dumps(e["missing"]), e.get("comment")))


def entries(con: sqlite3.Connection) -> list[dict]:
    """Answers marked wrong first, newest first."""
    out = []
    for r in con.execute("SELECT * FROM feedback ORDER BY vote = 'up', created_at DESC"):
        d = dict(r)
        d["sources"], d["missing"] = json.loads(d["sources"]), json.loads(d["missing"])
        out.append(d)
    return out
