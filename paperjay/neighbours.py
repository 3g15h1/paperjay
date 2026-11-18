"""Neighbours: the library layer (spec B.1). What the household's own filing says about a new
document, learned only from documents a person stands behind.

Trusted filing is per-field: a field counts when a person set it (at upload, by hand, or — for
imports — only when the household says the imported filing was done by hand:
`trust_imported_filing`, default false) and the AI has not changed it since. An imported
document carrying an `import_ai_tags` tag is never trusted until a person confirms its filing
(`Library.confirm_filing`). Signals, strongest first:

  1. a shared ID number with the same kind of label on both sides (account, policy, member,
     customer, group — invoice/order/claim/reference/confirmation numbers never link)
     → that document's correspondent and type, confidence 0.95;
  2. the same email sender address (0.9), or a sender domain mapped to one company (0.85);
     a domain maps only when ≥ 2 trusted documents from it agree ≥ 90 %, and free-mail,
     shared and no-reply domains never map. A sender alone suggests — it never creates a
     company (`vendor_may_file` stays False when only sender signals voted);
  3. text similarity: a BM25 query of the document's 30 highest-IDF terms against ask's
     index, the top 10 trusted neighbours at ≥ 0.3 × the best score → a weighted vote.

A vote applies when the winner holds ≥ 0.75 of the weight from ≥ 2 neighbours (tags: ≥ 0.6,
and a tag still needs evidence in the document's own text unless ≥ 3 neighbours with the
same vendor carry it). The card this returns holds only values that passed; profile.merge
treats a library value as decided (spec section 3).
"""
import json
import logging
import re
import sqlite3
from collections import Counter

from . import analyze, settings as settings_mod
from .library import FILING_FIELDS, Library, NotFound
from .textutil import normalize

log = logging.getLogger("paperjay")

NEIGHBOURS_VERSION = 1
APPLY_SHARE = 0.75
TAG_SHARE = 0.6
STRENGTH = {"id": 0.95, "sender": 0.9, "domain": 0.85, "similarity": 0.8}
# ID label kinds that may link two documents (spec B.1): the same kind on both sides,
# via rules.id_kind, so verbose labels ("Policy Number") and short ones mean the same kind.
FREE_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
                "icloud.com", "me.com", "yahoo.com", "aol.com", "proton.me", "protonmail.com",
                "gmx.com", "mail.com", "yandex.com", "zoho.com"}
_SIGNAL_WORDS = {"id": "shared account number", "sender": "same sender",
                 "domain": "same sender domain", "similarity": "similar text"}


# ---- trust ----

def _audit_facts(con: sqlite3.Connection, doc_id: int) -> dict:
    given, updated, released = set(), set(), set()
    imported = confirmed = False
    for r in con.execute("SELECT action, detail FROM audit_log WHERE document_id = ? ORDER BY id",
                         (doc_id,)):
        detail = json.loads(r["detail"])
        if r["action"] == "add":
            given |= set(detail.get("given", [])) & FILING_FIELDS
        elif r["action"] == "update":
            updated |= set(detail) & FILING_FIELDS
        elif r["action"] == "release":
            released |= set(detail.get("fields", [])) & FILING_FIELDS
        elif r["action"] == "import":
            imported = True
        elif r["action"] == "confirm_filing":
            confirmed = True
    return {"given": given, "updated": updated, "released": released,
            "imported": imported, "confirmed": confirmed}


def trusted_fields(con: sqlite3.Connection, doc: dict) -> set[str]:
    """The filing fields of `doc` the library layer may learn from (spec B.1)."""
    ai = set(doc["ai_fields"])
    facts = _audit_facts(con, doc["id"])
    ai_tags = {str(t).lower() for t in settings_mod.get(con, "import_ai_tags")}
    if {t["name"].lower() for t in doc["tags"]} & ai_tags and not facts["confirmed"]:
        return set()
    if facts["confirmed"]:
        return set(FILING_FIELDS) - ai
    person = (facts["given"] | facts["updated"]) - facts["released"]
    if facts["imported"] and not settings_mod.get(con, "trust_imported_filing"):
        # an import's `given` is the old system's filing, not a person's — only what a person
        # changed by hand after the import counts
        person = facts["updated"] - facts["released"]
    return person - ai


def _trusted_from_facts(facts: dict, ai: set, tag_names: set, trust_imported: bool,
                        ai_tags: set) -> set:
    """The trust rule (spec B.1) over pre-loaded audit facts for one document."""
    if tag_names & ai_tags and not facts["confirmed"]:
        return set()
    if facts["confirmed"]:
        return set(FILING_FIELDS) - ai
    person = (facts["given"] | facts["updated"]) - facts["released"]
    if facts["imported"] and not trust_imported:
        person = facts["updated"] - facts["released"]
    return person - ai


def trusted_documents(con: sqlite3.Connection, exclude: int | None = None) -> list[tuple[dict, set]]:
    """Every live, ready document with at least one trusted filing field, and those fields.
    One pass over the documents, their tags and the audit log: this runs inside every
    document's refile, so it must not re-read the library once per document."""
    trust_imported = bool(settings_mod.get(con, "trust_imported_filing"))
    ai_tags = {str(t).lower() for t in settings_mod.get(con, "import_ai_tags")}
    docs: dict[int, dict] = {}
    for r in con.execute(
            "SELECT d.id, d.ai_fields, d.content, d.status, d.deleted_at, c.name AS correspondent, "
            "t.name AS document_type FROM documents d "
            "LEFT JOIN correspondents c ON c.id = d.correspondent_id "
            "LEFT JOIN document_types t ON t.id = d.document_type_id "
            "WHERE d.deleted_at IS NULL AND d.status = 'ready' ORDER BY d.id"):
        doc = dict(r)
        doc["ai_fields"] = json.loads(doc["ai_fields"])
        doc["tags"] = []
        docs[doc["id"]] = doc
    for r in con.execute("SELECT dt.document_id, t.name FROM document_tags dt "
                         "JOIN tags t ON t.id = dt.tag_id"):
        if r[0] in docs:
            docs[r[0]]["tags"].append({"name": r[1]})
    facts: dict[int, dict] = {}
    for r in con.execute("SELECT document_id, action, detail FROM audit_log "
                         "WHERE document_id IS NOT NULL ORDER BY document_id, id"):
        f = facts.setdefault(r[0], {"given": set(), "updated": set(), "released": set(),
                                    "imported": False, "confirmed": False})
        detail = json.loads(r["detail"])
        if r["action"] == "add":
            f["given"] |= set(detail.get("given", [])) & FILING_FIELDS
        elif r["action"] == "update":
            f["updated"] |= set(detail) & FILING_FIELDS
        elif r["action"] == "release":
            f["released"] |= set(detail.get("fields", [])) & FILING_FIELDS
        elif r["action"] == "import":
            f["imported"] = True
        elif r["action"] == "confirm_filing":
            f["confirmed"] = True
    out = []
    for doc_id, doc in docs.items():
        if doc_id == exclude:
            continue
        f = facts.get(doc_id) or {"given": set(), "updated": set(), "released": set(),
                                  "imported": False, "confirmed": False}
        fields = _trusted_from_facts(f, set(doc["ai_fields"]),
                                     {t["name"].lower() for t in doc["tags"]},
                                     trust_imported, ai_tags)
        if fields:
            out.append((doc, fields))
    return out


def library_stats(con: sqlite3.Connection) -> dict:
    total = con.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL").fetchone()[0]
    return {"documents": total, "trusted": len(trusted_documents(con))}


# ---- senders ----

def sender_of(con: sqlite3.Connection, doc_id: int) -> str | None:
    row = con.execute("SELECT sender FROM uploads WHERE document_id = ? AND sender IS NOT NULL "
                      "AND sender != '' ORDER BY created_at DESC, id DESC LIMIT 1",
                      (doc_id,)).fetchone()
    return row[0] if row else None


def _senders(con: sqlite3.Connection) -> dict[int, str]:
    """The latest recorded sender per document, in one query."""
    out: dict[int, str] = {}
    for r in con.execute("SELECT document_id, sender FROM uploads WHERE document_id IS NOT NULL "
                         "AND sender IS NOT NULL AND sender != '' "
                         "ORDER BY created_at DESC, id DESC"):
        out.setdefault(r[0], r[1])
    return out


def _domain(sender: str) -> str:
    return sender.rsplit("@", 1)[-1].strip().lower().strip(">")


def _no_reply(sender: str) -> bool:
    local = sender.split("@", 1)[0].lower()
    return any(w in local for w in ("noreply", "no-reply", "no_reply", "donotreply"))


def _domain_map(trusted: list[tuple[dict, set]], senders: dict[int, str]) -> dict[str, dict]:
    """Sender domain → the one company trusted documents from it agree on (spec B.1 signal 2)."""
    by_domain: dict[str, list[tuple[int, str]]] = {}
    for doc, fields in trusted:
        if "correspondent_id" not in fields or not doc["correspondent"]:
            continue
        sender = senders.get(doc["id"])
        if not sender or _no_reply(sender):
            continue
        domain = _domain(sender)
        if not domain or domain in FREE_DOMAINS:
            continue
        by_domain.setdefault(domain, []).append((doc["id"], doc["correspondent"]))
    out = {}
    for domain, entries in by_domain.items():
        if len(entries) < 2:
            continue
        name, n = Counter(name for _, name in entries).most_common(1)[0]
        if n / len(entries) >= 0.9:
            out[domain] = {"name": name, "docs": [i for i, _ in entries]}
    return out


# ---- similarity (signal 3) ----

def _similarity(con: sqlite3.Connection, doc: dict, index, trusted: list) -> dict[int, float]:
    """BM25 ratios (best = 1.0) for the trusted documents most similar to `doc`, or {} when the
    index can't be trusted to describe this database (entries are verified by content hash)."""
    from . import ask as ask_mod
    idx = index if index is not None else ask_mod.INDEX
    target = idx.docs.get(doc["id"])
    if target is None or target.chash != analyze.content_hash(doc["content"] or ""):
        return {}
    terms = sorted(target.tf, key=lambda t: -idx.idf(t))[:30]
    if not terms:
        return {}
    asker = ask_mod.Asker(con, idx)
    weights = {t: 1.0 for t in terms}
    scored = []
    for other, _fields in trusted:
        od = idx.docs.get(other["id"])
        if od is None or od.chash != analyze.content_hash(other["content"] or ""):
            continue
        s = asker.bm25(od, weights)
        if s > 0:
            scored.append((other["id"], s))
    if not scored:
        return {}
    best = max(s for _, s in scored)
    top = sorted(scored, key=lambda kv: -kv[1])[:10]
    return {doc_id: s / best for doc_id, s in top if s >= 0.3 * best}


# ---- the vote ----

def _link_key(item: dict) -> tuple[str, str] | None:
    from .rules import id_kind
    kind = id_kind(str(item.get("label") or ""))
    value = analyze._squash(str(item.get("value") or ""))
    return (kind, value) if kind and len(value) >= 4 else None


def _kind_of(doc: dict) -> str:
    for kind, name in analyze.TYPE_NAMES.items():
        if name == doc["document_type"]:
            return kind
    return ""


def _why(name: str, voters: dict, signals: set) -> str:
    n = len(voters)
    words = ", ".join(_SIGNAL_WORDS[s] for s in ("id", "sender", "domain", "similarity") if s in signals)
    return f"{n} similar document{'s' if n != 1 else ''} filed as {name} ({words})"


def _tag_evidence(tag: str, norm_text: str) -> bool:
    from . import ask as ask_mod
    words = [w for w in re.findall(r"[^\W\d_]{3,}", tag.lower())]
    if not words:
        return False
    tokens = set(ask_mod.tokenize(norm_text))
    return all(ask_mod.stem(w) in tokens for w in words)


def _empty_card() -> dict:
    return {"kind": "", "category": "", "vendor": "", "title": "", "summary": "", "total": None,
            "dates": [], "doc_date": None, "ids": [], "people": [], "topics": [], "tags": [],
            "dropped": [], "conf": {}, "why": {}, "vendor_may_file": False,
            "version": NEIGHBOURS_VERSION}


def compute(con: sqlite3.Connection, doc_id: int, index=None) -> dict:
    """The library fact card for one document: the values the household's trusted filing
    decides for it (see the module docstring for the rules)."""
    from . import profile as profile_mod
    lib = Library(con)
    card = _empty_card()
    try:
        doc = lib.get(doc_id)
    except NotFound:
        return card
    if doc["deleted_at"] or doc["status"] != "ready" or not (doc["content"] or "").strip():
        return card
    trusted = trusted_documents(con, exclude=doc_id)
    if not trusted:
        return card
    own_links = set()
    for c in profile_mod.get_cards(con, doc_id).values():
        for i in c.get("ids") or []:
            k = _link_key(i)
            if k:
                own_links.add(k)
    senders = _senders(con)
    my_sender = senders.get(doc_id)
    dmap = _domain_map(trusted, senders)
    sim = _similarity(con, doc, index, trusted)

    # each trusted document votes with its strongest signal that connects it to this one
    voters: dict[int, tuple[float, float, str, dict, set]] = {}
    for other, fields in trusted:
        signals = []
        if own_links:
            their_links = set()
            for c in profile_mod.get_cards(con, other["id"]).values():
                for i in c.get("ids") or []:
                    k = _link_key(i)
                    if k:
                        their_links.add(k)
            if own_links & their_links:
                signals.append((STRENGTH["id"], STRENGTH["id"], "id"))
        their_sender = senders.get(other["id"])
        if my_sender and their_sender and my_sender.strip().lower() == their_sender.strip().lower():
            signals.append((STRENGTH["sender"], STRENGTH["sender"], "sender"))
        if other["id"] in sim:
            signals.append((STRENGTH["similarity"] * sim[other["id"]], STRENGTH["similarity"],
                            "similarity"))
        if signals:
            w, s, sig = max(signals)
            voters[other["id"]] = (w, s, sig, other, fields)
    # a mapped sender domain: its evidence documents vote for the mapped company
    if my_sender and not _no_reply(my_sender):
        hit = dmap.get(_domain(my_sender))
        if hit:
            by_id = {d["id"]: (d, f) for d, f in trusted}
            for ev_id in hit["docs"]:
                if ev_id in by_id and (ev_id not in voters
                                       or voters[ev_id][0] < STRENGTH["domain"]):
                    d, f = by_id[ev_id]
                    voters[ev_id] = (STRENGTH["domain"], STRENGTH["domain"], "domain", d, f)

    def run_vote(field: str, value_of) -> tuple[str, dict, float] | None:
        cand: dict[str, dict] = {}
        for vid, (w, s, sig, other, fields) in voters.items():
            if field not in fields:
                continue
            value = value_of(other)
            if not value:
                continue
            e = cand.setdefault(value, {"weight": 0.0, "voters": {}})
            e["weight"] += w
            e["voters"][vid] = (w, s, sig)
        if not cand:
            return None
        total = sum(e["weight"] for e in cand.values())
        winner, e = max(cand.items(), key=lambda kv: kv[1]["weight"])
        return winner, e, (e["weight"] / total if total else 0.0)

    for field, key, value_of in (
            ("correspondent_id", "vendor", lambda d: d["correspondent"]),
            ("document_type_id", "kind", _kind_of)):
        res = run_vote(field, value_of)
        if not res:
            continue
        winner, e, share = res
        signals = {v[2] for v in e["voters"].values()}
        if "id" in signals:
            decided, conf = True, STRENGTH["id"]
        elif share >= APPLY_SHARE and len(e["voters"]) >= 2:
            decided, conf = True, round(share * max(v[1] for v in e["voters"].values()), 3)
        else:
            decided, conf = False, 0.0
        if decided:
            card[key] = winner
            card["conf"][key] = conf
            card["why"][key] = _why(winner, e["voters"], signals)
            if key == "vendor":
                # sender evidence alone never creates a company (spec B.1)
                card["vendor_may_file"] = bool(signals - {"sender", "domain"})

    # tags: their own, lower bar, plus the text-evidence rule
    tag_votes: dict[str, dict[int, float]] = {}
    tag_total = 0.0
    for vid, (w, s, sig, other, fields) in voters.items():
        if "tags" not in fields:
            continue
        tag_total += w
        for t in other["tags"]:
            tag_votes.setdefault(t["name"], {})[vid] = w
    if tag_total:
        norm_text = normalize(f"{doc['title']}\n{doc['content']}")
        for name, vs in sorted(tag_votes.items()):
            share = sum(vs.values()) / tag_total
            if share < TAG_SHARE:
                continue
            same_vendor = Counter(voters[vid][3]["correspondent"] for vid in vs)
            three_same_vendor = any(n >= 3 for n in same_vendor.values())
            if three_same_vendor or _tag_evidence(name, norm_text):
                card["tags"].append(name)
                card["conf"]["tags"] = min(card["conf"].get("tags", 1.0), round(share, 3))
    return card


def top_filings(con: sqlite3.Connection, doc_id: int, index=None, limit: int = 3) -> list[dict]:
    """The filing of the most similar trusted neighbours, grouped by company — the 'Hints'
    block for the AI prompt (spec B.4): [{"vendor": "Xfinity", "count": 3, "kind": "bill"}]."""
    lib = Library(con)
    try:
        doc = lib.get(doc_id)
    except NotFound:
        return []
    trusted = trusted_documents(con, exclude=doc_id)
    sim = _similarity(con, doc, index, trusted)
    groups: dict[str, dict] = {}
    for other, fields in trusted:
        if other["id"] not in sim or "correspondent_id" not in fields or not other["correspondent"]:
            continue
        g = groups.setdefault(other["correspondent"],
                              {"vendor": other["correspondent"], "count": 0,
                               "kind": _kind_of(other), "weight": 0.0})
        g["count"] += 1
        g["weight"] += sim[other["id"]]
        g["kind"] = g["kind"] or _kind_of(other)
    out = sorted(groups.values(), key=lambda g: (-g["count"], -g["weight"]))[:limit]
    return [{"vendor": g["vendor"], "count": g["count"], "kind": g["kind"]} for g in out]
