"""The A–Z index's storage (spec C.2): one `index_terms` row per (term, document) —
companies, ID numbers, people and topics, taken from the document's merged profile and
its filed company. Rows are rewritten whenever a document is refiled, and backfilled
for documents that predate the index. Queries join `deleted_at IS NULL`, so trashed
documents drop out without any row surgery; purged documents take their rows with
them (ON DELETE CASCADE).

A term's public identity is `key` (a short hash of kind + norm): links and the
reveal button use it, so a number's full value never has to appear in a URL or in
a list response to be opened.
"""
import hashlib
import hmac
import re
import secrets
import sqlite3

from .db import transaction

KINDS = ("company", "number", "person", "topic")
# the short labels the rule extractor uses, as a person reads them in the index
_ID_LABELS = {"account": "account number", "acct": "account number", "policy": "policy number",
              "member": "member number", "subscriber": "member number", "customer": "customer number",
              "group": "group number", "invoice": "invoice number", "order": "order number",
              "claim": "claim number", "reference": "reference number",
              "confirmation": "confirmation number", "vin": "VIN", "case": "case number"}


def squash(value: str) -> str:
    """The sort/prefix key: lower-case, everything but letters and digits removed."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


_term_secrets: dict[str, bytes] = {}


def _secret(con: sqlite3.Connection) -> bytes:
    """The per-install secret term keys are HMACed with. Generated once and kept in
    the settings table (a SECRET setting: masked in every settings response, never
    returned by any API). Cached per database file for the process; on a read-only
    copy of the database the generated secret simply lives for the process, which
    is enough there — keys are only ever compared within the same run."""
    path = ""
    try:
        row = con.execute("PRAGMA database_list").fetchone()
        path = row[2] if row else ""
    except Exception:
        pass
    if path not in _term_secrets:
        from . import settings as settings_mod
        value = settings_mod.get(con, "index_secret") or ""
        if not value:
            value = secrets.token_hex(32)
            try:
                settings_mod.save(con, {"index_secret": value})
            except Exception:
                pass   # a read-only copy: the secret lives for this process only
        _term_secrets[path] = str(value).encode()
    return _term_secrets[path]


def term_key(con: sqlite3.Connection, kind: str, norm: str) -> str:
    """A term's public key: an HMAC of kind + norm under the install's secret
    (review 0.4.0 #3). It used to be an unsalted sha1 of the value itself, which
    anyone could brute-force offline in seconds — account numbers are short and
    their last 4 are shown. Keys stay stable within an install."""
    return hmac.new(_secret(con), f"{kind}:{norm}".encode(), hashlib.sha256).hexdigest()[:16]


def resolve_key(con: sqlite3.Connection, kind: str, key: str) -> str | None:
    """The norm behind a term key, or None. Only this kind's norms are hashed, and
    the caller turns the norm into documents — the full value stays in the database."""
    for r in con.execute("SELECT DISTINCT norm FROM index_terms WHERE kind = ?", (kind,)):
        if term_key(con, kind, r[0]) == key:
            return r[0]
    return None


def mask(value: str) -> str:
    """What the index shows of a number: its last 4 characters (owner decision, C.2)."""
    return "••••" + value[-4:] if len(value) > 4 else "••••"


def id_label(raw: str) -> str:
    label = " ".join(str(raw or "").split()).lower().rstrip(":")
    return _ID_LABELS.get(label, label or "number")


def _has_table(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'index_terms'"
                       ).fetchone() is not None


def terms_for(doc: dict, prof: dict, topic_categories: dict | None = None) -> list[tuple]:
    """(kind, norm, term, label) rows for one document: its filed company, the profile's
    ID numbers and people, and its topics. Duplicates (same kind + norm) collapse."""
    out: dict[tuple, tuple] = {}

    def add(kind, term, label=""):
        term = " ".join(str(term or "").split())
        if not term:
            return
        norm = squash(term)
        if norm:
            out.setdefault((kind, norm), (kind, norm, term, label))

    if doc.get("correspondent"):
        add("company", doc["correspondent"])
    for i in prof.get("ids") or []:
        add("number", i.get("value"), id_label(i.get("label") or ""))
    for p in prof.get("people") or []:
        add("person", p.get("name"), str(p.get("label") or "").lower())
    for t in prof.get("topics") or []:
        add("topic", t, (topic_categories or {}).get(t, ""))
    return list(out.values())


def profile_for(con: sqlite3.Connection, doc_id: int):
    """The merged profile of one document, computed the way profile.refile computes it
    (stored cards + the person's own filing), plus the document row itself."""
    from . import profile as profile_mod
    from .library import Library
    lib = Library(con)
    doc = lib.get(doc_id)
    cards = profile_mod.get_cards(con, doc_id)
    locked = lib.person_fields(doc_id)
    prof = profile_mod.merge(cards, person=profile_mod._person_values(con, doc, locked),
                             correspondent_id=doc["correspondent_id"],
                             original_name=doc["original_name"])
    if not prof["vendor"] and doc["correspondent"]:
        prof["vendor"] = doc["correspondent"]
        prof["src"]["vendor"] = "label"
    return doc, prof


def reindex_document(con: sqlite3.Connection, doc_id: int, prof: dict | None = None,
                     doc: dict | None = None) -> None:
    """Rewrite one document's index terms from its merged profile. A document in the
    trash keeps no rows (its restore re-files it, which rebuilds them)."""
    if not _has_table(con):
        return  # a database from before migration 0008 (scripts open copies read-only)
    from . import settings as settings_mod
    from . import topics as topics_mod
    if doc is None or prof is None:
        doc, prof = profile_for(con, doc_id)
    cats = {t["name"]: t["category"]
            for t in topics_mod.all_topics(settings_mod.get(con, "custom_topics"))}
    rows = [] if doc.get("deleted_at") else terms_for(doc, prof, cats)
    with transaction(con):
        con.execute("DELETE FROM index_terms WHERE document_id = ?", (doc_id,))
        con.executemany("INSERT OR IGNORE INTO index_terms(kind, norm, term, label, document_id) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [(k, n, t, l, doc_id) for k, n, t, l in rows])


def backfill(con: sqlite3.Connection) -> int:
    """(Re)build the terms of every live document — the upgrade path for libraries that
    predate the index, and the repair for any drift. Returns how many were indexed."""
    if not _has_table(con):
        return 0
    ids = [r[0] for r in con.execute("SELECT id FROM documents WHERE deleted_at IS NULL ORDER BY id")]
    for doc_id in ids:
        reindex_document(con, doc_id)
    return len(ids)


def documents_for_term(con: sqlite3.Connection, kind: str, key: str) -> list[int]:
    """Live document ids filed under one index term, newest first."""
    norm = resolve_key(con, kind, key)
    if norm is None:
        return []
    return [r[0] for r in con.execute(
        "SELECT it.document_id FROM index_terms it JOIN documents d ON d.id = it.document_id "
        "WHERE it.kind = ? AND it.norm = ? AND d.deleted_at IS NULL "
        "ORDER BY COALESCE(d.doc_date, substr(d.added_at, 1, 10)) DESC, d.id DESC", (kind, norm))]


def az_entries(con: sqlite3.Connection, kind: str, letter: str | None = None, q: str = "",
               limit: int = 200, reveal_key: str | None = None, user_id: int | None = None) -> dict:
    """One section of the A–Z index: {entries: [{term, label, count, see, key, ...}]}.
    Numbers are masked; the single entry whose key is `reveal_key` comes back in full,
    and the reveal is written to the audit log (without the value)."""
    if kind not in KINDS:
        raise ValueError(f"index sections are {', '.join(KINDS)}")
    rows = con.execute(
        "SELECT it.norm AS norm, MIN(it.term) AS term, MIN(it.label) AS label, "
        "COUNT(*) AS count FROM index_terms it "
        "JOIN documents d ON d.id = it.document_id "
        "WHERE it.kind = ? AND d.deleted_at IS NULL GROUP BY it.norm", (kind,)).fetchall()
    entries = []
    for r in rows:
        entries.append({"term": r["term"], "label": r["label"] or "", "count": r["count"],
                        "see": None, "key": term_key(con, kind, r["norm"]), "_norm": r["norm"]})
    if kind == "company":
        counts = {e["_norm"]: e["count"] for e in entries}
        corr_norm = {}
        for r in con.execute("SELECT id, name FROM correspondents"):
            corr_norm[r["id"]] = squash(r["name"])
        for r in con.execute("SELECT a.alias AS alias, a.correspondent_id AS cid, c.name AS name "
                             "FROM correspondent_aliases a JOIN correspondents c ON c.id = a.correspondent_id"):
            if squash(r["alias"]) in counts:
                continue  # the alias *is* a filed company name in its own right
            entries.append({"term": r["alias"], "label": "", "see": r["name"],
                            "count": counts.get(corr_norm.get(r["cid"], ""), 0),
                            "key": term_key(con, "company", corr_norm.get(r["cid"], "")),
                            "_norm": squash(r["alias"])})
        ids_by_name = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM correspondents")}
        for e in entries:
            target = e["see"] or e["term"]
            if target in ids_by_name:
                e["correspondent_id"] = ids_by_name[target]
    q = (q or "").strip().lower()
    revealed = None
    for e in entries:
        if kind == "number":
            full = e["term"]
            e["masked"] = True
            e["term"] = mask(full)
            if reveal_key and e["key"] == reveal_key:
                e["term"], e["masked"] = full, False
                revealed = e
    if revealed is not None:
        from .library import Library
        Library(con)._audit(user_id, "reveal_number", None,
                            {"label": revealed["label"], "last4": revealed["_norm"][-4:]})
    if letter:
        want = letter.strip().upper()[:1]

        def letter_of(e):
            src = e["label"] if kind == "number" else e["term"]  # numbers sort under their label
            return src.upper()[:1] if src[:1].isalpha() else "#"

        entries = [e for e in entries if letter_of(e) == want]
    if q:
        qn = squash(q)
        # Deliberate (spec C.2, noted in review 0.4.0 #3): for numbers this matches q
        # against the full stored value (_norm), so part of a number finds its entry
        # without unmasking the list. That makes the search a yes/no oracle on a
        # number's digits — inherent to the spec'd behaviour, and accepted: anyone
        # who can search can already open the document. It does mean the
        # reveal_number audit entry is NOT a complete record of who saw a number.
        entries = [e for e in entries if q in e["term"].lower() or q in e["label"].lower()
                   or (qn and qn in e["_norm"])]
    if kind == "number":
        entries.sort(key=lambda e: (e["label"].lower(), e["_norm"][-4:], e["_norm"]))
    else:
        entries.sort(key=lambda e: e["term"].lower())
    entries = entries[:max(1, min(int(limit), 1000))]
    for e in entries:
        del e["_norm"]
    return {"kind": kind, "entries": entries}
