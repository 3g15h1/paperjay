"""Paper numbers (spec Phase D): the number written on the physical original, so a
document on the shelf can be found from the app and a document in the app can be found
on the shelf.

Rules (spec §Phase D):
- The next free number is max(documents.paper_no, paper_reservations.last) + 1, so a
  number that was printed on a blank-ahead label sheet is never handed out twice.
- A number belongs to at most one document (partial unique index, migration 0008);
  claiming one inside the add transaction means two uploads at the same moment can
  never take the same number.
- A reserved number (printed, no document yet) may be typed at upload and is then
  claimed exactly once — after that it is a used number like any other, and typing it
  again gets a clear refusal, never a silent steal.
"""
import re
import sqlite3

from .db import get_setting, set_setting, transaction

# How Paperless imports before 0.4.2 recorded an archive serial number (a note line).
ASN_NOTE = re.compile(r"Archive serial number:\s*(\d+)")


def validate(n) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("paper number must be a whole number, 1 or more")
    return n


def next_free(con: sqlite3.Connection) -> int:
    """The next number to hand out: past every used number and every reserved range."""
    used = con.execute("SELECT MAX(paper_no) FROM documents").fetchone()[0] or 0
    reserved = con.execute("SELECT MAX(last) FROM paper_reservations").fetchone()[0] or 0
    return max(used, reserved) + 1


def holder(con: sqlite3.Connection, n: int, exclude_doc: int | None = None) -> int | None:
    """The document that owns paper number n, if any."""
    row = con.execute("SELECT id FROM documents WHERE paper_no = ?", (n,)).fetchone()
    return row[0] if row and row[0] != exclude_doc else None


def check_free(con: sqlite3.Connection, n: int, exclude_doc: int | None = None) -> None:
    other = holder(con, n, exclude_doc)
    if other is not None:
        raise ValueError(f"paper number {n} already belongs to document {other}")


def is_reserved(con: sqlite3.Connection, n: int) -> bool:
    return con.execute("SELECT 1 FROM paper_reservations WHERE ? BETWEEN first AND last",
                       (n,)).fetchone() is not None


def reserve_blanks(con: sqlite3.Connection, first: int, last: int,
                   user_id: int | None = None) -> list[tuple[int, int]]:
    """Record that the blank numbers in [first, last] were printed on a label sheet
    (Phase D task 6). Only spans that are neither used by a document nor already
    reserved are inserted, in one transaction; returns the spans reserved."""
    used = {r[0] for r in con.execute(
        "SELECT paper_no FROM documents WHERE paper_no BETWEEN ? AND ?", (first, last))}
    taken = [(r[0], r[1]) for r in con.execute(
        "SELECT first, last FROM paper_reservations WHERE last >= ? AND first <= ?", (first, last))]
    spans, start = [], None
    for n in range(first, last + 1):
        blocked = n in used or any(a <= n <= b for a, b in taken)
        if blocked:
            if start is not None:
                spans.append((start, n - 1))
                start = None
        elif start is None:
            start = n
    if start is not None:
        spans.append((start, last))
    with transaction(con):
        for a, b in spans:
            con.execute("INSERT INTO paper_reservations (first, last, user_id) VALUES (?, ?, ?)",
                        (a, b, user_id))
    return spans


ASN_BACKFILL_FLAG = "paper_asn_backfill_done"  # settings-table stamp: the step below runs once


def backfill_asn_notes(con: sqlite3.Connection) -> int:
    """Phase D upgrade step: imports before 0.4.2 stored a Paperless archive serial
    number as a note ('Archive serial number: N') instead of as the paper number.
    Give the number to the document when exactly one document claims it and it is
    not already used; the note stays. Returns how many documents were numbered.

    One-time (REVIEW-0.4.2 #3): the step stamps ASN_BACKFILL_FLAG when it has run
    and never runs again. Even on that one run it skips a document that has ever
    had a paper number assigned — paper_no is then among its person-touched fields
    (the audit history a person's clear leaves behind) — so a number a person
    deliberately cleared is never re-derived behind their back."""
    from .library import Library  # lazy: library imports this module
    if get_setting(con, ASN_BACKFILL_FLAG):
        return 0
    lib = Library(con)
    claims: dict[int, set[int]] = {}
    for r in con.execute(
            "SELECT n.document_id, n.body FROM notes n JOIN documents d ON d.id = n.document_id "
            "WHERE d.paper_no IS NULL AND d.deleted_at IS NULL"):
        for m in ASN_NOTE.finditer(r[1] or ""):
            claims.setdefault(int(m.group(1)), set()).add(r[0])
    done = 0
    for n, doc_ids in sorted(claims.items()):
        if len(doc_ids) != 1 or holder(con, n) is not None:
            continue
        doc_id = next(iter(doc_ids))
        if "paper_no" in lib.person_fields(doc_id):
            continue  # numbered once and cleared on purpose: leave it cleared
        try:
            lib.update(doc_id, by="system", paper_no=n)
            done += 1
        except ValueError:
            pass
    with transaction(con):
        set_setting(con, ASN_BACKFILL_FLAG, "1")
    return done
