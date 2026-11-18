"""The camera scanner: photos in, a clean multi-page PDF document out.

    POST /api/scan/detect               a small camera frame (JPEG body) → {corners (0-1 of the frame), found}:
                                        the live camera's outline, several times a second
    POST /api/scan/pages                one photo → {id, width, height, corners, found} (corners detected; the
                                        optional form field hint=x1,y1,…  (0-1) is used if none are found)
    GET  /api/scan/pages/{id}/photo     the photo, smaller, for the corner editor
    GET  /api/scan/pages/{id}/preview?corners=x,y,…&look=bw&rotate=0   the cleaned-up page, smaller
    POST /api/scan/finish               {pages: [{id, corners, look, rotate}], title?, mode?: "one"|"separate"} → added like an
                                        upload (one document, or one per page)

Photos wait in media/tmp (as scan-<id>.jpg) and are removed when the document is made, or after a day.
"""
import io
import re
import tempfile
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from PIL import Image

from . import filetypes, scanner
from .api import Con, User, _ingest, _upload_json

router = APIRouter()
_ID = re.compile(r"^[0-9a-f]{32}$")


def _photo_path(request: Request, page_id: str) -> Path:
    if not _ID.match(page_id):
        raise HTTPException(404, "no such page")
    p = request.app.state.paperjay["store"].root / "tmp" / f"scan-{page_id}.jpg"
    if not p.exists():
        raise HTTPException(404, "that photo has expired; take it again")
    return p


def _corners(text: str | list) -> list[list[float]]:
    vals = [float(v) for v in (text.split(",") if isinstance(text, str) else [c for pt in text for c in pt])]
    if len(vals) != 8:
        raise HTTPException(400, "corners are 4 points: x1,y1,…,x4,y4")
    return [vals[i:i + 2] for i in range(0, 8, 2)]


def _webp(img: Image.Image, width: int) -> Response:
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    img.thumbnail((width, width * 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=80)
    return Response(buf.getvalue(), media_type="image/webp", headers={"Cache-Control": "private, max-age=600"})


@router.post("/api/scan/detect")
async def detect_frame(request: Request):
    body = await request.body()
    if not body or len(body) > 2 * 1024 * 1024:
        raise HTTPException(400, "send one camera frame as JPEG, at most 2 MB")
    img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    if img is None or max(img.shape[:2]) > 1600:
        raise HTTPException(400, "send one camera frame as JPEG, at most 1600 pixels")
    h, w = img.shape[:2]
    found = scanner.detect(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    return {"found": found.found, "corners": [[round(x / w, 4), round(y / h, 4)] for x, y in found.corners]}


@router.post("/api/scan/pages")
def add_page(request: Request, file: UploadFile = File(...), hint: str | None = Form(None)):
    store = request.app.state.paperjay["store"]
    staged = store.stage(file.file, max_bytes=request.app.state.config.max_upload_bytes)
    try:
        ftype = filetypes.sniff(staged.path)
        if ftype is None or ftype.kind != "image":
            raise HTTPException(415, "the scanner takes photos (JPEG, PNG, HEIC, WebP)")
        img = scanner.load(staged.path)
    finally:
        store.discard(staged)
    page_id = uuid.uuid4().hex
    Image.fromarray(img).save(store.root / "tmp" / f"scan-{page_id}.jpg", "JPEG", quality=92)
    found = scanner.detect(img)
    corners, ok = found.corners, found.found
    if not ok and hint:
        h, w = img.shape[:2]
        pts = _corners(hint)
        if all(0 <= x <= 1 and 0 <= y <= 1 for x, y in pts):
            corners, ok = [[round(x * w, 1), round(y * h, 1)] for x, y in pts], True
    return {"id": page_id, "width": img.shape[1], "height": img.shape[0], "corners": corners, "found": ok}


@router.get("/api/scan/pages/{page_id}/photo")
def page_photo(request: Request, page_id: str, w: int = Query(1200, ge=200, le=2400)):
    with Image.open(_photo_path(request, page_id)) as im:
        return _webp(im.copy(), w)


@router.get("/api/scan/pages/{page_id}/preview")
def page_preview(request: Request, page_id: str, corners: str, look: str = "bw", rotate: int = 0,
                 w: int = Query(900, ge=200, le=1600)):
    img = scanner.load(_photo_path(request, page_id))
    try:
        return _webp(scanner.scan(img, _corners(corners), look, rotate), w)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/scan/finish")
def finish(request: Request, con: Con, user: User, payload: dict):
    pages = payload.get("pages") or []
    if not pages or len(pages) > 100:
        raise HTTPException(400, "send 1 to 100 pages")
    import img2pdf
    from . import settings as settings_mod
    mode = payload.get("mode", "one")
    if mode not in ("one", "separate"):
        raise HTTPException(400, "mode must be 'one' or 'separate'")
    store = request.app.state.paperjay["store"]
    photos = [_photo_path(request, str(p.get("id", ""))) for p in pages]
    # "one": all pages become one document; "separate": every page is a document of its own
    groups = [list(zip(pages, photos))] if mode == "one" else [[pp] for pp in zip(pages, photos)]
    stamp = time.strftime("%Y-%m-%d %H.%M")
    title = (payload.get("title") or "").strip()
    # Phase D: the scan page's "Keeping the paper?" number: a claim counts only with the setting on
    paper_no = payload.get("paper_no")
    paper_no = paper_no if isinstance(paper_no, int) and not isinstance(paper_no, bool) else None
    paper_next = bool(payload.get("paper_next")) and bool(settings_mod.get(con, "paper_numbers"))
    pdfs = []
    with tempfile.TemporaryDirectory(dir=store.root / "tmp") as tmp:
        for g, group in enumerate(groups):
            files = []
            for i, (p, photo) in enumerate(group):
                look = p.get("look", "bw")
                try:
                    out = scanner.scan(scanner.load(photo), _corners(p.get("corners")), look, int(p.get("rotate", 0)))
                except ValueError as e:
                    raise HTTPException(400, str(e))
                # black & white as a 1-bit PNG (tiny, sharp, like a fax); the others as JPEG
                f = Path(tmp) / (f"g{g}p{i}.png" if look == "bw" else f"g{g}p{i}.jpg")
                out.save(f, **({"optimize": True} if look == "bw" else {"quality": 90}))
                files.append(str(f))
            # a page photographed whole is about letter/A4 size: that gives the scan its resolution
            dpi = max(150, min(600, round(max(Image.open(files[0]).size) / 11)))
            pdfs.append(img2pdf.convert(files, layout_fun=img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))))
    uploads = []
    for g, pdf in enumerate(pdfs):
        n = f" ({g + 1})" if len(pdfs) > 1 else ""
        up = _ingest(request, con, UploadFile(io.BytesIO(pdf), filename=f"Scan {stamp}{n}.pdf"), "scan", user,
                     title=(f"{title}{n}" if title else None),
                     # one explicit paper number cannot go on several documents: the first one gets it
                     paper_no=paper_no if g == 0 else None,
                     paper_next=paper_next)
        uploads.append(_upload_json(up))
    for photo in photos:
        photo.unlink(missing_ok=True)
    return {"uploads": uploads}
