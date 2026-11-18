"""The 'consume' job: OCR one stored document and fill in its text, pages, archive PDF, thumbnail, date
and (for photos named IMG_1234 and the like) a title taken from its first line of text.

Later steps (AI filing, embeddings) register in AFTER_CONSUME and are queued when a document is ready.
"""
import logging
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable

from . import filetypes, jobs, ocr, settings
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


def _failed(lib: Library, doc_id: int, message: str) -> None:
    lib.update(doc_id, by="system", status="failed", error=message)
