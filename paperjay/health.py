"""Filing health (spec Phase E): the "Tidy up" lists — documents whose filing has a gap or a
doubt a person can settle in one tap. Each list is a plain rule over the documents table and
the suggestions the last refile stored, so counting every list is one pass over the library.

Lists, in the order the page shows them:
    failed          couldn't be read (with the reason)
    no_company      no company           (suggestion: the company the text names, if any)
    no_date         no date              (every labelled date the text carries)
    no_type         no type              (suggestion: the type the text points to)
    file_name       the title is just the file name (suggestion: a title from the text)
    copies          possible copies of another document
    disagree        the sources disagree (the library / the AI / the text pattern)
    low_evidence    filed from a single source nobody has confirmed (company, type, date)
    confirm_import  imported filing the library layer doesn't learn from until confirmed (B.1)
"""
import calendar
import json
import re
import sqlite3
from functools import lru_cache

from . import rules, settings as settings_mod

LISTS = ("failed", "no_company", "no_date", "no_type", "file_name", "copies", "disagree",
         "low_evidence", "confirm_import")
# low evidence only for the fields a person files by: a lone-source title or summary is normal
# and would put nearly every document on the list
_LOW_FIELDS = ("vendor", "kind", "doc_date")
_DISAGREE_MARKS = (" says ", " suggests ")
# the file-name test is most of a count's cost on a big library, and titles rarely change
_file_name_title = lru_cache(maxsize=1 << 16)(rules.looks_like_filename)


def _rows(con: sqlite3.Connection) -> list[dict]:
    rows = []
    for r in con.execute(
            "SELECT d.id, d.title, d.original_name, d.status, d.error, d.doc_date, d.added_at, "
            "d.correspondent_id, d.document_type_id, d.suggestions, d.ai_fields, "
            "c.name AS correspondent, t.name AS document_type FROM documents d "
            "LEFT JOIN correspondents c ON c.id = d.correspondent_id "
            "LEFT JOIN document_types t ON t.id = d.document_type_id "
            "WHERE d.deleted_at IS NULL ORDER BY d.id DESC"):
        row = dict(r)
        row["suggestions"] = json.loads(row["suggestions"] or "{}")
        rows.append(row)
    return rows


def _import_state(con: sqlite3.Connection) -> tuple[set, set]:
    """(imported document ids, confirmed document ids) from the audit log."""
    imported, confirmed = set(), set()
    for r in con.execute("SELECT document_id, action FROM audit_log WHERE document_id IS NOT NULL "
                         "AND action IN ('import', 'confirm_filing')"):
        (imported if r[1] == "import" else confirmed).add(r[0])
    return imported, confirmed


def _ai_tagged(con: sqlite3.Connection) -> set:
    names = [str(t).lower() for t in settings_mod.get(con, "import_ai_tags")]
    if not names:
        return set()
    marks = ", ".join("?" * len(names))
    return {r[0] for r in con.execute(
        f"SELECT dt.document_id FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
        f"WHERE lower(t.name) IN ({marks})", names)}


def disagreements(row: dict) -> list[str]:
    sug = row["suggestions"]
    seen = set(sug.get("reviewed") or [])
    return [r for r in sug.get("review") or []
            if any(m in r for m in _DISAGREE_MARKS) and r not in seen]


def low_fields(row: dict) -> list[str]:
    """The person-filing fields that were applied from one unconfirmed source and are still
    what the document says (a field a person has since set leaves the list on refile)."""
    present = {"vendor": row["correspondent_id"], "kind": row["document_type_id"],
               "doc_date": row["doc_date"]}
    return [f for f in _LOW_FIELDS if f in (row["suggestions"].get("low_evidence") or [])
            and present[f] is not None]


def classify(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """Every list's documents (newest first), from one pass over the library."""
    out: dict[str, list[dict]] = {k: [] for k in LISTS}
    rows = _rows(con)
    live = {r["id"] for r in rows}
    imported, confirmed = _import_state(con)
    ai_tagged = _ai_tagged(con)
    trust_imported = bool(settings_mod.get(con, "trust_imported_filing"))
    for row in rows:
        if row["status"] == "failed":
            out["failed"].append(row)
            continue
        if row["status"] != "ready":
            continue  # still being read: nothing to tidy yet
        sug = row["suggestions"]
        if row["correspondent_id"] is None:
            out["no_company"].append(row)
        if row["doc_date"] is None:
            out["no_date"].append(row)
        if row["document_type_id"] is None:
            out["no_type"].append(row)
        if _file_name_title(row["title"] or "", row["original_name"] or ""):
            out["file_name"].append(row)
        if sug.get("duplicate_of") in live:
            out["copies"].append(row)
        if disagreements(row):
            out["disagree"].append(row)
        if low_fields(row):
            out["low_evidence"].append(row)
        filed = row["correspondent_id"] is not None or row["document_type_id"] is not None
        if row["id"] in imported and row["id"] not in confirmed and filed \
                and (not trust_imported or row["id"] in ai_tagged):
            out["confirm_import"].append(row)
    return out


def counts(con: sqlite3.Connection) -> dict:
    lists = classify(con)
    by_list = {k: len(v) for k, v in lists.items()}
    docs = {row["id"] for k, v in lists.items() for row in v}
    return {"counts": by_list, "documents": len(docs)}


def _dates(con: sqlite3.Connection, doc_id: int) -> tuple[list[dict], str | None]:
    """Every labelled date the document's cards found, and the one PaperJay would pick."""
    from . import indexing
    _, prof = indexing.profile_for(con, doc_id)
    pick = prof.get("doc_date") or (prof.get("suggestions") or {}).get("doc_date")
    return [{"label": d["label"], "date": d["date"], "picked": d["date"] == pick}
            for d in prof.get("dates") or []], pick


def _title_options(con: sqlite3.Connection, row: dict) -> list[str]:
    """Titles to offer for a file-name title, best first, each grounded in something the
    document already has: the title its cards read from the text; the file's own name when
    it reads like words (a scan's OCR title can be worse than "Reward certificate.pdf");
    and its company + type + month ("Acme Energy bill, Mar 2026")."""
    from . import indexing
    out: list[str] = []

    def add(title):
        title = " ".join(str(title or "").split())
        if title and title != row["title"] and title not in out \
                and not rules.looks_like_filename(title, row["original_name"] or ""):
            out.append(title)

    _, prof = indexing.profile_for(con, row["id"])
    add(prof.get("title"))
    stem = (row["original_name"] or "").rsplit(".", 1)[0].replace("_", " ")
    if len(re.findall(r"[A-Za-z]{3,}", stem)) >= 2 and not re.search(r"\d{5,}", stem):
        add(stem)
    if row["correspondent"] and row["document_type"]:
        built = f"{row['correspondent']} {row['document_type'].lower()}"
        if row["doc_date"]:
            y, m = row["doc_date"][:4], int(row["doc_date"][5:7])
            built += f", {calendar.month_abbr[m]} {y}"
        add(built)
    return out[:3]


def items(con: sqlite3.Connection, name: str, limit: int = 100) -> list[dict]:
    """One list, shaped for the page: each document with what PaperJay would suggest."""
    if name not in LISTS:
        raise ValueError(f"lists are {', '.join(LISTS)}")
    rows = classify(con)[name][:max(1, min(int(limit), 500))]
    titles = {}
    if name == "copies":
        ids = sorted({r["suggestions"]["duplicate_of"] for r in rows})
        if ids:
            marks = ", ".join("?" * len(ids))
            titles = {r[0]: r[1] for r in con.execute(
                f"SELECT id, title FROM documents WHERE id IN ({marks})", ids)}
    out = []
    for row in rows:
        sug = row["suggestions"]
        item = {"id": row["id"], "title": row["title"], "correspondent": row["correspondent"],
                "document_type": row["document_type"], "doc_date": row["doc_date"],
                "added_at": row["added_at"], "original_name": row["original_name"]}
        if name == "failed":
            item["error"] = row["error"] or "The text couldn't be read."
        elif name == "no_company":
            item["suggest"] = sug.get("correspondent")
        elif name == "no_type":
            item["suggest"] = sug.get("document_type")
        elif name == "no_date":
            item["dates"], item["suggest"] = _dates(con, row["id"])
        elif name == "file_name":
            item["options"] = _title_options(con, row)
            item["suggest"] = item["options"][0] if item["options"] else None
        elif name == "copies":
            orig = sug["duplicate_of"]
            item["copy_of"] = {"id": orig, "title": titles.get(orig) or f"Document {orig}"}
        elif name == "disagree":
            item["reasons"] = disagreements(row)
        elif name == "low_evidence":
            item["fields"] = low_fields(row)
            if "doc_date" in item["fields"]:
                item["dates"], _ = _dates(con, row["id"])
        out.append(item)
    return out
