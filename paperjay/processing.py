"""The 'consume' job: OCR one stored document and fill in its text, pages, archive PDF, thumbnail, date
and (for photos named IMG_1234 and the like) a title taken from its first line of text.

Later steps (AI filing, embeddings) register in AFTER_CONSUME and are queued when a document is ready.
"""
import json
import logging
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable

from . import filetypes, jobs, ocr, settings
from .db import transaction
from .config import Config
from .dates import find_date
from .library import Library, NotFound
from .store import FileStore, sha256_file

log = logging.getLogger("paperjay")

AFTER_CONSUME: list[Callable[[sqlite3.Connection, int], None]] = []


def title_from_text(text: str) -> str | None:
    """First line that reads like a heading: some letters, not a lone number or date, a sensible length."""
    for line in text.splitlines()[:15]:
        line = " ".join(line.split()).strip(" -_:|•*")
        if 4 <= len(line) <= 80 and len(re.findall(r"[^\W\d_]", line)) >= 4:
            return line
    return None


def make_consume(config: Config, store: FileStore) -> jobs.Handler:
    def consume(con: sqlite3.Connection, job: dict) -> None:
        lib = Library(con)
        doc_id = job["document_id"]
        try:
            doc = lib.get(doc_id)
        except NotFound:
            return  # purged while waiting
        if doc["deleted_at"]:
            return
        ftype = filetypes.BY_MIME.get(doc["mime_type"])
        if ftype is None:
            _failed(lib, doc_id, f"unsupported file type {doc['mime_type']}")
            raise jobs.Permanent(f"unsupported file type {doc['mime_type']}")
        lib.update(doc_id, by="system", status="processing", error=None)
        opts = ocr.Options(language=settings.get(con, "ocr_language"), jobs=config.ocr_jobs, timeout=config.ocr_timeout,
                           max_pages=config.max_pages)
        original = store.path("originals", doc["checksum"], doc["original_ext"])
        if ftype.ext == ".pdf" and _look_for_codes(con, doc):
            if _barcodes(con, store, config, lib, doc, original):
                return   # a stack, split at its separator sheets: the parts are read on their own
        with tempfile.TemporaryDirectory(dir=store.root / "tmp", prefix=f"ocr-{doc_id}-") as wd:
            try:
                result = ocr.process(original, ftype, Path(wd), opts)
            except ocr.OcrFailed as e:
                _failed(lib, doc_id, str(e))
                raise jobs.Permanent(str(e)) from e
            except Exception as e:
                last = job["attempts"] >= job["max_attempts"]
                if last:
                    _failed(lib, doc_id, f"{type(e).__name__}: {e}")
                else:
                    lib.update(doc_id, by="system", status="pending", error=f"will retry: {e}")
                raise

            fields = {"content": result.text, "page_count": result.page_count, "status": "ready",
                      "error": "; ".join(result.warnings) or None, "archive_checksum": None}
            if result.archive:
                fields["archive_checksum"] = sha256_file(result.archive)
                store.put("archive", doc["checksum"], ".pdf", result.archive)
            else:
                store.path("archive", doc["checksum"], ".pdf").unlink(missing_ok=True)
            if result.thumbnail:
                store.put("thumbs", doc["checksum"], ".webp", result.thumbnail)

        now = lib.get(doc_id)  # someone may have edited it while OCR ran; never overwrite their changes
        if not now["doc_date"]:
            found = find_date(result.text, settings.get(con, "date_order"))
            if found:
                fields["doc_date"] = found.isoformat()
        if job["payload"].get("auto_title") and now["title"] == job["payload"]["auto_title"]:
            title = title_from_text(result.text)
            if title:
                fields["title"] = title
        lib.update(doc_id, by="system", **fields)
        for step in AFTER_CONSUME:
            step(con, doc_id)

    return consume


def _look_for_codes(con: sqlite3.Connection, doc: dict) -> bool:
    """QR codes are looked for once: not again in the parts of a split stack, nor when it's read again."""
    if not settings.get(con, "barcodes"):
        return False
    row = con.execute("SELECT source FROM uploads WHERE document_id = ? ORDER BY created_at LIMIT 1",
                      (doc["id"],)).fetchone()
    if row and row[0] in ("split", "edit"):
        return False
    return not con.execute("SELECT 1 FROM audit_log WHERE document_id = ? AND action = 'barcodes'",
                           (doc["id"],)).fetchone()


def _barcodes(con, store: FileStore, config: Config, lib: Library, doc: dict, original: Path) -> bool:
    """Separator sheets and paper-number labels (barcodes.py). True when the document was a stack and has
    been split into new documents (it then goes to the trash)."""
    import io
    import pikepdf
    from . import barcodes, intake, papers
    from .pageedit import _pdf_bytes
    try:
        with pikepdf.open(original) as pdf:
            if len(pdf.pages) > barcodes.MAX_PAGES:
                return False
        codes = barcodes.page_codes(original)
    except Exception as e:   # never let this stop the document being read
        log.warning("couldn't look for QR codes in document %d: %s", doc["id"], e)
        return False
    with transaction(con):
        con.execute("INSERT INTO audit_log(document_id, action, detail) VALUES (?, 'barcodes', ?)",
                    (doc["id"], json.dumps({"pages_with_codes": sum(1 for c in codes if c)})))

    def free(n):
        try:
            papers.validate(n)
            papers.check_free(con, n, exclude_doc=doc["id"])
            return True
        except ValueError:
            return False

    stretches = barcodes.parts(codes)
    if not barcodes.has_separator(codes) or not stretches:
        n = barcodes.paper_number(codes[0]) if codes else None
        if n and doc["paper_no"] is None and free(n):
            lib.update(doc["id"], by="system", paper_no=n)
        return False
    stack_paper = doc["paper_no"]
    if stack_paper is not None:   # claimed at upload ("Keeping the paper?"): it goes to the first part
        lib.update(doc["id"], by="system", paper_no=None)
    tags = [t["id"] for t in doc["tags"]]
    made = []
    with pikepdf.open(original) as pdf:
        for i, pages in enumerate(stretches):
            n = barcodes.paper_number(codes[pages[0]]) or (stack_paper if i == 0 else None)
            up = intake.ingest(con, store, io.BytesIO(_pdf_bytes(pdf, [(p, 0) for p in pages])),
                               doc["original_name"], source="split", max_bytes=config.max_upload_bytes,
                               user_id=doc["owner_id"], tag_ids=tags, correspondent_id=doc["correspondent_id"],
                               document_type_id=doc["document_type_id"],
                               paper_no=n if n is not None and free(n) else None)
            if up.outcome == "accepted":
                made.append(up.document_id)
    if not made:
        if stack_paper is not None:
            lib.update(doc["id"], by="system", paper_no=stack_paper)
        return False
    lib.update(doc["id"], by="system", status="ready",
               error=f"Split at its separator sheets into {len(made)} documents: "
                     + ", ".join(f"#{i}" for i in made))
    lib.trash(doc["id"])
    log.info("split document %d at separator sheets into %s", doc["id"], made)
    return True


def _failed(lib: Library, doc_id: int, message: str) -> None:
    lib.update(doc_id, by="system", status="failed", error=message)
