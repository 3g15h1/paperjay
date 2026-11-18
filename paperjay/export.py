"""Export everything to a plain folder: files you can open without PaperJay, plus one JSON file with all
the filing. Running it again into the same folder only copies what changed (fit for a nightly job to a NAS).

    <dest>/README.txt
    <dest>/manifest.json                       format "paperjay-export" version 1, see docs/export.md
    <dest>/documents/2025/2025-08-14 Acme Energy bill [12].jpg            the original, byte for byte
    <dest>/documents/2025/2025-08-14 Acme Energy bill [12].searchable.pdf the OCR'd PDF, if there is one

A document's files keep its id in [brackets], so a renamed document is moved, not copied twice. With
prune=True, files of documents that no longer exist are deleted from the folder.
"""
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

from . import __version__, analyze
from .library import Library
from .store import FileStore

FORMAT = "paperjay-export"
VERSION = 1
README = """This folder is an export from PaperJay (https://github.com/3g15h1/paperjay).

documents/<year>/<date> <title> [<id>].<ext>   each document's original file, exactly as it was added
documents/<year>/<…> [<id>].searchable.pdf     the searchable PDF PaperJay made from it, if any
manifest.json                                  titles, dates, companies, types, tags, notes, the text of
                                               every document and the facts the AI read, as JSON

Everything here can be opened without PaperJay. PaperJay can also import this folder again:
    paperjay import-export <this folder>
"""
_ID_IN_NAME = re.compile(r"\[(\d+)\](\.searchable)?\.[a-z0-9]+$")


def safe_name(title: str, limit: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title)
    name = " ".join(name.split()).strip(" .")
    return name[:limit].rstrip(" .") or "Untitled"


def _paths(doc: dict) -> tuple[str, str | None]:
    day = doc["doc_date"] or doc["added_at"][:10]
    base = f"documents/{day[:4]}/{day} {safe_name(doc['title'])} [{doc['id']}]"
    return base + doc["original_ext"], (base + ".searchable.pdf") if doc["archive_checksum"] else None


def _copy(src: Path, dest: Path) -> bool:
    """Copy unless an identical-looking file is already there. Returns True if it copied."""
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    return True


def manifest(con: sqlite3.Connection) -> dict:
    lib = Library(con)
    docs = []
    ids = [r[0] for r in con.execute("SELECT id FROM documents WHERE deleted_at IS NULL ORDER BY id")]
    for doc_id in ids:
        d = lib.get(doc_id)
        original, archive = _paths(d)
        docs.append({
            "id": d["id"], "title": d["title"], "date": d["doc_date"], "added": d["added_at"],
            "correspondent": d["correspondent"], "document_type": d["document_type"],
            "tags": [t["name"] for t in d["tags"]],
            "notes": [{"text": n["body"], "created": n["created_at"], "by": n["user"]} for n in lib.notes(doc_id)],
            "original": original, "original_name": d["original_name"], "mime_type": d["mime_type"],
            "sha256": d["checksum"], "searchable_pdf": archive, "pages": d["page_count"],
            "text": d["content"], "facts": analyze.get_card(con, doc_id, d["content"]),
            "paper_no": d["paper_no"],
        })
    return {
        "format": FORMAT, "version": VERSION, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "paperjay": __version__,
        "tags": [{"name": t["name"], "color": t["color"]} for t in lib.labels("tag")],
        "correspondents": [c["name"] for c in lib.labels("correspondent")],
        "document_types": [t["name"] for t in lib.labels("document_type")],
        "documents": docs,
    }


def export(con: sqlite3.Connection, store: FileStore, dest, prune: bool = False, archives: bool = True) -> dict:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    m = manifest(con)
    existing: dict[tuple[int, bool], Path] = {}
    for p in (dest / "documents").rglob("*") if (dest / "documents").exists() else []:
        hit = _ID_IN_NAME.search(p.name)
        if hit and p.is_file():
            existing[(int(hit.group(1)), bool(hit.group(2)))] = p
    copied = moved = 0
    wanted = set()
    for d in m["documents"]:
        row = con.execute("SELECT checksum, original_ext FROM documents WHERE id = ?", (d["id"],)).fetchone()
        jobs = [(False, d["original"], store.path("originals", row["checksum"], row["original_ext"]))]
        if archives and d["searchable_pdf"]:
            jobs.append((True, d["searchable_pdf"], store.path("archive", row["checksum"], ".pdf")))
        elif not archives:
            d["searchable_pdf"] = None
        for is_archive, rel, src in jobs:
            target = dest / rel
            wanted.add(target)
            old = existing.get((d["id"], is_archive))
            if old and old != target and not target.exists():  # renamed or re-dated: move, don't copy again
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(old, target)
                moved += 1
            if src.exists() and _copy(src, target):
                copied += 1
    removed = 0
    if prune:
        for old in existing.values():
            if old not in wanted and old.exists():
                old.unlink()
                removed += 1
    tmp = dest / "manifest.json.part"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1))
    os.replace(tmp, dest / "manifest.json")
    (dest / "README.txt").write_text(README)
    return {"documents": len(m["documents"]), "copied": copied, "moved": moved, "removed": removed, "folder": str(dest)}


def export_zip(con: sqlite3.Connection, store: FileStore, zip_path: Path) -> Path:
    """The same export as one .zip file (for the "Download everything" button)."""
    work = zip_path.with_suffix("")
    try:
        export(con, store, work)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            for p in sorted(work.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(work).as_posix())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return zip_path
