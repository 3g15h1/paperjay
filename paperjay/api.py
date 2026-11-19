"""HTTP API for intake and processing status.

    POST /api/documents                      upload one or more files (multipart field "file")
    POST /api/documents/post_document/       Paperless-ngx compatible upload, for existing phone shortcuts and scripts
    GET  /api/tasks/?task_id=…               Paperless-ngx compatible status of such an upload
    GET  /api/uploads                        recent uploads and what happened to them
    GET  /api/documents/{id}                 a document, with its processing state
    GET  /api/documents/{id}/original|archive|thumb
    POST /api/documents/{id}/reprocess       run OCR again
    GET  /api/jobs?status=failed             background jobs

Every request is signed in (see auth_api.py); `current_user` gives the person's id for the history.
"""
import sqlite3
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import intake, settings
from .db import connect
from .library import Library, NotFound, check_date

router = APIRouter()

STATUS_CODES = {"accepted": 201, "duplicate": 409, "rejected": 415}
# Served files are shown in the browser but may never run script on our origin
FILE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}


def get_con(request: Request):
    con = connect(request.app.state.config.db_path)
    try:
        yield con
    finally:
        con.close()


def current_user(request: Request) -> int | None:
    """The signed-in person's id (set by auth_api's guard), for "who did this" in the history."""
    user = getattr(request.state, "user", None)
    return user.id if user else None


Con = Annotated[sqlite3.Connection, Depends(get_con)]
User = Annotated[int | None, Depends(current_user)]


def _doc_or_404(lib: Library, doc_id: int) -> dict:
    try:
        return lib.get(doc_id)
    except NotFound:
        raise HTTPException(404, f"no document {doc_id}")


def _date_arg(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()[:10]  # Paperless clients send full timestamps
    try:
        check_date(value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return value


def _ingest(request: Request, con, f: UploadFile, source: str, user, **kw) -> intake.Upload:
    st = request.app.state.paperjay
    up = intake.ingest(con, st["store"], f.file, f.filename, source=source,
                       max_bytes=request.app.state.config.max_upload_bytes, user_id=user, **kw)
    if up.outcome == "accepted":
        st["worker"].wake()
    return up


def _combine(request: Request, files: list[UploadFile]) -> UploadFile:
    """Several page photos -> one PDF (each page upright, at its real size), as if one file had been uploaded."""
    import io
    import time

    from . import filetypes, ocr
    store = request.app.state.paperjay["store"]
    limit = request.app.state.config.max_upload_bytes
    with tempfile.TemporaryDirectory(dir=store.root / "tmp") as tmp:
        pdfs = []
        for i, f in enumerate(files):
            staged = store.stage(f.file, max_bytes=limit)
            ftype = filetypes.sniff(staged.path)
            if ftype is None or ftype.kind != "image":
                store.discard(staged)
                raise HTTPException(415, f"{f.filename}: only photos can be combined into one document")
            src = Path(tmp) / f"p{i}{ftype.ext}"
            staged.path.replace(src)
            pdfs.append(ocr.image_to_pdf(src, ftype, Path(tmp) / f"p{i}.pdf"))
        import pikepdf
        out, parts = pikepdf.new(), [pikepdf.open(p) for p in pdfs]
        try:
            for one in parts:  # pages copied from another PDF need it open until saving
                out.pages.extend(one.pages)
            buf = io.BytesIO()
            out.save(buf)
        finally:
            for one in parts:
                one.close()
    buf.seek(0)
    return UploadFile(buf, filename=f"Scan {time.strftime('%Y-%m-%d %H.%M')}.pdf")


def _upload_json(up: intake.Upload) -> dict:
    return {"id": up.id, "outcome": up.outcome, "filename": up.filename, "document_id": up.document_id,
            "duplicate_of": up.duplicate_of, "message": up.message, "paper_no": up.paper_no}


@router.post("/api/documents")
def upload(request: Request, con: Con, user: User, file: list[UploadFile] = File(...),
           title: str | None = Form(None), doc_date: str | None = Form(None), tags: list[int] = Form([]),
           correspondent_id: int | None = Form(None), document_type_id: int | None = Form(None),
           combine: bool = Form(False), paper_no: int | None = Form(None),
           paper_next: bool = Form(False)):
    """combine=true: the files are photos of the pages of one document (the phone's multi-page scan).
    Paper numbers (Phase D): paper_no numbers the (single, or first) document; paper_next claims the
    next free number for each file — honoured only while the paper_numbers setting is on."""
    doc_date = _date_arg(doc_date)
    if combine and len(file) > 1:
        file = [_combine(request, file)]
    claim = paper_next and bool(settings.get(con, "paper_numbers"))
    results = []
    for i, f in enumerate(file):
        kw = {}
        if i == 0 and paper_no is not None:
            kw["paper_no"] = paper_no
        elif claim:
            kw["paper_next"] = True
        results.append(_upload_json(_ingest(request, con, f, "api", user, title=title if len(file) == 1 else None,
                                            doc_date=doc_date, tag_ids=tags, correspondent_id=correspondent_id,
                                            document_type_id=document_type_id, **kw)))
    outcomes = {r["outcome"] for r in results}
    if len(results) == 1:
        code = 413 if results[0]["message"] and "larger than" in results[0]["message"] \
            else STATUS_CODES[results[0]["outcome"]]
    else:
        code = 201 if outcomes == {"accepted"} else 200
    return JSONResponse({"uploads": results}, status_code=code)


@router.post("/api/documents/post_document/")
def paperless_post_document(request: Request, con: Con, user: User, document: UploadFile = File(...),
                            title: str | None = Form(None), created: str | None = Form(None),
                            correspondent: int | None = Form(None), document_type: int | None = Form(None),
                            tags: list[int] = Form([]), archive_serial_number: int | None = Form(None)):
    """Same fields and reply as Paperless-ngx: the reply is the task id as a JSON string. Duplicates are
    accepted here and reported through the task, as Paperless does. archive_serial_number becomes the
    document's paper number (Phase D)."""
    up = _ingest(request, con, document, "paperless-api", user, title=title, doc_date=_date_arg(created),
                 tag_ids=tags, correspondent_id=correspondent, document_type_id=document_type,
                 paper_no=archive_serial_number)
    if up.outcome == "rejected":
        raise HTTPException(400, up.message)
    return up.id


@router.get("/api/tasks/")
def paperless_tasks(con: Con, task_id: str = Query(...)):
    row = con.execute("SELECT u.*, d.status AS doc_status, d.error AS doc_error FROM uploads u "
                      "LEFT JOIN documents d ON d.id = u.document_id WHERE u.id = ?", (task_id,)).fetchone()
    if not row:
        return []
    if row["outcome"] != "accepted":
        status, result = "FAILURE", row["message"]
    else:
        status = {"pending": "PENDING", "processing": "STARTED", "ready": "SUCCESS", "failed": "FAILURE"}.get(
            row["doc_status"], "FAILURE")
        result = row["doc_error"] if status == "FAILURE" else (
            f"Success. New document id {row['document_id']} created" if status == "SUCCESS" else None)
    return [{"task_id": row["id"], "task_file_name": row["filename"], "date_created": row["created_at"],
             "type": "file", "status": status, "result": result, "acknowledged": False,
             "related_document": str(row["document_id"]) if row["document_id"] else None}]


@router.get("/api/uploads")
def uploads(con: Con, limit: int = Query(20, ge=1, le=200)):
    rows = con.execute("SELECT u.*, d.status AS document_status, d.title AS document_title FROM uploads u "
                       "LEFT JOIN documents d ON d.id = u.document_id ORDER BY u.created_at DESC, u.rowid DESC "
                       "LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/documents/{doc_id}")
def document(con: Con, doc_id: int):
    doc = _doc_or_404(Library(con), doc_id)
    job = con.execute("SELECT status, attempts, max_attempts, error, run_after FROM jobs "
                      "WHERE document_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                      (doc_id, intake.CONSUME_JOB)).fetchone()
    doc["processing"] = dict(job) if job else None
    from . import analyze
    doc["card"] = analyze.get_card(con, doc_id, doc["content"])
    from . import keeping
    doc["keeping"] = keeping.for_document(con, doc_id)
    return doc


@router.get("/api/documents/{doc_id}/original")
def document_original(request: Request, con: Con, doc_id: int):
    return document_file(request, con, doc_id, "original")


@router.get("/api/documents/{doc_id}/archive")
def document_archive(request: Request, con: Con, doc_id: int):
    return document_file(request, con, doc_id, "archive")


@router.get("/api/documents/{doc_id}/thumb")
def document_thumb(request: Request, con: Con, doc_id: int):
    return document_file(request, con, doc_id, "thumb")


def document_file(request: Request, con, doc_id: int, which: str):
    doc = _doc_or_404(Library(con), doc_id)
    store = request.app.state.paperjay["store"]
    if which == "original":
        path, media, name = store.path("originals", doc["checksum"], doc["original_ext"]), doc["mime_type"], \
            doc["original_name"]
    elif which == "archive" and doc["archive_checksum"]:
        path, media, name = store.path("archive", doc["checksum"], ".pdf"), "application/pdf", f"{doc['title']}.pdf"
    elif which == "thumb":
        path, media, name = store.path("thumbs", doc["checksum"], ".webp"), "image/webp", None
    else:
        raise HTTPException(404, "no such file")
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type=media, filename=name, content_disposition_type="inline",
                        headers=FILE_HEADERS)


@router.post("/api/documents/{doc_id}/reprocess")
def reprocess(request: Request, con: Con, user: User, doc_id: int):
    _doc_or_404(Library(con), doc_id)
    job_id = intake.reprocess(con, doc_id, user_id=user)
    request.app.state.paperjay["worker"].wake()
    return {"job_id": job_id}


@router.get("/api/jobs")
def job_list(con: Con, status: str | None = Query(None, pattern="^(queued|running|done|failed)$"),
             limit: int = Query(50, ge=1, le=500)):
    sql, args = "SELECT * FROM jobs", []
    if status:
        sql, args = sql + " WHERE status = ?", [status]
    return [dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]
