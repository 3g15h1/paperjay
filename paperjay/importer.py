"""Bring documents in from Paperless-ngx (over its API) or from a PaperJay export folder.

Nothing is changed at the source. Each document keeps its id (when free), title, date, added date,
company, type, tags (with colours), notes, text, original file and searchable PDF; custom fields and
archive serial numbers become a note. The fields that had a value count as set by a person, so the AI
will never change them, but may fill in what's empty. Documents already here (same file) are skipped,
so an interrupted import can simply be run again.

Imported documents are ready at once (Paperless's text and searchable PDF are kept); only those without
any text are read with OCR. The AI then reads every document's facts in the background.
"""
import json
import logging
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import filetypes, jobs, ocr, papers
from .db import connect, transaction
from .library import Duplicate, Library
from .store import FileStore, clean_ext, sha256_file

log = logging.getLogger("paperjay")
TRANSPORT = None  # tests swap in an httpx.MockTransport

# ---- progress, shown in the app ----

_lock = threading.Lock()
PROGRESS: dict = {"state": "idle"}


def _progress(**kw) -> None:
    with _lock:
        PROGRESS.update(kw)


def _note_error(msg: str) -> None:
    with _lock:
        PROGRESS.setdefault("errors", []).append(msg)
        PROGRESS["errors"] = PROGRESS["errors"][-20:]
        PROGRESS["failed"] = PROGRESS.get("failed", 0) + 1


def progress() -> dict:
    with _lock:
        return json.loads(json.dumps(PROGRESS))


# ---- one document ----

def _utc(stamp: str | None) -> str | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def import_one(con: sqlite3.Connection, store: FileStore, meta: dict, original: Path, archive: Path | None,
               user_id: int | None, source: str) -> int | None:
    """meta: title, date, added, correspondent, document_type, tags [{name, color}], notes [text],
    content, original_name, mime_type, pages, id. Returns the new document id, or None if already here."""
    checksum = sha256_file(original)
    if con.execute("SELECT 1 FROM documents WHERE checksum = ?", (checksum,)).fetchone():
        return None
    ftype = filetypes.sniff(original)
    mime = ftype.mime if ftype else (meta.get("mime_type") or "application/octet-stream")
    ext = ftype.ext if ftype else clean_ext(meta.get("original_name") or ".bin")
    with open(original, "rb") as f:
        staged = store.stage(f)
    store.commit(staged, ext)

    lib = Library(con)
    wanted_id = meta.get("id")
    if wanted_id and con.execute("SELECT 1 FROM documents WHERE id = ?", (wanted_id,)).fetchone():
        wanted_id = None
    content = meta.get("content") or ""
    has_archive = archive is not None and archive.exists() and filetypes.sniff(archive) == filetypes.PDF
    ready = bool(content.strip()) or has_archive
    from .rules import looks_like_filename
    given = [f for f, v in (("title", meta.get("title")), ("doc_date", meta.get("date")),
                            ("correspondent_id", meta.get("correspondent")),
                            ("document_type_id", meta.get("document_type")), ("tags", meta.get("tags"))) if v]
    if "title" in given and looks_like_filename(meta.get("title") or "", meta.get("original_name") or ""):
        given.remove("title")  # a file name is not a person's title (spec A.3); the rules/AI may set one
    paper_no = meta.get("paper_no")
    if paper_no is not None:  # Phase D: an imported ASN keeps its number unless it's taken here
        try:
            papers.validate(paper_no)
            papers.check_free(con, paper_no)
            given.append("paper_no")
        except ValueError:
            paper_no = None  # the document still imports, unnumbered
    try:
        doc_id = lib.add(title=meta.get("title") or meta.get("original_name") or "Untitled", checksum=checksum,
                         original_name=meta.get("original_name") or original.name, original_ext=ext, mime_type=mime,
                         size=staged.size, doc_date=(meta.get("date") or None), content=content,
                         status="ready" if ready else "pending", doc_id=wanted_id,
                         added_at=_utc(meta.get("added")), user_id=user_id, given=given, paper_no=paper_no)
    except Duplicate:
        return None
    except ValueError:  # e.g. a date in a shape we don't accept: keep the document, drop the date
        doc_id = lib.add(title=meta.get("title") or "Untitled", checksum=checksum,
                         original_name=meta.get("original_name") or original.name, original_ext=ext, mime_type=mime,
                         size=staged.size, content=content, status="ready" if ready else "pending",
                         doc_id=wanted_id, user_id=user_id, given=[g for g in given if g != "doc_date"],
                         paper_no=paper_no)

    labels = {}
    if meta.get("correspondent"):
        labels["correspondent_id"] = lib.get_or_create_label("correspondent", meta["correspondent"], user_id)[0]
    if meta.get("document_type"):
        labels["document_type_id"] = lib.get_or_create_label("document_type", meta["document_type"], user_id)[0]
    tag_ids = []
    for t in meta.get("tags") or []:
        tid, created = lib.get_or_create_label("tag", t["name"], user_id)
        if created and t.get("color"):
            try:
                lib.set_tag_color(tid, t["color"])
            except ValueError:
                pass
        tag_ids.append(tid)
    if tag_ids:
        labels["tags"] = tag_ids
    if labels:
        lib.update(doc_id, user_id=user_id, by="system", **labels)
    from . import fields as fields_mod
    for name, value in (meta.get("fields") or {}).items():   # a PaperJay export: the household's own fields
        f = con.execute("SELECT id FROM custom_fields WHERE name = ?", (name,)).fetchone()
        if f and isinstance(value, str):
            try:
                fields_mod.set_value(con, doc_id, f[0], value, user_id)
            except ValueError:
                pass
    keeping = {k: meta[k] for k in ("keep_until", "shredded_at", "shredded_paper_no", "warranty_until")
               if meta.get(k) is not None}
    if keeping:   # a PaperJay export: how long to keep the paper, and whether it was shredded
        try:
            lib.update(doc_id, user_id=user_id, by="system", **keeping)
        except (ValueError, TypeError):
            pass
    for note in meta.get("notes") or []:
        if str(note).strip():
            lib.add_note(doc_id, str(note), user_id=user_id)

    fields = {"page_count": meta.get("pages")}
    with tempfile.TemporaryDirectory(dir=store.root / "tmp") as tmp:
        thumb_src = None
        if has_archive:
            work = Path(tmp) / "archive.pdf"
            shutil.copyfile(archive, work)
            fields["archive_checksum"] = sha256_file(work)
            store.put("archive", checksum, ".pdf", work)
            thumb_src = store.path("archive", checksum, ".pdf")
        elif mime == "application/pdf":
            thumb_src = store.path("originals", checksum, ext)
        try:
            if thumb_src:
                store.put("thumbs", checksum, ".webp", ocr.thumbnail(thumb_src, Path(tmp) / "t.webp"))
            elif ftype and ftype.kind == "image":
                from PIL import Image, ImageOps
                with Image.open(store.path("originals", checksum, ext)) as im:
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((600, 1800))
                    im.convert("RGB").save(Path(tmp) / "t.webp", "WEBP", quality=80)
                store.put("thumbs", checksum, ".webp", Path(tmp) / "t.webp")
        except Exception as e:
            log.info("no thumbnail for imported document %d: %s", doc_id, e)
    lib.update(doc_id, by="system", **{k: v for k, v in fields.items() if v is not None})
    if not ready and ftype:
        from .intake import CONSUME_JOB
        with transaction(con):
            jobs.enqueue(con, CONSUME_JOB, document_id=doc_id, priority=60)
    lib._audit(user_id, "import", doc_id, {"source": source})
    return doc_id


# ---- Paperless-ngx over its API ----

class Paperless:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.client = httpx.Client(transport=TRANSPORT, timeout=httpx.Timeout(120, connect=10),
                                   headers={"Authorization": f"Token {token.strip()}", "Accept": "application/json"})

    def close(self) -> None:
        self.client.close()

    def _get(self, path: str, **params) -> dict:
        r = self.client.get(path if path.startswith("http") else self.url + path, params=params or None)
        if r.status_code in (401, 403):
            raise PermissionError("Paperless refused the token (check it, and that its user can see documents)")
        r.raise_for_status()
        return r.json()

    def all(self, path: str) -> list[dict]:
        out, data = [], self._get(path, page_size=100)
        while True:
            out += data["results"]
            if not data.get("next"):
                return out
            data = self._get(data["next"])

    def names(self) -> dict:
        tags = {t["id"]: {"name": t["name"], "color": t.get("color") if str(t.get("color", "")).startswith("#") else None}
                for t in self.all("/api/tags/")}
        return {"tags": tags,
                "correspondents": {c["id"]: c["name"] for c in self.all("/api/correspondents/")},
                "document_types": {c["id"]: c["name"] for c in self.all("/api/document_types/")},
                "custom_fields": {c["id"]: c["name"] for c in self._safe_all("/api/custom_fields/")}}

    def _safe_all(self, path):
        try:
            return self.all(path)
        except httpx.HTTPStatusError:
            return []  # older Paperless without custom fields

    def download(self, doc_id: int, original: bool, dest: Path) -> Path:
        params = {"original": "true"} if original else None
        with self.client.stream("GET", f"{self.url}/api/documents/{doc_id}/download/", params=params) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        return dest


def paperless_meta(d: dict, names: dict) -> dict:
    notes = [n.get("note", "") for n in d.get("notes") or [] if isinstance(n, dict)]
    extra = []
    for cf in d.get("custom_fields") or []:
        if cf.get("value") not in (None, "", []):
            extra.append(f"{names['custom_fields'].get(cf.get('field'), 'Field')}: {cf['value']}")
    if extra:
        notes.append("Imported from Paperless-ngx\n" + "\n".join(extra))
    asn = d.get("archive_serial_number")  # Phase D: the ASN is the paper number now, not a note
    return {"id": d["id"], "title": d.get("title"), "date": (d.get("created_date") or d.get("created") or "")[:10] or None,
            "paper_no": asn if isinstance(asn, int) and not isinstance(asn, bool) and asn >= 1 else None,
            "added": d.get("added"), "correspondent": names["correspondents"].get(d.get("correspondent")),
            "document_type": names["document_types"].get(d.get("document_type")),
            "tags": [names["tags"][t] for t in d.get("tags") or [] if t in names["tags"]], "notes": notes,
            "content": d.get("content") or "", "original_name": d.get("original_file_name"),
            "mime_type": d.get("mime_type"), "pages": d.get("page_count"),
            "has_archive": bool(d.get("archived_file_name")), "checksum": _root_checksum(d)}


def _root_checksum(d: dict) -> str | None:
    for v in d.get("versions") or []:
        if v.get("is_root") and len(str(v.get("checksum", ""))) == 64:
            return v["checksum"]
    return None


def check_paperless(con: sqlite3.Connection, url: str, token: str) -> dict:
    """What an import would do, without importing anything."""
    pl = Paperless(url, token)
    try:
        names = pl.names()
        docs = [d for d in pl.all("/api/documents/") if not d.get("root_document")]
    finally:
        pl.close()
    have = {r[0] for r in con.execute("SELECT checksum FROM documents")}
    used_ids = {r[0] for r in con.execute("SELECT id FROM documents")}
    known = [d for d in docs if _root_checksum(d) in have]
    return {"documents": len(docs), "already_here": len(known),
            "with_searchable_pdf": sum(1 for d in docs if d.get("archived_file_name")),
            "without_text": sum(1 for d in docs if not (d.get("content") or "").strip()),
            "id_clashes": sum(1 for d in docs if d["id"] in used_ids and d not in known),
            "tags": len(names["tags"]), "correspondents": len(names["correspondents"]),
            "document_types": len(names["document_types"]),
            "notes": sum(len(d.get("notes") or []) for d in docs),
            "custom_fields_in_use": sum(1 for d in docs if d.get("custom_fields"))}


def import_paperless(db_path, store: FileStore, url: str, token: str, user_id: int | None = None) -> dict:
    con = connect(db_path)
    pl = Paperless(url, token)
    _progress(state="running", source="Paperless-ngx", started=time.time(), finished=None, total=0, done=0,
              imported=0, duplicates=0, failed=0, errors=[])
    try:
        names = pl.names()
        docs = [d for d in pl.all("/api/documents/") if not d.get("root_document")]
        _progress(total=len(docs))
        have = {r[0] for r in con.execute("SELECT checksum FROM documents")}
        for d in sorted(docs, key=lambda x: x["id"]):
            meta = paperless_meta(d, names)
            try:
                if meta["checksum"] and meta["checksum"] in have:
                    _progress(duplicates=PROGRESS["duplicates"] + 1)
                    continue
                with tempfile.TemporaryDirectory(dir=store.root / "tmp") as tmp:
                    original = pl.download(d["id"], True, Path(tmp) / "original")
                    archive = pl.download(d["id"], False, Path(tmp) / "archive") if meta["has_archive"] else None
                    new_id = import_one(con, store, meta, original, archive, user_id, "paperless-ngx")
                if new_id is None:
                    _progress(duplicates=PROGRESS["duplicates"] + 1)
                else:
                    _progress(imported=PROGRESS["imported"] + 1)
            except Exception as e:
                log.warning("import of Paperless document %s failed: %s", d.get("id"), e)
                _note_error(f"document {d.get('id')}: {type(e).__name__}: {e}")
            finally:
                _progress(done=PROGRESS["done"] + 1)
        _progress(state="done", finished=time.time())
    except Exception as e:
        log.exception("Paperless import stopped")
        _progress(state="failed", finished=time.time(), message=f"{type(e).__name__}: {e}")
    finally:
        pl.close()
        con.close()
    return progress()


# ---- a PaperJay export folder ----

def import_export(db_path, store: FileStore, folder, user_id: int | None = None) -> dict:
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest.get("format") != "paperjay-export":
        raise ValueError("that folder isn't a PaperJay export (no paperjay-export manifest.json)")
    con = connect(db_path)
    colors = {t["name"]: t.get("color") for t in manifest.get("tags", [])}
    docs = manifest["documents"]
    from . import fields as fields_mod
    for f in manifest.get("custom_fields") or []:   # the household's own fields, before their values
        try:
            if not con.execute("SELECT 1 FROM custom_fields WHERE name = ?", (f.get("name"),)).fetchone():
                fields_mod.create(con, f.get("name"), f.get("kind") or "text", f.get("fill") or "")
        except (ValueError, TypeError):
            pass
    _progress(state="running", source="PaperJay export", started=time.time(), finished=None, total=len(docs),
              done=0, imported=0, duplicates=0, failed=0, errors=[])
    try:
        for d in docs:
            try:
                original = (folder / d["original"]).resolve()
                if folder.resolve() not in original.parents:
                    raise ValueError("file outside the export folder")
                archive = (folder / d["searchable_pdf"]).resolve() if d.get("searchable_pdf") else None
                if archive is not None and folder.resolve() not in archive.parents:
                    archive = None
                meta = {"id": d["id"], "title": d["title"], "date": d.get("date"), "added": d.get("added"),
                        "correspondent": d.get("correspondent"), "document_type": d.get("document_type"),
                        "tags": [{"name": t, "color": colors.get(t)} for t in d.get("tags", [])],
                        "notes": [n["text"] for n in d.get("notes", [])], "content": d.get("text") or "",
                        "paper_no": d.get("paper_no"), "keep_until": d.get("keep_until"),
                        "shredded_at": d.get("shredded_at"), "shredded_paper_no": d.get("shredded_paper_no"),
                        "warranty_until": d.get("warranty_until"), "fields": d.get("fields") or {},
                        "original_name": d.get("original_name"), "mime_type": d.get("mime_type"), "pages": d.get("pages")}
                new_id = import_one(con, store, meta, original, archive, user_id, "paperjay-export")
                _progress(**({"imported": PROGRESS["imported"] + 1} if new_id else {"duplicates": PROGRESS["duplicates"] + 1}))
            except Exception as e:
                _note_error(f"document {d.get('id')}: {type(e).__name__}: {e}")
            finally:
                _progress(done=PROGRESS["done"] + 1)
        _progress(state="done", finished=time.time())
    finally:
        con.close()
    return progress()


def start(target, *args, **kwargs) -> bool:
    """Run an import in the background. False if one is already running."""
    with _lock:
        if PROGRESS.get("state") == "running":
            return False
        PROGRESS.clear()
        PROGRESS["state"] = "running"
    threading.Thread(target=target, args=args, kwargs=kwargs, name="paperjay-import", daemon=True).start()
    return True
