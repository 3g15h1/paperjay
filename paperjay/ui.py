"""Serving the web app: the page shell for every app route, static files, the PWA manifest, the service
worker, and the share target (Android's share sheet posts files to /share)."""
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import Con, User, _ingest

STATIC = Path(__file__).with_name("static")
APP_ROUTES = ["/", "/login", "/setup", "/browse", "/index", "/library", "/documents/{doc_id}", "/upload", "/review", "/trash", "/labels", "/settings", "/settings/{section}", "/secure"]
router = APIRouter()
_shell: str | None = None


class Static(StaticFiles):
    """Static files that the browser re-checks each time (cheap with ETags), so an upgrade shows up at once."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = "no-cache"
        return r


def shell() -> str:
    global _shell
    if _shell is None:
        _shell = (STATIC / "index.html").read_text().replace("__VERSION__", __version__)
    return _shell


async def page(request: Request):
    return HTMLResponse(shell(), headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
                                          "Referrer-Policy": "same-origin"})


for path in APP_ROUTES:
    router.add_api_route(path, page, methods=["GET"], include_in_schema=False, response_class=HTMLResponse)


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return JSONResponse({
        "name": "PaperJay", "short_name": "PaperJay", "id": "/", "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#0e0f12", "theme_color": "#0e0f12",
        "description": "File your documents, then ask them questions.",
        "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/static/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
        "share_target": {"action": "/share", "method": "POST", "enctype": "multipart/form-data",
                         "params": {"files": [{"name": "file", "accept": ["application/pdf", "image/*"]}]}},
        "shortcuts": [{"name": "Add documents", "url": "/upload"}, {"name": "Library", "url": "/library"}],
    }, media_type="application/manifest+json")


# Network only: PaperJay needs the server anyway. Having a service worker makes it installable everywhere.
SW = """self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
"""


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return Response(SW, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.post("/share", include_in_schema=False)
def share(request: Request, con: Con, user: User, file: list[UploadFile] = File([])):
    ids = [_ingest(request, con, f, "share", user).id for f in file if f.filename]
    return RedirectResponse("/upload?shared=" + ",".join(ids), status_code=303)
