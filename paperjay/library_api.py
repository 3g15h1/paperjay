"""HTTP API for browsing and managing the library.

    GET    /api/documents                      list / search (q, tags, correspondent_id, document_type_id,
                                               date_from, date_to, needs_review, status, trash, sort, limit, offset)
    PATCH  /api/documents/{id}                 edit title, doc_date, correspondent_id, document_type_id, tags
    GET    /api/documents/{id}/history         who changed what
    GET    /api/documents/{id}/notes, POST …   notes
    POST   /api/documents/{id}/trash | restore
    DELETE /api/documents/{id}                 delete for good (only from the trash)
    POST   /api/trash/empty
    GET    /api/documents/{id}/pages/{n}?w=    one page as an image (works on every browser, unlike embedded PDFs)
    POST   /api/documents/bulk                 {ids, action, value}
    POST   /api/documents/{id}/edit-pages      turn, remove, reorder and split pages (pageedit.py)
    POST   /api/documents/merge                {ids} join documents into one
    GET    /api/labels/{kind}, POST …, PATCH /api/labels/{kind}/{id}, DELETE …, POST /api/labels/{kind}/merge
    GET    /api/views, POST …, DELETE /api/views/{id}   saved searches
    GET    /api/summary                        counts for the navigation
"""
import json
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from . import analyze, auth, intake, jobs, ocr
from .api import Con, User, _doc_or_404
from .db import transaction
from .library import LABEL_TABLES, Library, NotFound

router = APIRouter()
PAGE_WIDTHS = (600, 1200, 1800)


def _ids(value: str | None) -> list[int]:
    try:
        return [int(x) for x in (value or "").split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids must be numbers separated by commas")


@router.get("/api/documents")
def documents(con: Con, q: str = "", tags: str | None = None, correspondent_id: int | None = None,
              document_type_id: int | None = None, date_from: str | None = None, date_to: str | None = None,
              needs_review: bool | None = None, status: str | None = None, trash: bool = False,
              sort: str = "relevance", limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
              category: str | None = None, topic: str | None = None, series: str | None = None,
              people: str | None = None, has_paper: bool | None = None, paper_no: int | None = None,
              term: str | None = None):
    try:
        return Library(con).search(q, tag_ids=_ids(tags), correspondent_id=correspondent_id,
                                   document_type_id=document_type_id, date_from=date_from or None,
                                   date_to=date_to or None, needs_review=needs_review, status=status,
                                   in_trash=trash, sort=sort, limit=limit, offset=offset,
                                   category=category or None, topic=topic or None, series=series or None,
                                   people=tuple(p.strip() for p in (people or "").split(",") if p.strip()),
                                   has_paper=has_paper, paper_no=paper_no, term=term or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


EDITABLE = {"title", "doc_date", "correspondent_id", "document_type_id", "tags", "paper_no", "category", "keep_until", "warranty_until"}


@router.patch("/api/documents/{doc_id}")
def edit(con: Con, user: User, doc_id: int, payload: dict):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    unknown = set(payload) - EDITABLE
    if unknown:
        raise HTTPException(400, f"can't change {', '.join(sorted(unknown))}")
    try:
        return lib.update(doc_id, user_id=user, **payload)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # e.g. a label id that doesn't exist (foreign key)
        if "FOREIGN KEY" in str(e):
            raise HTTPException(400, "unknown tag, company or type")
        raise


@router.get("/api/documents/{doc_id}/history")
def history(con: Con, doc_id: int):
    return Library(con).history(doc_id)


@router.get("/api/documents/{doc_id}/notes")
def notes(con: Con, doc_id: int):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    return lib.notes(doc_id)


@router.post("/api/documents/{doc_id}/notes")
def add_note(con: Con, user: User, doc_id: int, payload: dict):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    try:
        lib.add_note(doc_id, str(payload.get("body", "")), user_id=user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return lib.notes(doc_id)


@router.post("/api/documents/{doc_id}/trash")
def trash(con: Con, user: User, doc_id: int):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    lib.trash(doc_id, user_id=user)
    return {"ok": True}


@router.post("/api/documents/{doc_id}/restore")
def restore(con: Con, user: User, doc_id: int):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    lib.restore(doc_id, user_id=user)
    return {"ok": True}


def _purge(request: Request, lib: Library, doc_id: int, user) -> None:
    checksum, ext = lib.purge(doc_id, user_id=user)
    store = request.app.state.paperjay["store"]
    store.remove(checksum, ext)
    for p in (store.root / "pages" / checksum[:2]).glob(f"{checksum}-*"):
        p.unlink(missing_ok=True)


@router.delete("/api/documents/{doc_id}")
def purge(request: Request, con: Con, user: User, doc_id: int):
    lib = Library(con)
    _doc_or_404(lib, doc_id)
    try:
        _purge(request, lib, doc_id, user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/api/trash/empty")
def empty_trash(request: Request, con: Con, user: User):
    lib = Library(con)
    ids = [r[0] for r in con.execute("SELECT id FROM documents WHERE deleted_at IS NOT NULL")]
    for doc_id in ids:
        _purge(request, lib, doc_id, user)
    return {"deleted": len(ids)}


@router.get("/api/documents/{doc_id}/pages/{n}")
def page(request: Request, con: Con, doc_id: int, n: int, w: int = 1200):
    """Page n (from 1) as a WebP image, rendered once per width and kept in media/pages."""
    doc = _doc_or_404(Library(con), doc_id)
    w = min(PAGE_WIDTHS, key=lambda x: abs(x - w))
    pages = doc["page_count"] or 1
    if not 1 <= n <= pages:
        raise HTTPException(404, "no such page")
    store = request.app.state.paperjay["store"]
    out = store.root / "pages" / doc["checksum"][:2] / f"{doc['checksum']}-{n}-{w}.webp"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        if doc["archive_checksum"]:
            src = store.path("archive", doc["checksum"], ".pdf")
        else:
            src = store.path("originals", doc["checksum"], doc["original_ext"])
        try:
            _render(src, "application/pdf" if src.suffix == ".pdf" else doc["mime_type"], n, w, out)
        except Exception as e:
            raise HTTPException(500, f"couldn't show this page: {e}")
    return FileResponse(out, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


def _render(src: Path, mime: str, n: int, w: int, out: Path) -> None:
    from PIL import Image, ImageOps
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        if mime == "application/pdf":
            prefix = Path(tmp) / "p"
            r = ocr.run(["pdftoppm", "-f", str(n), "-l", str(n), "-scale-to-x", str(w), "-scale-to-y", "-1",
                         "-png", "-singlefile", str(src), str(prefix)], 120)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[-200:])
            img = Image.open(prefix.with_suffix(".png"))
        else:
            import pillow_heif
            pillow_heif.register_heif_opener()
            img = Image.open(src)
            img.seek(n - 1)
            img = ImageOps.exif_transpose(img.copy())
            img.thumbnail((w, w * 3))
        tmp_out = Path(tmp) / "out.webp"
        img.convert("RGB").save(tmp_out, "WEBP", quality=82, method=4)
        tmp_out.replace(out)


# ---- bulk ----

BULK = {"add_tags", "remove_tags", "set_correspondent", "set_type", "trash", "restore", "reprocess", "analyze",
        "mark_reviewed", "number_in_order", "confirm_filing", "confirm_fields"}


@router.post("/api/documents/bulk")
def bulk(request: Request, con: Con, user: User, payload: dict):
    ids = [int(i) for i in payload.get("ids") or []]
    action, value = payload.get("action"), payload.get("value")
    if action not in BULK or not ids:
        raise HTTPException(400, f"send ids and an action: {', '.join(sorted(BULK))}")
    who = getattr(request.state, "user", None)   # a key limited to some permissions (auth.may)
    if who is not None and not auth.may(who, "delete" if action == "trash" else "update"):
        raise HTTPException(403, "this access key isn't allowed to do that")
    lib = Library(con)
    if action == "number_in_order":  # Phase D: sequential free numbers, one transaction
        assigned = lib.assign_paper_numbers(ids, user_id=user)
        return {"done": len(assigned), "assigned": assigned}
    done = 0
    for doc_id in ids:
        try:
            doc = lib.get(doc_id)
        except NotFound:
            continue
        current = [t["id"] for t in doc["tags"]]
        if action == "add_tags":
            lib.update(doc_id, user_id=user, tags=current + [int(v) for v in value or []])
        elif action == "remove_tags":
            lib.update(doc_id, user_id=user, tags=[t for t in current if t not in {int(v) for v in value or []}])
        elif action == "set_correspondent":
            lib.update(doc_id, user_id=user, correspondent_id=int(value) if value else None)
        elif action == "set_type":
            lib.update(doc_id, user_id=user, document_type_id=int(value) if value else None)
        elif action == "trash":
            lib.trash(doc_id, user_id=user)
        elif action == "restore":
            lib.restore(doc_id, user_id=user)
        elif action == "reprocess":
            intake.reprocess(con, doc_id, user_id=user)
        elif action == "analyze":
            with transaction(con):
                jobs.enqueue(con, analyze.ANALYZE_JOB, document_id=doc_id, priority=90, payload={"force": True})
        elif action == "mark_reviewed":
            # the same as one document's "Looks right": what was looked at stays reviewed, so a
            # later refile only flags it again for a new reason (0.3.2; bulk had missed it)
            sug = doc["suggestions"]
            reviewed = sorted(set(sug.get("reviewed") or []) | set(sug.get("review") or []))
            lib.update(doc_id, user_id=user, needs_review=False,
                       suggestions={**sug, "review": [], "reviewed": reviewed})
        elif action == "confirm_filing":   # Tidy up (spec E): imported filing, in bulk
            lib.confirm_filing(doc_id, user_id=user)
        elif action == "confirm_fields":   # Tidy up (spec E): low-evidence values, in bulk
            from .health_api import confirm_low_evidence
            try:
                confirm_low_evidence(con, doc_id, None, user)
            except ValueError:
                continue
        done += 1
    state = request.app.state.paperjay
    state["worker"].wake()
    state["ai_worker"].wake()
    return {"done": done}


# ---- labels ----

def _kind(kind: str) -> str:
    if kind not in LABEL_TABLES:
        raise HTTPException(404, "labels are tag, correspondent or document_type")
    return kind


@router.get("/api/labels/{kind}")
def labels(con: Con, kind: str):
    return Library(con).labels(_kind(kind))


@router.post("/api/labels/{kind}")
def create_label(con: Con, user: User, kind: str, payload: dict):
    lib = Library(con)
    try:
        label_id = lib.create_label(_kind(kind), str(payload.get("name", "")), payload.get("color"), user_id=user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "that name is already used")
        raise
    return next(x for x in lib.labels(kind) if x["id"] == label_id)


@router.patch("/api/labels/{kind}/{label_id}")
def edit_label(con: Con, user: User, kind: str, label_id: int, payload: dict):
    lib = Library(con)
    try:
        if "name" in payload:
            lib.rename_label(_kind(kind), label_id, str(payload["name"]), user_id=user)
        if "color" in payload and kind == "tag":
            lib.set_tag_color(label_id, str(payload["color"]))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "that name is already used; merge them instead")
        raise
    return next(x for x in lib.labels(kind) if x["id"] == label_id)


@router.delete("/api/labels/{kind}/{label_id}")
def delete_label(con: Con, user: User, kind: str, label_id: int):
    try:
        Library(con).delete_label(_kind(kind), label_id, user_id=user)
    except NotFound as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.get("/api/labels/correspondent/{label_id}/aliases")
def list_aliases(con: Con, label_id: int):
    try:
        return {"aliases": Library(con).list_aliases(label_id)}
    except NotFound as e:
        raise HTTPException(404, str(e))


@router.post("/api/labels/correspondent/{label_id}/aliases")
def add_alias(con: Con, user: User, label_id: int, payload: dict):
    lib = Library(con)
    try:
        alias_id = lib.add_alias(label_id, str(payload.get("alias", "")), source="person")
    except NotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if alias_id is None:
        raise HTTPException(400, "that name teaches nothing: it is the company's own name, "
                                   "or another company's")
    return {"aliases": lib.list_aliases(label_id)}


@router.delete("/api/labels/correspondent/{label_id}/aliases/{alias_id}")
def delete_alias(con: Con, user: User, label_id: int, alias_id: int):
    lib = Library(con)
    try:
        lib.delete_alias(label_id, alias_id)
    except NotFound as e:
        raise HTTPException(404, str(e))
    return {"aliases": lib.list_aliases(label_id)}


@router.post("/api/labels/{kind}/merge")
def merge_labels(con: Con, user: User, kind: str, payload: dict):
    try:
        Library(con).merge_labels(_kind(kind), [int(i) for i in payload.get("source_ids") or []],
                                  int(payload.get("target_id")), user_id=user)
    except (NotFound, TypeError) as e:
        raise HTTPException(400, str(e))
    return Library(con).labels(kind)


# ---- saved searches ----

VIEW_KEYS = {"q", "tags", "correspondent_id", "document_type_id", "date_from", "date_to", "sort", "needs_review"}


@router.get("/api/views")
def views(con: Con):
    return [{**dict(r), "params": json.loads(r["params"])}
            for r in con.execute("SELECT id, name, params, created_at FROM saved_views ORDER BY name COLLATE NOCASE")]


@router.post("/api/views")
def save_view(con: Con, user: User, payload: dict):
    name = " ".join(str(payload.get("name", "")).split())[:80]
    params = {k: v for k, v in (payload.get("params") or {}).items() if k in VIEW_KEYS and v not in (None, "", [])}
    if not name:
        raise HTTPException(400, "give the search a name")
    with transaction(con):
        cur = con.execute("INSERT INTO saved_views(name, params, user_id) VALUES (?, ?, ?)",
                          (name, json.dumps(params), user))
    return {"id": cur.lastrowid, "name": name, "params": params}


@router.delete("/api/views/{view_id}")
def delete_view(con: Con, view_id: int):
    with transaction(con):
        con.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))
    return {"ok": True}


@router.get("/api/summary")
def summary(con: Con):
    counts = Library(con).counts()
    processing = con.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL "
                             "AND status IN ('pending', 'processing')").fetchone()[0]
    from . import keeping
    return {**counts, "processing": processing, "shred": keeping.count_ready(con)}


# ---- fixing pages (pageedit.py): always new documents; the old ones go to the trash ----

def _may_replace(request: Request) -> None:
    who = getattr(request.state, "user", None)
    if who is not None and not (auth.may(who, "create") and auth.may(who, "delete")):
        raise HTTPException(403, "changing pages makes a new document and moves the old one to the trash: "
                                 "this access key needs both create and delete")


@router.post("/api/documents/{doc_id}/edit-pages")
def edit_pages(request: Request, con: Con, user: User, doc_id: int, payload: dict):
    """{pages: [{n, rotate, keep}] in the order wanted, split_after: [n…]} -> {documents: [new ids]}"""
    from . import pageedit
    _may_replace(request)
    _doc_or_404(Library(con), doc_id)
    st = request.app.state
    try:
        ids = pageedit.edit(con, st.paperjay["store"], doc_id, payload.get("pages"), payload.get("split_after") or (),
                            user_id=user, max_bytes=st.config.max_upload_bytes)
    except pageedit.EditError as e:
        raise HTTPException(400, str(e))
    st.paperjay["worker"].wake()
    return {"documents": ids}


@router.post("/api/documents/merge")
def merge_documents(request: Request, con: Con, user: User, payload: dict):
    """{ids: [...]} in page order -> {document: new id}"""
    from . import pageedit
    _may_replace(request)
    ids = payload.get("ids")
    lib = Library(con)
    for i in ids if isinstance(ids, list) else []:
        if not isinstance(i, int) or isinstance(i, bool):
            raise HTTPException(400, "ids must be document ids")
        _doc_or_404(lib, i)
    st = request.app.state
    try:
        new_id = pageedit.merge(con, st.paperjay["store"], ids, user_id=user, max_bytes=st.config.max_upload_bytes)
    except pageedit.EditError as e:
        raise HTTPException(400, str(e))
    st.paperjay["worker"].wake()
    return {"document": new_id}
