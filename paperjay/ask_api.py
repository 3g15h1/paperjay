"""HTTP API for answers, AI settings, fact cards and "coming up".

    GET  /api/ask/stream?q=…            streamed answer (Server-Sent Events): sources, stage, token…, done
    POST /api/ask                       the same as JSON (same shape as Paperless Ask's, for scripts)
    GET  /api/upcoming                  due and expiry dates; `alerts` are the ones to notify about today
    GET  /calendar.ics                  the same as a calendar feed
    GET  /api/facts                     every fact card (to check what the AI read)
    POST /api/feedback, GET /api/feedback
    GET  /api/settings, PUT /api/settings, POST /api/settings/ai/test
    GET  /api/ai/status                 is the AI on, busy, and how far along is the background work
    POST /api/documents/{id}/analyze    file and read it again with the AI
    POST /api/documents/{id}/suggestions/accept   {"tag": "Utilities"} or {"correspondent": "…"}
    POST /api/documents/{id}/reviewed   clear "needs review"
"""
import asyncio
import json
import logging
import time
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import ai, analyze, ask, feedback, jobs, meaning, reminders, settings
from .api import Con, User, _doc_or_404, current_user
from .db import connect, transaction
from .library import Library

log = logging.getLogger("paperjay")
router = APIRouter()


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _prepare(db_path, q, prev_q, prev_a, doc, full, user_id=None):
    """Everything up to the model's answer, on a worker thread (it reads the database and may wait for
    search by meaning)."""
    con = connect(db_path)
    try:
        ask.INDEX.refresh(con)
        asker = ask.Asker(con, user_id=user_id)
        sem = ask.semantic_scores(con, asker.search_text(q, prev_q)) if doc is None else {}
        t0 = time.monotonic()
        r, mode, context = asker.prepare(q, prev_q, prev_a, doc, full, sem=sem)
        r["ms"] = int((time.monotonic() - t0) * 1000)
        return r, mode, context, ai.Config.load(con), len(ask.INDEX.docs)
    finally:
        con.close()


def _log(kind: str, q: str, r: dict, mode: str, ms: int) -> None:
    extra = "".join([f", follow-up of {r['context_q'][:80]!r}" if r["followup"] else "",
                     f", about #{r['scoped']}" if r["scoped"] is not None else ""])
    log.info("%s %r -> %s, %d sources, %d ms%s", kind, q[:120], mode, len(r["sources"]), ms, extra)


@router.get("/api/ask/stream")
async def ask_stream(request: Request, q: str = Query(..., min_length=1, max_length=500), full: bool = False,
                     prev_q: str | None = Query(None, max_length=1000), prev_a: str | None = Query(None, max_length=2000),
                     doc: int | None = None):
    q = " ".join(q.split())
    ai.note_question()
    db_path = request.app.state.config.db_path

    async def events():
        t0 = time.monotonic()
        try:
            r, mode, context, cfg, n_docs = await asyncio.to_thread(
                _prepare, db_path, q, prev_q, prev_a, doc, full, current_user(request))
        except Exception:
            log.exception("retrieval failed")
            yield sse("fatal", {"message": "Something went wrong while searching. Please try again."})
            return
        _log("ask", q, r, mode or ("llm" if r["llm_docs"] else "list" if r["listing"] else "none"), r["ms"])
        yield sse("sources", {**ask.public(r), "library": n_docs})
        if mode or not r["llm_docs"]:
            yield sse("done", {"mode": mode or ("list" if r["listing"] else "none"),
                               "ms": int((time.monotonic() - t0) * 1000)})
            return
        if not cfg.on:
            yield sse("done", {"mode": "extractive", "ai": "off", "ms": int((time.monotonic() - t0) * 1000)})
            return
        yield sse("stage", {"stage": "writing", "busy": ai.busy()})
        got, text = False, ""
        try:
            ai.note_question()
            async for tok in ai.chat_stream(cfg, ask.messages(q, r["llm_docs"], r["missing"], context,
                                                              r["scoped"] is not None)):
                got = True
                text += tok
                yield sse("token", {"t": tok})
            if not ask.has_words(text):
                raise RuntimeError(f"no usable answer: {text!r}")
            v = ask.verify_answer(text, r["llm_docs"])
            if v["unverified"]:
                log.info("unverified numbers in answer: %s", v["unverified"])
            yield sse("done", {"mode": "llm", "model": cfg.model, **v, "ms": int((time.monotonic() - t0) * 1000)})
        except Exception as e:
            log.warning("AI answer failed (%s): %s", type(e).__name__, e)
            yield sse("llm_error", {"partial": got,
                                    "message": "The AI didn't answer, so here are the best matching passages."})
            yield sse("done", {"mode": "extractive", "ms": int((time.monotonic() - t0) * 1000)})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/ask")
async def ask_json(request: Request, payload: dict):
    """{"q": "...", "no_llm": false, "full": false, "prev": {"q": "...", "a": "..."}, "doc": 123, "debug": false}"""
    q = " ".join(str(payload.get("q", "")).split())[:500]
    if not q:
        return JSONResponse({"error": "missing q"}, status_code=400)
    prev = payload.get("prev") if isinstance(payload.get("prev"), dict) else {}
    doc = payload.get("doc") if isinstance(payload.get("doc"), int) else None
    t0 = time.monotonic()
    ai.note_question()
    r, mode, context, cfg, _ = await asyncio.to_thread(
        _prepare, request.app.state.config.db_path, q, str(prev.get("q") or "")[:1000] or None,
        str(prev.get("a") or "")[:2000], doc, bool(payload.get("full")), current_user(request))
    scoped = r["scoped"] is not None
    out = ask.public(r)
    out["mode"], out["answer"], out["unverified"] = mode or ("list" if r["listing"] else "none"), \
        r["extractive"]["text"], []
    if payload.get("debug") and r["llm_docs"]:
        out["prompt"] = ask.build_prompt(q, r["llm_docs"], r["missing"], context, scoped)
    if r["llm_docs"] and not mode:
        out["mode"] = "extractive"
        if not payload.get("no_llm") and cfg.on:
            try:
                text = "".join([t async for t in ai.chat_stream(cfg, ask.messages(q, r["llm_docs"], r["missing"],
                                                                                  context, scoped))])
                if not ask.has_words(text):
                    raise RuntimeError(f"no usable answer: {text!r}")
                out.update(ask.verify_answer(text, r["llm_docs"]))
                out["raw_answer"] = text
                out["mode"] = "llm"
            except Exception as e:
                log.warning("AI answer failed (%s): %s", type(e).__name__, e)
    out["total_ms"] = int((time.monotonic() - t0) * 1000)
    _log("ask(json)", q, r, out["mode"], out["total_ms"])
    return out


# ---- coming up ----

def _upcoming(con, days: int, link) -> dict:
    ask.INDEX.refresh(con)
    s = settings.all_settings(con)
    return reminders.upcoming(ask.INDEX.docs.values(), ask.INDEX.cards, date.today(), days, s["done_tags"], link,
                              s["alert_days"])


@router.get("/api/upcoming")
def upcoming(request: Request, con: Con, days: int | None = Query(None, ge=0, le=3660)):
    """Full links, so Home Assistant notifications and cards can open the document."""
    base = str(request.base_url).rstrip("/")
    return _upcoming(con, days if days is not None else settings.get(con, "reminder_days"),
                     lambda i: f"{base}/documents/{i}")


@router.get("/calendar.ics")
def calendar(request: Request, con: Con):
    base = str(request.base_url).rstrip("/")
    up = _upcoming(con, 365, lambda i: f"{base}/documents/{i}")
    body = reminders.ics(up["items"], settings.get(con, "alert_days"), host=request.url.hostname or "paperjay")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="paperjay-coming-up.ics"'})


@router.get("/api/facts")
def facts_list(con: Con):
    ask.INDEX.refresh(con)
    docs = ask.INDEX.docs
    return {"cards": [{"id": i, "title": docs[i].title, "url": f"/documents/{i}", **c}
                      for i, c in sorted(ask.INDEX.cards.items())]}


# ---- feedback ----

@router.post("/api/feedback")
def feedback_add(con: Con, user: User, payload: dict):
    try:
        entry = feedback.clean(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    feedback.save(con, entry, user)
    log.info("feedback %s on %r%s", entry["vote"], entry["q"][:120], " (with comment)" if entry.get("comment") else "")
    return {"ok": True}


@router.get("/api/feedback")
def feedback_list(con: Con):
    items = feedback.entries(con)
    return {"up": sum(e["vote"] == "up" for e in items), "down": sum(e["vote"] == "down" for e in items),
            "total": len(items), "items": items[:500]}


# ---- settings and AI ----

@router.get("/api/settings")
def settings_get(con: Con):
    return settings.all_settings(con)


@router.put("/api/settings")
def settings_put(request: Request, con: Con, payload: dict):
    before = ai.Config.load(con)
    try:
        out = settings.save(con, payload)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    after = ai.Config.load(con)
    if (after.on and not before.on) or after.model != before.model or after.embed_key != before.embed_key:
        n = queue_missing(con)
        request.app.state.paperjay["ai_worker"].wake()
        log.info("AI settings changed: queued %d document(s) for AI work", n)
    return out


@router.post("/api/settings/ai/test")
def settings_test(con: Con, payload: dict | None = None):
    """Test the saved AI settings, or the ones in the body (to try them before saving)."""
    cfg = ai.Config.load(con)
    if payload:
        merged = {**settings.all_settings(con, hide_secrets=False),
                  **{k: v for k, v in payload.items() if not (k in settings.SECRET and v == "••••••")}}
        try:
            for k in ("ai_provider", "ai_url", "ai_model", "embed_model", "ai_api_key", "ai_threads", "embed_source",
                      "ai_no_think"):
                settings._validate(k, merged[k])
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        cfg = ai.Config.from_settings(merged)
    return ai.test(cfg)


@router.get("/api/ocr/languages")
def ocr_languages():
    from .ocr import tesseract_languages
    return sorted(tesseract_languages() - {"osd"})


@router.get("/api/ai/status")
def ai_status(con: Con):
    cfg = ai.Config.load(con)
    total = con.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL AND status = 'ready' "
                        "AND length(content) > 0").fetchone()[0]
    cards = len(analyze.current_cards(con))
    embedded = len(meaning.indexed(con, cfg.embed_key)) if cfg.meaning_on else 0
    waiting = {r[0]: r[1] for r in con.execute("SELECT kind, COUNT(*) FROM jobs WHERE status IN ('queued', 'running') "
                                               "GROUP BY kind")}
    return {"on": cfg.on, "provider": cfg.provider, "model": cfg.model if cfg.on else None,
            "embed_model": (cfg.embed_model if cfg.embed_source == "ai" else "built-in") if cfg.meaning_on else None,
            "meaning": cfg.meaning_on, "busy": ai.busy(),
            "documents": total, "analyzed": cards, "embedded": embedded, "queued": waiting}


@router.post("/api/ai/reread-all")
def reread_all(request: Request, con: Con, user: User, payload: dict | None = None):
    """Admin: re-read every document with the AI (spec B.4). Without {"confirm": true} this
    only reports the estimate — a full re-read is ~40 s per document — and queues nothing."""
    row = con.execute("SELECT role FROM users WHERE id = ?", (user,)).fetchone() if user else None
    if not row or row["role"] != "admin":
        raise HTTPException(403, "only an admin can re-read everything")
    ids = [r[0] for r in con.execute("SELECT id FROM documents WHERE deleted_at IS NULL "
                                      "AND status = 'ready' AND length(content) > 0 ORDER BY id")]
    estimate = len(ids) * 40
    out = {"documents": len(ids), "estimate_seconds": estimate,
           "estimate": f"about {max(1, round(estimate / 60))} minutes at ~40 s per document",
           "queued": 0}
    if not (payload or {}).get("confirm"):
        return out
    cfg = ai.Config.load(con)
    if not cfg.on:
        raise HTTPException(400, "the AI is off")
    with transaction(con):
        for doc_id in ids:
            jobs.enqueue(con, analyze.ANALYZE_JOB, document_id=doc_id, priority=130,
                         payload={"force": True})
    request.app.state.paperjay["ai_worker"].wake()
    out["queued"] = len(ids)
    return out


@router.post("/api/documents/{doc_id}/analyze")
def reanalyze(request: Request, con: Con, doc_id: int):
    _doc_or_404(Library(con), doc_id)
    with transaction(con):
        job = jobs.enqueue(con, analyze.ANALYZE_JOB, document_id=doc_id, priority=90, payload={"force": True})
    request.app.state.paperjay["ai_worker"].wake()
    return {"job_id": job}


@router.post("/api/documents/{doc_id}/suggestions/accept")
def accept_suggestion(con: Con, user: User, doc_id: int, payload: dict):
    lib = Library(con)
    doc = _doc_or_404(lib, doc_id)
    sug = dict(doc["suggestions"])
    if payload.get("tag"):
        name = str(payload["tag"])
        tag_id, _ = lib.get_or_create_label("tag", name, user_id=user)
        lib.update(doc_id, user_id=user, tags=[t["id"] for t in doc["tags"]] + [tag_id])
        sug["new_tags"] = [t for t in sug.get("new_tags", []) if t.lower() != name.lower()]
        sug["review"] = [r for r in sug.get("review", []) if name.lower() not in r.lower()]
    elif payload.get("correspondent"):
        cid, _ = lib.get_or_create_label("correspondent", str(payload["correspondent"]), user_id=user)
        lib.update(doc_id, user_id=user, correspondent_id=cid)
        sug.pop("correspondent", None)
    # Tidy up (spec E): the other suggested values are a person's one-tap filing too
    elif payload.get("document_type"):
        tid, _ = lib.get_or_create_label("document_type", str(payload["document_type"]), user_id=user)
        lib.update(doc_id, user_id=user, document_type_id=tid)
        sug.pop("document_type", None)
    elif payload.get("doc_date"):
        try:
            lib.update(doc_id, user_id=user, doc_date=str(payload["doc_date"]))
        except ValueError as e:
            raise HTTPException(400, str(e))
        sug.pop("doc_date", None)
    elif payload.get("title"):
        try:
            lib.update(doc_id, user_id=user, title=str(payload["title"]))
        except ValueError as e:
            raise HTTPException(400, str(e))
        sug.pop("title", None)
    else:
        raise HTTPException(400, "send one of tag, correspondent, document_type, doc_date or title")
    lib.update(doc_id, user_id=user, by="system", suggestions=sug)
    return lib.get(doc_id)


@router.post("/api/documents/{doc_id}/reviewed")
def mark_reviewed(con: Con, user: User, doc_id: int):
    lib = Library(con)
    doc = _doc_or_404(lib, doc_id)
    sug = doc["suggestions"]
    # what was looked at stays reviewed: a later refile only flags the document for new reasons
    reviewed = sorted(set(sug.get("reviewed") or []) | set(sug.get("review") or []))
    lib.update(doc_id, user_id=user, needs_review=False, suggestions={**sug, "review": [], "reviewed": reviewed})
    return lib.get(doc_id)


# ---- background AI work ----

def queue_missing(con) -> int:
    """Queue AI work for every ready document that lacks a current fact card or embedding.
    Documents that already have a usable card are only re-read selectively (spec B.4: a
    conflict, a low-evidence field, no company or kind, or no topics), as bulk work: lowest
    priority (after new documents) and only inside `ai_batch_hours`, because the AI server
    is shared. The single-threaded AI worker already runs them one at a time."""
    cfg = ai.Config.load(con)
    if not cfg.on and not cfg.meaning_on:
        return 0
    rows = con.execute("SELECT id, content FROM documents WHERE deleted_at IS NULL AND status = 'ready' "
                       "AND length(content) > 0").fetchall()
    have = analyze.current_cards(con)
    embedded = meaning.indexed(con, cfg.embed_key) if cfg.meaning_on else set()
    profiles = None
    in_window = analyze.in_batch_window(con)
    versions = {r[0]: r[1] for r in con.execute("SELECT document_id, version FROM facts WHERE source = 'ai'")}
    # a document whose AI read failed recently (e.g. the reply wasn't valid JSON) waits a week
    failed = {r[0] for r in con.execute(
        "SELECT DISTINCT document_id FROM jobs WHERE kind = ? AND status = 'failed' AND finished_at > ?",
        (analyze.ANALYZE_JOB, time.time() - 7 * 86400))}
    n = 0
    with transaction(con):
        for r in rows:
            if not cfg.on:
                pass
            elif r["id"] not in have:
                jobs.enqueue(con, analyze.ANALYZE_JOB, document_id=r["id"], priority=110)
                n += 1
            elif in_window:
                if profiles is None:
                    from . import profile as profile_mod
                    profiles = profile_mod.current_profiles(con)
                prof = profiles.get(r["id"])
                if prof and r["id"] not in failed and analyze.needs_reread(prof, versions.get(r["id"])):
                    jobs.enqueue(con, analyze.ANALYZE_JOB, document_id=r["id"], priority=130,
                                 payload={"reread": True})
                    n += 1
            if cfg.meaning_on and r["id"] not in embedded:
                jobs.enqueue(con, meaning.EMBED_JOB, document_id=r["id"], priority=120)
                n += 1
    return n


def after_consume(con, doc_id: int) -> None:
    """Queued when OCR finishes: the profile job always runs (rules file the document even with the
    AI off, spec A.4); the AI jobs queue only when the AI is on."""
    cfg = ai.Config.load(con)
    with transaction(con):
        jobs.enqueue(con, "profile", document_id=doc_id, priority=90)
        if cfg.on:
            jobs.enqueue(con, analyze.ANALYZE_JOB, document_id=doc_id, priority=100, payload={"force": True})
        if cfg.meaning_on:
            jobs.enqueue(con, meaning.EMBED_JOB, document_id=doc_id, priority=120)
