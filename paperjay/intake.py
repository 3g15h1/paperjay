"""Intake: every way a file gets in (web, API, Paperless-compatible API, consume folder) ends up here.

    receive (hash while writing to media/tmp) → check type from content → duplicate? → store original
    → add document (status 'pending') → queue the 'consume' job (OCR and the rest)

Each attempt is recorded in the uploads table, including duplicates and rejected files, so people can
see what happened to something they sent. Nothing is ever OCRed here; that's the worker's job.
"""
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from . import filetypes, jobs, papers
from .db import transaction
from .library import Duplicate, Library
from .store import FileStore, TooLarge

log = logging.getLogger("paperjay")

CONSUME_JOB = "consume"
# File names that say nothing about the document; for these the title comes from the OCR text instead
_GENERIC_NAME = re.compile(r"^(img|image|pxl|dsc|dscn|dcim|photo|scan|scanned|document|doc|file|upload|"
                           r"untitled|screenshot|cam|picture|pic|new|wp)?[\s_\-.]*\d*[\s_\-.\d]*$", re.I)


@dataclass
class Upload:
    id: str
    outcome: str  # accepted | duplicate | rejected
    filename: str
    document_id: int | None = None
    duplicate_of: int | None = None
    paper_no: int | None = None  # Phase D: the paper number the document got, when it got one
    message: str | None = None
    sender: str | None = None  # email From address, for email imports (spec B.1 signal 2)


def title_from_filename(filename: str) -> tuple[str, bool]:
    """('Water bill August', False) from 'Water_bill_August.pdf'. The flag says the name is generic
    (IMG_2041.HEIC, scan0003.pdf), so a better title should be taken from the text later."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    generic = bool(_GENERIC_NAME.match(stem)) or not re.search(r"[^\W\d_]", stem)
    title = " ".join(re.sub(r"[_]+", " ", stem).split())[:128] or "Untitled"
    return title, generic


def _safe_name(filename: str | None) -> str:
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch.isprintable())
    return name[:200] or "upload"


def ingest(con: sqlite3.Connection, store: FileStore, src: BinaryIO, filename: str | None, *, source: str,
           max_bytes: int, user_id: int | None = None, title: str | None = None, doc_date: str | None = None,
           tag_ids=(), correspondent_id: int | None = None, document_type_id: int | None = None,
           sender: str | None = None, paper_no: int | None = None, paper_next: bool = False) -> Upload:
    """paper_no / paper_next (Phase D): an explicit paper number is honoured from any source that
    passes one; paper_next claims the next free number inside the add transaction. Callers only
    pass paper_next for the upload page and camera scan — email and the consume folder never do,
    so their documents get no number unless they pass one explicitly."""
    filename = _safe_name(filename)
    up = Upload(id=uuid.uuid4().hex, outcome="rejected", filename=filename, sender=sender)
    try:
        staged = store.stage(src, max_bytes=max_bytes)
    except TooLarge:
        up.message = f"the file is larger than {max_bytes // (1024 * 1024)} MB"
        return _record(con, up, source, user_id)

    ftype = filetypes.sniff(staged.path) if staged.size else None
    if ftype is None:
        store.discard(staged)
        up.message = "empty file" if not staged.size else f"not a document PaperJay can read (needs {filetypes.SUPPORTED_TEXT})"
        return _record(con, up, source, user_id)

    existing = con.execute("SELECT id, deleted_at FROM documents WHERE checksum = ?", (staged.checksum,)).fetchone()
    if existing:
        store.discard(staged)
        return _record(con, _duplicate(up, existing["id"], existing["deleted_at"]), source, user_id)

    if paper_no is not None:  # refuse a taken/invalid number before anything is stored
        try:
            papers.validate(paper_no)
            papers.check_free(con, paper_no)
        except ValueError as e:
            store.discard(staged)
            up.message = str(e)
            return _record(con, up, source, user_id)

    store.commit(staged, ftype.ext)
    auto_title, generic = title_from_filename(filename)
    lib = Library(con)
    given = [f for f, v in (("title", (title or "").strip()), ("doc_date", doc_date)) if v]
    if paper_no is not None or paper_next:
        given.append("paper_no")
    try:
        doc_id = lib.add(title=(title or "").strip() or auto_title, checksum=staged.checksum, original_name=filename,
                         original_ext=ftype.ext, mime_type=ftype.mime, size=staged.size, owner_id=user_id,
                         doc_date=doc_date, user_id=user_id, given=given,
                         paper_no=paper_no, claim_paper=paper_next and paper_no is None)
    except Duplicate as e:  # the same file arrived twice at the same moment; the other one won
        return _record(con, _duplicate(up, e.existing_id, None), source, user_id)
    except ValueError as e:  # e.g. the paper number was claimed by a simultaneous upload
        store.remove(staged.checksum, ftype.ext)
        up.message = str(e)
        return _record(con, up, source, user_id)
    except BaseException:
        if not con.execute("SELECT 1 FROM documents WHERE checksum = ?", (staged.checksum,)).fetchone():
            store.remove(staged.checksum, ftype.ext)
        raise

    labels = {k: v for k, v in (("correspondent_id", correspondent_id), ("document_type_id", document_type_id))
              if v is not None}
    if tag_ids:
        labels["tags"] = list(tag_ids)
    if labels:
        lib.update(doc_id, user_id=user_id, **labels)

    up.outcome, up.document_id = "accepted", doc_id
    if paper_no is not None or paper_next:
        up.paper_no = lib.get(doc_id)["paper_no"]
    with transaction(con):
        jobs.enqueue(con, CONSUME_JOB, document_id=doc_id, priority=50,
                     payload={"upload_id": up.id,
                              # replace the title from the text, unless someone has renamed it by then
                              "auto_title": auto_title if generic and not (title or "").strip() else None})
        _record(con, up, source, user_id, in_transaction=True)
    log.info("accepted %s from %s as document %d", filename, source, doc_id)
    return up


def _duplicate(up: Upload, existing_id: int, deleted_at) -> Upload:
    up.outcome, up.duplicate_of = "duplicate", existing_id
    up.message = (f"this file is already in the trash as document {existing_id}; restore it from there"
                  if deleted_at else f"this file is already stored as document {existing_id}")
    return up


def _record(con, up: Upload, source: str, user_id, in_transaction: bool = False) -> Upload:
    sql = ("INSERT INTO uploads(id, source, filename, user_id, outcome, document_id, duplicate_of, message, sender) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
    args = (up.id, source, up.filename, user_id, up.outcome, up.document_id, up.duplicate_of, up.message,
            up.sender)
    if in_transaction:
        con.execute(sql, args)
    else:
        with transaction(con):
            con.execute(sql, args)
    if up.outcome != "accepted":
        log.info("%s from %s: %s (%s)", up.filename, source, up.outcome, up.message)
    return up


def requeue_unfinished(con: sqlite3.Connection) -> int:
    """Documents left pending/processing with no OCR job (a crash between storing and queueing): queue them."""
    rows = con.execute(
        "SELECT d.id FROM documents d WHERE d.status IN ('pending', 'processing') AND d.deleted_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.document_id = d.id AND j.kind = ? "
        "AND j.status IN ('queued', 'running'))", (CONSUME_JOB,)).fetchall()
    with transaction(con):
        for r in rows:
            jobs.enqueue(con, CONSUME_JOB, document_id=r[0], priority=50)
    return len(rows)


def reprocess(con: sqlite3.Connection, doc_id: int, user_id: int | None = None) -> int:
    """Run OCR again (the Retry / Redo OCR button)."""
    lib = Library(con)
    lib.get(doc_id)
    lib.update(doc_id, user_id=user_id, status="pending", error=None)
    with transaction(con):
        return jobs.enqueue(con, CONSUME_JOB, document_id=doc_id, priority=50)
