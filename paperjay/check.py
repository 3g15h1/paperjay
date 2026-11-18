"""Integrity check (`paperjay check`): do the files on disk match the database?

Reports, never repairs:
  missing   a document's original or archive PDF isn't on disk
  corrupt   a file's sha256 no longer matches what was stored when it was added
  orphan    a file on disk that no document refers to (e.g. left by a crash during purge)
Also runs SQLite's own integrity check and the full-text index's.
"""
import sqlite3

from .store import FileStore, sha256_file


def run(con: sqlite3.Connection, store: FileStore, verify_hashes: bool = True) -> dict:
    problems = []
    db_ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    if db_ok != "ok":
        problems.append({"kind": "database", "detail": db_ok})
    try:
        con.execute("INSERT INTO documents_fts(documents_fts, rank) VALUES ('integrity-check', 1)")
    except sqlite3.DatabaseError as e:
        problems.append({"kind": "search_index", "detail": str(e)})

    known_originals, known_derived, checked = set(), set(), 0
    for d in con.execute("SELECT id, checksum, original_ext, archive_checksum FROM documents ORDER BY id"):
        known_originals.add(d["checksum"])
        known_derived.add(d["checksum"])
        files = [("originals", d["original_ext"], d["checksum"])]
        if d["archive_checksum"]:
            files.append(("archive", ".pdf", d["archive_checksum"]))
        for kind, ext, expected in files:
            path = store.path(kind, d["checksum"], ext)
            if not path.exists():
                problems.append({"kind": "missing", "document_id": d["id"], "file": kind, "path": str(path)})
            elif verify_hashes:
                checked += 1
                if sha256_file(path) != expected:
                    problems.append({"kind": "corrupt", "document_id": d["id"], "file": kind, "path": str(path)})

    for kind, known in (("originals", known_originals), ("archive", known_derived), ("thumbs", known_derived)):
        for checksum, _ext, path in store.files(kind):
            if checksum not in known:
                problems.append({"kind": "orphan", "file": kind, "path": str(path)})

    return {"ok": not problems, "files_verified": checked, "problems": problems}
