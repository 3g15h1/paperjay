"""Fixing pages after a document was added: turn pages, take pages out, split one document into several,
join several into one.

PaperJay keeps every original byte for byte, so an edit never changes a file: it makes new document(s) from
the searchable PDF (the original, if that is a PDF and there is no searchable one yet), with the pages as
asked. The new documents keep the old one's filing (title, date, company, type, tags, category, notes,
keep and warranty choices) and its paper number, and are read again in the background (pages that already
have text keep it, so a page you turned stays turned). The old document goes to the Trash, where it can be
restored for 30 days.
"""
import io
import re
import uuid
import sqlite3
from pathlib import Path

import pikepdf

from . import intake
from .library import Library
from .store import FileStore

ROTATIONS = (0, 90, 180, 270)
MAX_PARTS = 50


class EditError(ValueError):
    pass


def source_pdf(store: FileStore, doc: dict) -> Path:
    if doc["deleted_at"]:
        raise EditError("this document is in the trash; restore it first")
    if doc["archive_checksum"]:
        return store.path("archive", doc["checksum"], ".pdf")
    if doc["mime_type"] == "application/pdf":
        return store.path("originals", doc["checksum"], doc["original_ext"])
    raise EditError("its pages can be changed once PaperJay has read it")


def _save(out: pikepdf.Pdf) -> bytes:
    # every edit is a new file, even one that puts the pages back the way an earlier document had them
    # (that one may be in the trash, and a file can only be stored once)
    out.docinfo["/PaperJayEdit"] = uuid.uuid4().hex
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def _pdf_bytes(src: pikepdf.Pdf, pages: list[tuple[int, int]]) -> bytes:
    out = pikepdf.Pdf.new()
    for index, turn in pages:
        out.pages.append(src.pages[index])
        if turn:
            out.pages[-1].rotate(turn, relative=True)
    return _save(out)


def plan(page_count: int, pages: list[dict], split_after=()) -> list[list[tuple[int, int]]]:
    """pages: [{"n": 1, "rotate": 90, "keep": true}, …] in the order wanted (every page, kept or not);
    split_after: page numbers after which a new document starts. Returns the parts as (index, turn) lists."""
    if not isinstance(pages, list) or not pages:
        raise EditError("say which pages to keep")
    seen, parts, cur = set(), [], []
    splits = {int(n) for n in split_after or ()}
    for p in pages:
        if not isinstance(p, dict):
            raise EditError("each page is {n, rotate, keep}")
        n, turn = p.get("n"), p.get("rotate", 0)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= page_count or n in seen:
            raise EditError(f"page numbers must be 1 to {page_count}, each once")
        if turn not in ROTATIONS:
            raise EditError("pages turn by 0, 90, 180 or 270 degrees")
        seen.add(n)
        if p.get("keep", True):
            cur.append((n - 1, turn))
        if n in splits and cur:
            parts.append(cur)
            cur = []
    if cur:
        parts.append(cur)
    if not parts:
        raise EditError("keep at least one page")
    if len(parts) > MAX_PARTS:
        raise EditError(f"at most {MAX_PARTS} documents at once")
    return parts


def _carry_over(con: sqlite3.Connection, lib: Library, new_id: int, old: list[dict], user_id) -> None:
    """The filing of the old document(s) goes to the new one: tags of all of them, everything else from the
    first, and every note."""
    first = old[0]
    fields = {k: first[k] for k in ("category", "keep_until", "warranty_until") if first.get(k)}
    if fields:
        lib.update(new_id, user_id=user_id, **fields)
    for d in old:
        for note in lib.notes(d["id"]):
            lib.add_note(new_id, note["body"], note["user_id"])


def _ingest(con, store, data: bytes, name: str, first: dict, user_id, max_bytes, title: str, tags,
            paper_no) -> int:
    up = intake.ingest(con, store, io.BytesIO(data), name, source="edit", max_bytes=max_bytes, user_id=user_id,
                       title=title, doc_date=first["doc_date"], tag_ids=tags,
                       correspondent_id=first["correspondent_id"], document_type_id=first["document_type_id"],
                       paper_no=paper_no)
    if up.outcome != "accepted":
        raise EditError(up.message or "the new document couldn't be added")
    return up.document_id


def _base_name(doc: dict) -> str:
    stem = Path(doc["original_name"] or "document").stem
    return f"{stem}.pdf"


def edit(con: sqlite3.Connection, store: FileStore, doc_id: int, pages: list[dict], split_after=(),
         user_id: int | None = None, max_bytes: int = 200 * 1024 * 1024) -> list[int]:
    """Turn, drop, reorder and split the pages of one document. Returns the new documents' ids."""
    lib = Library(con)
    doc = lib.get(doc_id)
    src_path = source_pdf(store, doc)
    with pikepdf.open(src_path) as src:
        parts = plan(len(src.pages), pages, split_after)
        if len(parts) == 1 and parts[0] == [(i, 0) for i in range(len(src.pages))]:
            raise EditError("nothing to change")
        blobs = [_pdf_bytes(src, part) for part in parts]
    return _replace(con, store, lib, [doc], blobs, user_id, max_bytes)


def merge(con: sqlite3.Connection, store: FileStore, ids: list[int], user_id: int | None = None,
          max_bytes: int = 200 * 1024 * 1024) -> int:
    """Join several documents into one, in the order given. Returns the new document's id."""
    if not isinstance(ids, list) or len(ids) < 2 or len(set(ids)) != len(ids):
        raise EditError("pick two or more different documents to join")
    if len(ids) > 100:
        raise EditError("at most 100 documents at once")
    lib = Library(con)
    docs = [lib.get(i) for i in ids]
    out = pikepdf.Pdf.new()
    opened = []
    try:
        for d in docs:
            src = pikepdf.open(source_pdf(store, d))
            opened.append(src)
            out.pages.extend(src.pages)
        data = _save(out)
    finally:
        for src in opened:
            src.close()
    return _replace(con, store, lib, docs, [data], user_id, max_bytes)[0]


def _replace(con, store, lib: Library, old: list[dict], blobs: list[bytes], user_id, max_bytes) -> list[int]:
    first = dict(old[0])
    if len(old) > 1:   # joining the parts of an earlier split: "Statement (part 1 of 2)" is "Statement" again
        first["title"] = re.sub(r" \(part \d+ of \d+\)$", "", first["title"]) or first["title"]
    tags = sorted({t["id"] for d in old for t in d["tags"]})
    paper_no = first["paper_no"]
    if paper_no is not None:   # the paper number moves to the (first) new document
        lib.update(first["id"], user_id=user_id, paper_no=None)
    new_ids = []
    try:
        for i, data in enumerate(blobs):
            title = first["title"] if len(blobs) == 1 else f"{first['title']} (part {i + 1} of {len(blobs)})"
            new_id = _ingest(con, store, data, _base_name(first), first, user_id, max_bytes, title, tags,
                             paper_no if i == 0 else None)
            new_ids.append(new_id)
            _carry_over(con, lib, new_id, old, user_id)
    except Exception:
        for new_id in new_ids:   # all or nothing: take back what was made, give the number back
            lib.trash(new_id, user_id)
            store.remove(*lib.purge(new_id, user_id))
        if paper_no is not None:
            lib.update(first["id"], user_id=user_id, paper_no=paper_no)
        raise
    for d in old:
        lib.trash(d["id"], user_id)
    return new_ids
