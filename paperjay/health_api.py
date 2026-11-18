"""HTTP API for filing health (spec Phase E): the Tidy up lists and the one-tap fixes.

    GET  /api/health/filing                  {counts: {list: n}, documents: n}
    GET  /api/health/filing?list=no_date     … plus {list, items: [...]} for that list
    POST /api/documents/{id}/confirm-filing  a person vouches for imported filing (B.1)
    POST /api/documents/{id}/confirm-fields  {fields?: [...]} single-source values are right
    POST /api/documents/{id}/not-a-copy      the copy flag was wrong; never pair them again
    POST /api/documents/{id}/refile          re-run rules + library for one document now
    POST /api/refile                         admin: re-file everything ({confirm, ai})
"""
from fastapi import APIRouter, HTTPException, Query, Request

from . import ai, analyze, health, jobs, profile
from .api import Con, User, _doc_or_404
from .db import transaction
from .library import Library

router = APIRouter()

# Tidy up speaks in profile fields; the document stores columns
_COLUMNS = {"vendor": "correspondent_id", "kind": "document_type_id", "doc_date": "doc_date"}
AI_SECONDS_PER_DOC = 40  # the same planning figure as the AI re-read estimate (B.4)


@router.get("/api/health/filing")
def filing_health(con: Con, user: User, list: str | None = None,
                  limit: int = Query(100, ge=1, le=500)):
    out = health.counts(con)
    if list:
        try:
            out["list"], out["items"] = list, health.items(con, list, limit=limit)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return out


@router.post("/api/documents/{doc_id}/confirm-filing")
def confirm_filing(con: Con, user: User, doc_id: int):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    lib.confirm_filing(doc_id, user_id=user)
    return lib.get(doc_id)


def confirm_low_evidence(con, doc_id: int, fields: list[str] | None, user_id) -> dict:
    """Confirm a document's single-source fields (all of the listed ones by default) and take
    them off its low-evidence list."""
    lib = Library(con)
    doc = lib.get(doc_id)
    row = {"correspondent_id": doc["correspondent_id"], "document_type_id": doc["document_type_id"],
           "doc_date": doc["doc_date"], "suggestions": doc["suggestions"]}
    wanted = fields if fields is not None else health.low_fields(row)
    unknown = set(wanted) - set(_COLUMNS)
    if unknown:
        raise ValueError(f"fields are {', '.join(_COLUMNS)}")
    columns = [_COLUMNS[f] for f in wanted if doc[_COLUMNS[f]] is not None]
    if not columns:
        raise ValueError("nothing filed to confirm")
    lib.confirm_fields(doc_id, columns, user_id=user_id)
    sug = dict(lib.get(doc_id)["suggestions"])
    sug["low_evidence"] = [f for f in sug.get("low_evidence") or [] if f not in wanted]
    return lib.update(doc_id, by="system", suggestions=sug)


@router.post("/api/documents/{doc_id}/confirm-fields")
def confirm_fields(con: Con, user: User, doc_id: int, payload: dict | None = None):
    _doc_or_404(Library(con), doc_id)
    fields = (payload or {}).get("fields")
    try:
        return confirm_low_evidence(con, doc_id, fields, user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/documents/{doc_id}/not-a-copy")
def not_a_copy(con: Con, user: User, doc_id: int):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    try:
        return lib.not_a_copy(doc_id, user_id=user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/documents/{doc_id}/refile")
def refile_one(con: Con, user: User, doc_id: int):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    changes = profile.refile(con, doc_id)
    return {"document": lib.get(doc_id), "changed": sorted((changes or {}).keys())}


@router.post("/api/refile")
def refile_all(request: Request, con: Con, user: User, payload: dict | None = None):
    """Admin (spec E): re-run the rules and the library layer on every document, and with
    {"ai": true} also queue AI re-reads. Without {"confirm": true} it only reports what it
    would do — the AI part takes ~40 s per document on a home computer."""
    row = con.execute("SELECT role FROM users WHERE id = ?", (user,)).fetchone() if user else None
    if not row or row["role"] != "admin":
        raise HTTPException(403, "only an admin can re-file everything")
    payload = payload or {}
    ids = [r[0] for r in con.execute("SELECT id FROM documents WHERE deleted_at IS NULL "
                                      "AND status = 'ready' AND length(content) > 0 ORDER BY id")]
    ai_on = ai.Config.load(con).on
    seconds = len(ids) * AI_SECONDS_PER_DOC
    out = {"documents": len(ids), "ai_on": ai_on, "ai_estimate_seconds": seconds if ai_on else 0,
           "ai_estimate": f"about {max(1, round(seconds / 60))} minutes" if ai_on else None,
           "queued": 0, "queued_ai": 0}
    if not payload.get("confirm"):
        return out
    with transaction(con):
        for doc_id in ids:  # the rules + library pass: cheap, ahead of everything but new uploads
            jobs.enqueue(con, "profile", document_id=doc_id, priority=80)
        if ai_on and payload.get("ai"):
            for doc_id in ids:
                jobs.enqueue(con, analyze.ANALYZE_JOB, document_id=doc_id, priority=130,
                             payload={"force": True})
            out["queued_ai"] = len(ids)
    out["queued"] = len(ids)
    state = request.app.state.paperjay
    state["worker"].wake()
    if out["queued_ai"]:
        state["ai_worker"].wake()
    return out
