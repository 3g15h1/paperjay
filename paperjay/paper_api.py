"""Paper numbers (spec Phase D): the shelf side of the library.

    GET /api/paper/next                     {enabled, next} — what the upload/scan pages offer
    GET /api/paper-labels.pdf               printable labels (see labels_pdf.py):
        ?ids=3,4          labels for those documents
        ?from=10&to=39    labels for a number range — documents where they exist, blank-ahead
                          labels where they don't; printing blanks reserves the numbers
        &layout=5160      5160 (30/page, Letter) | 7160 (21/page, A4) | single62 (one 62 mm label)
        &base=https://…   address the QR codes hold; default: the Secure (HTTPS) address when
                          one is set up, else the address this was requested from
    GET /api/paper/separator.pdf?paper=letter|a4&copies=   separator sheets (barcodes.py)
    GET  /api/shred?all=              paper past its keep-until date (keeping.py)
    POST /api/documents/{id}/shredded the paper was shredded (frees its shelf number); …/unshred undoes it
    POST /api/shred/bulk              {ids} several at once
"""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from . import labels_pdf, papers, settings
from .api import Con, User, _doc_or_404
from .library import now_iso
from .https import allowed

router = APIRouter()


@router.get("/api/paper/next")
def paper_next(con: Con, user: User):
    return {"enabled": bool(settings.get(con, "paper_numbers")), "next": papers.next_free(con)}


def _base_url(request: Request) -> str:
    """Where label QR codes point: the Secure address when HTTPS is set up with a name
    the authority covers, else the address this page was opened with (the print page
    says plainly that such codes only work on this network)."""
    tls = getattr(request.app.state, "https", None)
    if tls is not None:
        try:
            names = [n for n in tls.names() if allowed(n) is not None]
            if names:
                port = "" if tls.port == 443 else f":{tls.port}"
                return f"https://{sorted(names)[0]}{port}/"
        except Exception:
            pass
    return str(request.base_url)


@router.get("/api/paper/separator.pdf")
def separator_sheet(paper: str = "letter", copies: int = Query(1, ge=1, le=50)):
    """Separator sheets for scanning a stack of documents in one go (barcodes.py)."""
    if paper not in ("letter", "a4"):
        raise HTTPException(400, "paper is letter or a4")
    return Response(labels_pdf.separator(paper, copies), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="paperjay-separator.pdf"'})


@router.get("/api/paper-labels.pdf")
def paper_labels(request: Request, con: Con, user: User,
                 ids: str | None = Query(None), from_: int | None = Query(None, alias="from"),
                 to: int | None = Query(None), layout: str = Query("5160"),
                 base: str | None = Query(None)):
    if layout not in labels_pdf.LAYOUTS:
        raise HTTPException(400, f"layout must be one of {', '.join(sorted(labels_pdf.LAYOUTS))}")
    base_url = (base or _base_url(request)).rstrip("/") + "/"
    if ids:
        try:
            wanted = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "ids must be document ids, comma-separated")
        rows = {r[0]: r[1] for r in con.execute(
            "SELECT id, paper_no FROM documents WHERE deleted_at IS NULL")}
        labels = []
        for doc_id in wanted:
            if doc_id not in rows:
                raise HTTPException(404, f"no document {doc_id}")
            if rows[doc_id] is None:
                raise HTTPException(400, f"document {doc_id} has no paper number yet")
            labels.append(labels_pdf.label_for(con, doc_id, base_url))
        return _pdf(labels_pdf.build(labels, layout))
    if from_ is None or to is None:
        raise HTTPException(400, "give ids=… or a from=…&to=… range")
    if from_ < 1 or to < from_ or to - from_ > 999:
        raise HTTPException(400, "the range must be 1..1000 numbers, in order")
    docs = {r[0]: r[1] for r in con.execute(
        "SELECT paper_no, id FROM documents WHERE paper_no BETWEEN ? AND ? AND deleted_at IS NULL",
        (from_, to))}
    labels = [labels_pdf.label_for(con, docs[n], base_url) if n in docs
              else labels_pdf.blank_label(n, base_url) for n in range(from_, to + 1)]
    pdf = labels_pdf.build(labels, layout)
    # Printing blank-ahead labels reserves those numbers (Phase D task 6): the next free
    # number moves past the sheet, and a reserved number can be claimed exactly once.
    papers.reserve_blanks(con, from_, to, user_id=user)
    return _pdf(pdf)


def _pdf(data: bytes) -> Response:
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="paper-labels.pdf"'})


# ---- how long to keep the paper, and shredding it (keeping.py) ----

@router.get("/api/shred")
def shred_list(con: Con, all: bool = False):
    """Paper whose keep-until date has passed. all=true adds documents without a shelf number."""
    from . import keeping
    docs = keeping.ready(con, shelf_only=not all)
    return {"documents": docs, "count": len(docs), "paper_numbers": bool(settings.get(con, "paper_numbers"))}


def _shred(con, doc_id: int, user) -> dict:
    from .library import Library
    lib = Library(con)
    doc = _doc_or_404(lib, doc_id)
    if doc["shredded_at"]:
        return doc
    return lib.update(doc_id, user_id=user, by="person", shredded_at=now_iso(),
                      shredded_paper_no=doc["paper_no"], paper_no=None)


@router.post("/api/documents/{doc_id}/shredded")
def shredded(con: Con, user: User, doc_id: int):
    """The paper was shredded: its shelf number is freed (never reused); the digital copy stays."""
    return _shred(con, doc_id, user)


@router.post("/api/shred/bulk")
def shredded_many(con: Con, user: User, payload: dict):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise HTTPException(400, "ids must be a list of document ids")
    for doc_id in ids[:1000]:
        _shred(con, doc_id, user)
    return {"shredded": len(ids[:1000])}


@router.post("/api/documents/{doc_id}/unshred")
def unshred(con: Con, user: User, doc_id: int):
    """Undo "shredded": the paper number comes back if no other document took it meanwhile."""
    from .library import Library
    lib = Library(con)
    doc = _doc_or_404(lib, doc_id)
    if not doc["shredded_at"]:
        return doc
    n = doc["shredded_paper_no"]
    back = n if n is not None and papers.holder(con, n, exclude_doc=doc_id) is None else None
    return lib.update(doc_id, user_id=user, by="person", shredded_at=None, shredded_paper_no=None, paper_no=back)
