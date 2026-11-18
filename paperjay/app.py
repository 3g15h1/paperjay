"""The web app.

Startup: migrate the database (backing it up first), clear abandoned uploads, re-queue documents a crash
left unprocessed, start the job worker and the consume folder.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import threading

from . import __version__, analyze, api, ask_api, auth_api, db, health_api, https_api, index_api, indexing, intake, jobs, library_api, mail, mail_api, meaning, neighbours, ocr, paper_api, papers, processing, profile, scan_api, transfer_api, ui, updates
from .config import Config
from .consume import ConsumeFolder
from .library import Library
from .processing import make_consume
from .store import FileStore

log = logging.getLogger("paperjay")


def create_app(config: Config | None = None, start_worker: bool = True) -> FastAPI:
    config = config or Config.from_env()
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        version = db.migrate(config.db_path, config.backup_dir)
        store = FileStore(config.media_dir)
        removed = store.clean_tmp()
        if removed:
            log.info("removed %d abandoned upload(s)", removed)
        missing = ocr.missing_tools()
        if missing:
            log.error("OCR can't run, these programs are missing: %s", ", ".join(missing))
        con = db.connect(config.db_path)
        try:
            n = intake.requeue_unfinished(con)
            if n:
                log.info("queued %d document(s) that were never processed", n)
            released = Library(con).release_import_filing()
            if released:
                log.info("released import filing on %d document(s) (file-name titles / scan dates)", released)
            numbered = papers.backfill_asn_notes(con)
            if numbered:
                log.info("gave %d document(s) their paper number from an imported 'Archive serial number' note", numbered)
            stale = [r[0] for r in con.execute(
                "SELECT d.id FROM documents d WHERE d.deleted_at IS NULL AND d.status = 'ready' "
                "AND d.content != '' AND (NOT EXISTS (SELECT 1 FROM facts f WHERE f.document_id = d.id "
                "AND f.source = 'rules' AND f.version >= ?) OR NOT EXISTS (SELECT 1 FROM facts f "
                "WHERE f.document_id = d.id AND f.source = 'library' AND f.version >= ?) "
                "OR d.simhash IS NULL)",
                (profile.RULES_VERSION, neighbours.NEIGHBOURS_VERSION))]
            if stale:
                with db.transaction(con):
                    for doc_id in stale:
                        jobs.enqueue(con, "profile", document_id=doc_id, priority=90)
                log.info("queued profile jobs for %d document(s) with stale rule cards", len(stale))
            untermed = [r[0] for r in con.execute(
                "SELECT d.id FROM documents d WHERE d.deleted_at IS NULL AND d.status = 'ready' "
                "AND d.content != '' AND NOT EXISTS (SELECT 1 FROM index_terms it "
                "WHERE it.document_id = d.id)")]
            missing_terms = [i for i in untermed if i not in set(stale)]
            if missing_terms:
                # No refile for these (review 0.4.0 #4): a document with nothing to
                # index — no company, number, person or topic — would qualify for a
                # queued profile job on every restart, forever. Rebuilding its terms
                # directly is cheaper and never touches its filing; a document that
                # does have terms simply gets them here.
                for doc_id in missing_terms:
                    indexing.reindex_document(con, doc_id)
                log.info("rebuilt A–Z index terms for %d document(s) with none",
                         len(missing_terms))
        finally:
            con.close()
        worker = jobs.Worker(config.db_path, {intake.CONSUME_JOB: make_consume(config, store),
                                              "profile": profile.make_profile()},
                             threads=config.workers)
        # AI work runs on its own thread, so a long AI call never holds up OCR of new uploads
        ai_worker = jobs.Worker(config.db_path, {analyze.ANALYZE_JOB: analyze.make_analyze(),
                                                 meaning.EMBED_JOB: meaning.make_embed()}, threads=1)
        if ask_api.after_consume not in processing.AFTER_CONSUME:
            processing.AFTER_CONSUME.append(ask_api.after_consume)
        consume = None
        if config.consume_dir:
            consume = ConsumeFolder(config.consume_dir, config.db_path, store, config.max_upload_bytes,
                                    on_added=worker.wake)
        poller = mail.Poller(config.db_path, store, config.max_upload_bytes, on_added=worker.wake)
        stop = threading.Event()
        upkeep = threading.Thread(target=_upkeep, args=(config, store, stop, ai_worker, app.state.https), name="paperjay-upkeep",
                                  daemon=True)
        if start_worker:
            worker.start()
            ai_worker.start()
            upkeep.start()
            poller.start()
            if consume:
                consume.start()
        state.update(store=store, worker=worker, ai_worker=ai_worker, consume=consume, mail=poller, missing_tools=missing)
        log.info("PaperJay %s ready: schema v%d, data in %s", __version__, version, config.data_dir)
        try:
            yield
        finally:
            stop.set()
            poller.stop()
            if consume and start_worker:
                consume.stop()
            worker.stop()
            ai_worker.stop()

    app = FastAPI(title="PaperJay", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.config = config
    from .https import Https
    app.state.https = Https(config.data_dir, config.https_port, config.https_names) if config.https_port else None
    app.state.paperjay = state

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")          # no clickjacking from other sites
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=()")
        return response

    @app.middleware("http")
    async def limit_upload_size(request: Request, call_next):
        # Refuse oversized uploads before they are received; chunked uploads are checked while being stored
        length = request.headers.get("content-length")
        if request.method == "POST" and length and length.isdigit() \
                and int(length) > config.max_upload_bytes + 1024 * 1024:
            return JSONResponse({"detail": f"the upload is larger than {config.max_upload_mb} MB"}, status_code=413)
        return await call_next(request)

    @app.get("/healthz")
    def healthz():
        con = db.connect(config.db_path)
        try:
            con.execute("SELECT 1").fetchone()
            missing = state.get("missing_tools", [])
            return {"ok": True, "version": __version__, "schema": db.schema_version(con),
                    "ocr": {"ok": not missing, "missing": missing}}
        finally:
            con.close()

    @app.get("/api/stats")
    def stats():
        con = db.connect(config.db_path)
        try:
            return {"version": __version__, "documents": Library(con).counts(), "jobs": jobs.counts(con)}
        finally:
            con.close()

    auth_api.install(app)
    app.include_router(auth_api.router)
    app.include_router(api.router)
    app.include_router(ask_api.router)
    app.include_router(library_api.router)
    app.include_router(paper_api.router)
    app.include_router(health_api.router)
    app.include_router(index_api.router)
    app.include_router(transfer_api.router)
    app.include_router(scan_api.router)
    app.include_router(mail_api.router)
    app.include_router(https_api.router)
    app.include_router(ui.router)
    app.mount("/static", ui.Static(directory=ui.STATIC), name="static")
    return app


def _upkeep(config: Config, store: FileStore, stop: threading.Event, ai_worker: jobs.Worker, tls=None,
            every: float = 900) -> None:
    """Every 15 minutes: queue AI work that's missing (e.g. after the AI was down), tidy the job list,
    empty the trash of documents deleted more than 30 days ago, see whether a new PaperJay is out, and renew the HTTPS certificate when it's
    getting old or this machine's address changed."""
    first = True
    while not stop.wait(5 if first else every):
        first = False
        if tls:
            try:
                tls.ensure()
            except Exception:
                log.exception("HTTPS certificate renewal failed")
        con = db.connect(config.db_path)
        try:
            if ask_api.queue_missing(con):
                ai_worker.wake()
            with db.transaction(con):
                jobs.prune(con)
            lib = Library(con)
            for doc_id in lib.expired_trash(30):
                checksum, ext = lib.purge(doc_id)
                store.remove(checksum, ext)
                log.info("emptied document %d from the trash after 30 days", doc_id)
        except Exception:
            log.exception("upkeep failed")
        try:
            updates.periodic(con)   # asks GitHub only every few hours, and only if the setting allows
        except Exception:
            log.exception("checking for a new version failed")
        finally:
            con.close()
