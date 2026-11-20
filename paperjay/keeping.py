"""How long to keep the paper, and the list of paper that can go ("Can I shred this?").

Each document's paper has a keep-until date: a person's own choice for that document (documents.keep_until),
or else the first keep rule that matches it. A rule matches by category, topic or kind of document and
keeps the paper for a number of years: counted from the end of the year the document is dated in (so a
2025 tax paper kept 7 years can go in 2033), or from the day it expires (an insurance policy: a year after
it ends). A document no rule matches is kept for good: PaperJay never suggests shredding something it
can't place.

The rules are a setting (`keep_rules`), with defaults that follow common household advice in the US.
Shredding only ever concerns the paper: the digital copy stays in PaperJay.
"""
import json
import sqlite3
from datetime import date

from . import settings
from .analyze import TYPE_NAMES
from .keep_rules import DEFAULT_RULES  # noqa: F401  (the defaults live apart: settings needs them)

FOREVER = "9999-12-31"
_KIND_OF_TYPE = {name.lower(): kind for kind, name in TYPE_NAMES.items()}


def validate_rules(value) -> list:
    if not isinstance(value, list):
        raise ValueError("keep_rules must be a list of rules")
    out = []
    for r in value:
        if not isinstance(r, dict) or not str(r.get("name", "")).strip():
            raise ValueError("each keep rule needs a name")
        years = r.get("years")
        if years is not None and (isinstance(years, bool) or not isinstance(years, int) or not 0 <= years <= 100):
            raise ValueError("a keep rule's years must be a whole number from 0 to 100, or empty for 'for good'")
        if r.get("from", "year_end") not in ("year_end", "expires"):
            raise ValueError("a keep rule counts from year_end or expires")
        lists = {}
        for k in ("categories", "kinds", "topics"):
            v = r.get(k) or []
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError(f"a keep rule's {k} must be a list of names")
            lists[k] = [x.strip().lower() for x in v if x.strip()]
        out.append({"name": str(r["name"]).strip()[:80], "years": years, "from": r.get("from", "year_end"), **lists})
    return out


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:   # February 29
        return d.replace(year=d.year + years, day=28)


def _chunks(ids: list[int], size: int = 500):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def _facts(con: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    """Per document: its kind and latest expiry date, from the stored cards."""
    out: dict[int, dict] = {}
    if not ids:
        return out
    rows = []
    for chunk in _chunks(ids):   # SQLite caps how many values one query may carry
        marks = ", ".join("?" * len(chunk))
        rows += con.execute(f"SELECT document_id, source, card FROM facts WHERE document_id IN ({marks}) "
                            "ORDER BY CASE source WHEN 'rules' THEN 0 WHEN 'library' THEN 1 ELSE 2 END",
                            chunk).fetchall()
    rows.sort(key=lambda r: {"rules": 0, "library": 1}.get(r["source"], 2))
    for r in rows:
        card = json.loads(r["card"])
        f = out.setdefault(r["document_id"], {"kind": None, "expires": None})
        if card.get("kind") and card["kind"] != "other":
            f["kind"] = card["kind"]          # later sources (the AI) win over the text patterns
        for d in card.get("dates") or []:
            if d.get("label") == "expires" and (not f["expires"] or d["date"] > f["expires"]):
                f["expires"] = d["date"]
    return out


def _rows(con: sqlite3.Connection, where: str = "", params=()) -> list[dict]:
    rows = [dict(r) for r in con.execute(
        "SELECT d.id, d.title, COALESCE(d.doc_date, substr(d.added_at, 1, 10)) AS day, d.category, d.paper_no, "
        "d.keep_until, d.shredded_at, d.shredded_paper_no, d.warranty_until, d.content, d.doc_date, "
        "c.name AS company, t.name AS type "
        "FROM documents d LEFT JOIN correspondents c ON c.id = d.correspondent_id "
        f"LEFT JOIN document_types t ON t.id = d.document_type_id WHERE d.deleted_at IS NULL {where} "
        "ORDER BY day, d.id", params)]
    ids = [r["id"] for r in rows]
    facts = _facts(con, ids)
    topics: dict[int, set] = {}
    for chunk in _chunks(ids):
        marks = ", ".join("?" * len(chunk))
        for r in con.execute(f"SELECT document_id, topic FROM document_topics WHERE document_id IN ({marks})", chunk):
            topics.setdefault(r[0], set()).add(r[1])
    from . import warranty
    order = settings.get(con, "date_order")
    for r in rows:
        w = warranty.end(r.pop("content"), r["doc_date"], r["warranty_until"], order)
        r["warranty"] = w["until"] if w else None
        f = facts.get(r["id"], {})
        r["kind"] = _KIND_OF_TYPE.get((r["type"] or "").lower()) or f.get("kind")
        r["expires"] = f.get("expires")
        r["topics"] = topics.get(r["id"], set())
    return rows


def decide(doc: dict, rules: list[dict]) -> dict:
    """{until: YYYY-MM-DD (FOREVER = keep for good), why: the rule's name or "your choice", rule: index|None}.
    A receipt under warranty is kept at least until the warranty ends (the proof of purchase)."""
    if doc.get("keep_until"):
        return {"until": doc["keep_until"], "why": "your choice", "rule": None}
    k = _by_rules(doc, rules)
    w = doc.get("warranty")
    if w and w > k["until"]:
        return {"until": w, "why": "under warranty" if w != FOREVER else "lifetime warranty", "rule": k["rule"]}
    return k


def _by_rules(doc: dict, rules: list[dict]) -> dict:
    for i, rule in enumerate(rules):
        if doc.get("category") in rule["categories"] or (doc.get("kind") or "") in rule["kinds"] \
                or doc.get("topics", set()) & set(rule["topics"]):
            if rule["years"] is None:
                return {"until": FOREVER, "why": rule["name"], "rule": i}
            day = date.fromisoformat(doc["day"])
            start = date(day.year, 12, 31)
            if rule["from"] == "expires" and doc.get("expires"):
                start = date.fromisoformat(doc["expires"])
            return {"until": _add_years(start, rule["years"]).isoformat(), "why": rule["name"], "rule": i}
    return {"until": FOREVER, "why": "no keep rule covers it", "rule": None}


def for_document(con: sqlite3.Connection, doc_id: int) -> dict | None:
    rows = _rows(con, "AND d.id = ?", (doc_id,))
    if not rows:
        return None
    r = rows[0]
    return {**decide(r, settings.get(con, "keep_rules")), "shredded_at": r["shredded_at"],
            "shredded_paper_no": r["shredded_paper_no"], "own": bool(r["keep_until"])}


def ready(con: sqlite3.Connection, today: date | None = None, shelf_only: bool = True) -> list[dict]:
    """Paper whose keep-until date has passed and that hasn't been shredded yet. shelf_only: just the
    papers with a number on the shelf (the ones PaperJay knows you kept)."""
    today = today or date.today()
    rules = settings.get(con, "keep_rules")
    where = "AND d.shredded_at IS NULL AND d.status = 'ready'" + (" AND d.paper_no IS NOT NULL" if shelf_only else "")
    out = []
    for r in _rows(con, where):
        k = decide(r, rules)
        if k["until"] < today.isoformat():
            out.append({"id": r["id"], "title": r["title"], "date": r["day"], "company": r["company"],
                        "type": r["type"], "paper_no": r["paper_no"], "until": k["until"], "why": k["why"]})
    out.sort(key=lambda x: (x["paper_no"] is None, x["paper_no"] or 0, x["date"]))
    return out


_COUNT: dict = {}
COUNT_FOR = 300   # seconds; cards and topics change without a document edit, so look again now and then


def count_ready(con: sqlite3.Connection, today: date | None = None) -> int:
    """For the start page and Settings, asked every 30 seconds by each open page: worked out again when a
    document or a setting changed, the day did, or after a few minutes."""
    import time
    from . import library
    today = today or date.today()
    key = (con.execute("PRAGMA database_list").fetchone()[2], library.CHANGES, today.isoformat(),
           json.dumps(settings.get(con, "keep_rules")), settings.get(con, "date_order"))
    if _COUNT.get("key") != key or time.monotonic() - _COUNT.get("at", 0) > COUNT_FOR:
        _COUNT.update(key=key, n=len(ready(con, today)), at=time.monotonic())
    return _COUNT["n"]
