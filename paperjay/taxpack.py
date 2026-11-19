"""The tax pack: every document a household needs for one tax year, as one zip for the accountant (or for
filling in the return yourself).

    <zip>/README.txt                          what's inside, and the totals per category
    <zip>/summary.csv                         one row per document: date, title, company, amount, file…
    <zip>/<Category>/<date> <title> [<id>].pdf  the searchable PDF (or the original when there is none)

Which documents: those dated in the tax year that are in a chosen category, have a chosen topic or carry a
chosen tag. The tax year starts on the day set in Settings (`tax_year_start`, "01-01" by default; "04-06"
in the UK, "07-01" in Australia). Tax forms for the year (W-2, 1099, a tax return…) usually arrive a few
weeks after it ends, so a tax form dated up to four months later counts for the year its text names.

Amounts come from each document's profile, as for totals in Ask: they are listed for every document, but a
category's total only adds up real spending (not copies, price lists, pay or tax forms), and says so.
"""
import csv
import io
import re
import sqlite3
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

from . import settings
from .export import safe_name
from .store import FileStore
from .totals import NOT_SPENDING, SKIP_KINDS

DEFAULT_CATEGORIES = ["tax", "income", "medical"]
DEFAULT_TOPICS = ["donation", "mortgage", "childcare", "school", "investment", "tax payment", "w-2", "1099",
                  "tax return", "health insurance"]
LATE_FORM_MONTHS = 4
CSV_COLUMNS = ["Date", "Title", "Company", "Type", "Category", "Amount", "Added to total", "Note",
               "Paper number", "File", "PaperJay id"]


def parse_start(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d\d)-(\d\d)", str(value or "").strip())
    if not m:
        raise ValueError("tax_year_start must look like 01-01 (month-day)")
    month, day = int(m.group(1)), int(m.group(2))
    try:
        date(2001, month, day)   # not a leap year: 02-29 is refused
    except ValueError:
        raise ValueError("tax_year_start must be a real day of the year, like 04-06")
    return month, day


def period(year: int, start: str = "01-01") -> tuple[date, date, str]:
    """The tax year that starts in `year`: first day, last day, and how people write it ("2025", or
    "2025–26" when it doesn't start on January 1)."""
    month, day = parse_start(start)
    first = date(year, month, day)
    last = date(year + 1, month, day) - timedelta(days=1)
    label = str(year) if (month, day) == (1, 1) else f"{year}–{str(year + 1)[2:]}"
    return first, last, label


def current_year(today: date, start: str = "01-01") -> int:
    """The tax year people are usually filing for: the last one that has ended."""
    month, day = parse_start(start)
    started = today.year if (today.month, today.day) >= (month, day) else today.year - 1
    return started - 1


def _mentions(text: str, year: int, label: str) -> bool:
    return bool(re.search(rf"(?<!\d){year}(?!\d)", text or "")) or (label != str(year) and label in (text or ""))


def gather(con: sqlite3.Connection, year: int, start: str | None = None) -> dict:
    """Every live document that belongs to the tax year, with what the page needs to choose among them."""
    from . import profile
    start = start or settings.get(con, "tax_year_start")
    first, last, label = period(year, start)
    late = (last + timedelta(days=1)).replace(day=1)
    late_until = date(late.year + (late.month - 1 + LATE_FORM_MONTHS) // 12,
                      (late.month - 1 + LATE_FORM_MONTHS) % 12 + 1, 1) - timedelta(days=1)
    rows = con.execute(
        "SELECT d.id, d.title, COALESCE(d.doc_date, substr(d.added_at, 1, 10)) AS day, d.category, d.paper_no, "
        "d.content, d.checksum, d.original_ext, d.archive_checksum, c.name AS company, t.name AS type "
        "FROM documents d LEFT JOIN correspondents c ON c.id = d.correspondent_id "
        "LEFT JOIN document_types t ON t.id = d.document_type_id "
        "WHERE d.deleted_at IS NULL AND d.status = 'ready' "
        "AND COALESCE(d.doc_date, substr(d.added_at, 1, 10)) BETWEEN ? AND ? ORDER BY day, d.id",
        (first.isoformat(), late_until.isoformat())).fetchall()
    if not rows:
        return {"year": year, "label": label, "from": first.isoformat(), "to": last.isoformat(), "documents": []}
    ids = [r["id"] for r in rows]
    marks = ", ".join("?" * len(ids))
    topics, tags = {}, {}
    for r in con.execute(f"SELECT document_id, topic FROM document_topics WHERE document_id IN ({marks})", ids):
        topics.setdefault(r[0], []).append(r[1])
    for r in con.execute(f"SELECT document_id, tag_id FROM document_tags WHERE document_id IN ({marks})", ids):
        tags.setdefault(r[0], []).append(r[1])
    profiles = profile.current_profiles(con)
    docs = []
    for r in rows:
        prof = profiles.get(r["id"]) or {}
        kind = prof.get("kind")
        day = date.fromisoformat(r["day"])
        late_form = day > last
        if late_form and not (kind == "tax" or r["category"] in ("tax", "income")) \
                or late_form and not _mentions(r["content"], year, label):
            continue
        amount = prof.get("total")
        note, counted = "", False
        if amount is not None:
            if prof.get("duplicate_of"):
                note = "a copy of another document"
            elif kind in SKIP_KINDS or r["category"] in ("tax", "income"):
                note = "a tax form, pay or insurance record: not spending"
            elif NOT_SPENDING.search(r["content"] or ""):
                note = "a price list or estimate, not a payment"
            else:
                counted = True
        if late_form:
            note = (note + "; " if note else "") + f"arrived after the tax year, names {label}"
        docs.append({"id": r["id"], "title": r["title"], "date": r["day"], "company": r["company"],
                     "type": r["type"], "category": r["category"] or "other", "topics": topics.get(r["id"], []),
                     "tags": tags.get(r["id"], []), "amount": amount, "counted": counted, "note": note,
                     "paper_no": r["paper_no"], "late_form": late_form,
                     "_file": ("archive", r["checksum"], ".pdf") if r["archive_checksum"]
                     else ("originals", r["checksum"], r["original_ext"])})
    return {"year": year, "label": label, "from": first.isoformat(), "to": last.isoformat(), "documents": docs}


def choose(docs: list[dict], categories=(), topics=(), tag_ids=()) -> list[dict]:
    """The documents matching any chosen category, topic or tag, each with its `group`: the folder it goes
    in and the line it's added up on. That's its category, except that a document filed under "other"
    goes under the chosen topic it has (a donation receipt in "Donation", not "Other")."""
    categories, topics, tag_ids = set(categories), list(topics), {int(t) for t in tag_ids}
    out = []
    for d in docs:
        mine = [t for t in topics if t in d["topics"]]
        if d["category"] in categories or mine or tag_ids & set(d["tags"]):
            group = d["category"] if d["category"] != "other" or not mine else mine[0]
            out.append({**d, "group": group})
    return out


def totals(docs: list[dict]) -> list[dict]:
    """Per category: how many documents, and the sum of the amounts that are spending."""
    out: dict[str, dict] = {}
    for d in docs:
        g = d.get("group", d["category"])
        t = out.setdefault(g, {"category": g, "documents": 0, "total": 0.0, "counted": 0})
        t["documents"] += 1
        if d["counted"]:
            t["total"] = round(t["total"] + d["amount"], 2)
            t["counted"] += 1
    return sorted(out.values(), key=lambda t: t["category"])


def public(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def cell(value) -> str:
    """A spreadsheet cell that can't run as a formula (CSV injection): text starting with = + - @ gets a
    leading apostrophe, which spreadsheets show as plain text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    s = str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def write_csv(rows: list[list], header: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        w.writerow([cell(v) for v in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # the BOM makes Excel read it as UTF-8


def folder_name(category: str) -> str:
    return (category or "other").replace("_", " ").title()


def build_zip(con: sqlite3.Connection, store: FileStore, out: Path, year: int, categories=(), topics=(),
              tag_ids=(), today: date | None = None) -> dict:
    pack = gather(con, year)
    docs = choose(pack["documents"], categories, topics, tag_ids)
    if not docs:
        raise ValueError(f"no documents for the tax year {pack['label']} with these choices")
    root = safe_name(f"PaperJay tax pack {pack['label']}")
    rows, used = [], set()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in docs:
            kind, checksum, ext = d["_file"]
            src = store.path(kind, checksum, ext)
            name = f"{folder_name(d['group'])}/{d['date']} {safe_name(d['title'])} [{d['id']}]{ext}"
            if name in used:   # never twice in one zip
                continue
            used.add(name)
            if Path(src).exists():
                z.write(src, f"{root}/{name}")
            else:
                name = ""
            rows.append([d["date"], d["title"], d["company"], d["type"], folder_name(d["group"]),
                         d["amount"], d["counted"] if d["amount"] is not None else None, d["note"],
                         d["paper_no"], name or "(file missing)", d["id"]])
        z.writestr(f"{root}/summary.csv", write_csv(rows, CSV_COLUMNS))
        z.writestr(f"{root}/README.txt", readme(pack, docs, today or date.today()))
    return {"label": pack["label"], "documents": len(docs), "file": out}


def readme(pack: dict, docs: list[dict], today: date) -> str:
    lines = [f"PaperJay tax pack: {pack['label']}",
             f"Tax year {pack['from']} to {pack['to']}, made on {today.isoformat()}.", "",
             f"{len(docs)} document(s), one folder per category. summary.csv lists every document with its",
             "date, company, amount and the file in this folder; it opens in any spreadsheet.", "",
             "Totals per category (only payments are added up; copies, price lists, pay and tax forms are",
             "listed but not added; check every amount against its document before you rely on it):", ""]
    for t in totals(docs):
        added = f"total of {t['counted']} payment(s): {t['total']:.2f}" if t["counted"] else "no payments added"
        lines.append(f"  {folder_name(t['category']):<16} {t['documents']:>4} document(s)   {added}")
    paper = [d for d in docs if d["paper_no"]]
    if paper:
        lines += ["", f"{len(paper)} of these have a paper original on the shelf; summary.csv gives each one's number."]
    return "\n".join(lines) + "\n"


def list_csv(docs: list[dict], field_names: list[str] = (), values: dict | None = None) -> bytes:
    """The Library's "Download list": the documents as a spreadsheet, with a column per own field
    (values: {document id: {field name: value}})."""
    header = ["Date", "Title", "Company", "Type", "Category", "Tags", "Paper number", "Pages", "Added",
              "Needs review", "PaperJay id", *field_names]
    values = values or {}
    rows = [[d["doc_date"] or "", d["title"], d["correspondent"], d["document_type"], d["category"],
             ", ".join(t["name"] for t in d["tags"]), d["paper_no"], d["page_count"], (d["added_at"] or "")[:10],
             d["needs_review"], d["id"], *[values.get(d["id"], {}).get(n) for n in field_names]] for d in docs]
    return write_csv(rows, header)


def stamp() -> str:
    return time.strftime("%Y-%m-%d-%H%M%S")
