"""Import and export (admin only).

    POST /api/import/paperless/check   {url, token} -> what an import would do
    POST /api/import/paperless         {url, token} -> starts the import in the background
    GET  /api/import/status            progress of the running or last import
    GET  /api/export.zip               everything as one zip (files + manifest.json)
    GET  /api/update                   newest release (?force=true asks GitHub now), last update's outcome
    GET  /api/update/notice            whether to show admins the "new version" notice
    POST /api/update/dismiss           {tag} -> hide that notice for this release
    POST /api/update                   {tag?} -> update (Proxmox install)

The Paperless token is used for the import only and never stored.
"""
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from . import ask_api, export, importer
from .api import Con, User
from .db import connect

router = APIRouter()


def _source(payload: dict) -> tuple[str, str]:
    url, token = str(payload.get("url", "")).strip(), str(payload.get("token", "")).strip()
    if not url.startswith(("http://", "https://")) or not token:
        raise HTTPException(400, "give the Paperless address (http://…) and an API token")
    return url, token


@router.post("/api/import/paperless/check")
def check(con: Con, payload: dict):
    url, token = _source(payload)
    try:
        return importer.check_paperless(con, url, token)
    except PermissionError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"couldn't read Paperless at {url}: {type(e).__name__}: {e}")


@router.post("/api/import/paperless")
def start(request: Request, user: User, payload: dict):
    url, token = _source(payload)
    state = request.app.state.paperjay
    db_path = request.app.state.config.db_path

    def run():
        importer.import_paperless(db_path, state["store"], url, token, user)
        con = connect(db_path)
        try:
            ask_api.queue_missing(con)   # facts and search by meaning for everything imported
        finally:
            con.close()
        state["worker"].wake()
        state["ai_worker"].wake()

    if not importer.start(run):
        raise HTTPException(409, "an import is already running")
    return importer.progress()


@router.get("/api/import/status")
def status():
    return importer.progress()


@router.get("/api/export.zip")
def export_zip(request: Request, con: Con):
    store = request.app.state.paperjay["store"]
    out = store.root / "tmp" / f"paperjay-export-{time.strftime('%Y-%m-%d-%H%M%S')}.zip"
    export.export_zip(con, store, out)
    return FileResponse(out, media_type="application/zip", filename=out.name,
                        background=BackgroundTask(lambda: Path(out).unlink(missing_ok=True)))


# ---- updates (admin only, see auth.ADMIN_ONLY) ----

@router.get("/api/update")
def update_info(con: Con, force: bool = False):
    """The newest release (force=true asks GitHub now), the last update's outcome and how to update."""
    from . import updates
    return {**updates.check(force, con=con), "status": updates.status(), "how": updates.instructions()}


@router.get("/api/update/notice")
def update_notice(con: Con):
    """Whether to show admins the "new version" notice. Never asks GitHub (the upkeep thread does)."""
    from . import updates
    return updates.notice(con)


@router.post("/api/update/dismiss")
def update_dismiss(con: Con, payload: dict):
    """"Not now": hide the notice for this release (a later release shows it again)."""
    from . import updates
    try:
        updates.dismiss(con, str(payload.get("tag", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/update")
def update_start(con: Con, payload: dict):
    """Update to the newest release (the Proxmox install). With no tag, the newest one found."""
    from . import updates
    tag = str(payload.get("tag") or updates.known(con).get("latest") or "")
    if tag and not updates.newer(tag):
        raise HTTPException(400, f"PaperJay is already on {tag} or newer")
    try:
        updates.request(tag)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "tag": tag, "message": "Updating. PaperJay restarts in a minute or two; this page reloads by itself."}
