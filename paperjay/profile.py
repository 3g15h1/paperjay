"""The document profile (spec section 3): one merged view of what the rules, the household's own
filing (library) and the AI each say about a document, with the source and confidence of every
field. Ask, totals, reminders and browse read the profile, never "was the AI on?".

Layers, cheapest first: person > library > rules > AI. A person's own filing always wins; a
strong library vote beats the text layers; rules and AI merge by confidence, with ties going to
the AI for wording (title, summary) and to the rules for exact values (amounts, dates, ids).
"""
import json
import re
import sqlite3

from . import analyze
from .db import get_setting, set_setting, transaction
from .rules import RULES_VERSION  # re-exported for the startup staleness check

__all__ = ["RULES_VERSION"]

SOURCES = ("person", "library", "rules", "ai")
# Disagreements and weak values only send a document to review for the fields a person files by;
# a shaky category, total or title is kept as a suggestion without asking anyone (review noise, 0.3.2).
REVIEW_FIELDS = ("vendor", "kind", "doc_date")
# Auto-apply bars for a value that only one source supports (spec section 3 / review R1).
BARS = {"vendor": 0.85, "kind": 0.85, "tags": 0.85, "doc_date": 0.8, "total": 0.8}
DEFAULT_BAR = 0.8
MIN_APPLY = 0.7  # a lone value below this is a suggestion, not a filing…
MIN_WORD = 0.6   # …except wording (title/summary), whose defined confidence is 0.6 (section 3)
_WORD_FIELDS = ("title", "summary")
_ID_PREFIX = {"account": "acct", "policy": "policy", "member": "member",
              "customer": "cust", "group": "group"}
_ID_PRIORITY = ("account", "policy", "member", "customer", "group")


def _bar(field: str) -> float:
    return BARS.get(field, DEFAULT_BAR)


def _card_conf(card: dict, field: str, present: bool) -> float:
    """A card's confidence for a field. AI cards carry no conf of their own: the spec's table
    (section 3) derives it from grounding — values exist on the card only because they were
    grounded, so labelled facts get the 'found next to a label' confidence."""
    conf = card.get("conf") or {}
    if field in conf:
        return float(conf[field])
    if not present:
        return 0.0
    if field in ("total", "dates", "doc_date", "ids"):
        return 0.8
    if field == "vendor":
        return 0.8
    if field in ("kind", "category"):
        return 0.7
    return 0.6


def _value(card: dict, field: str):
    if field == "doc_date":
        if card.get("doc_date"):
            return card["doc_date"]
        for label in analyze.DOC_DATE_LABELS:
            for d in card.get("dates") or []:
                if d["label"] == label:
                    return d["date"]
        return None
    v = card.get(field)
    if field in ("kind", "category") and v == "other":
        return None  # "other" says nothing; it must not outvote or contradict a real answer
    return v if v not in (None, "", []) else None


def series_key(ids: list[dict], vendor: str = "", kind: str = "", correspondent_id=None) -> str | None:
    """The series a document belongs to (spec section 3, prefixes as amended in section 14)."""
    from .rules import id_kind
    by_label: dict[str, str] = {}
    for i in ids or []:
        id_label = id_kind(str(i.get("label") or ""))
        if id_label and id_label not in by_label:
            by_label[id_label] = re.sub(r"[^a-z0-9]", "", str(i.get("value") or "").lower())
    for label in _ID_PRIORITY:
        if by_label.get(label):
            return f"{_ID_PREFIX[label]}:{by_label[label]}"
    if correspondent_id is not None:
        return f"vendor:{correspondent_id}:{kind or 'other'}"
    return None


def merge(cards: dict, person: dict | None = None, correspondent_id=None, original_name: str = "") -> dict:
    """Merge the per-source cards ({'rules': card, 'ai': card, 'library': card}) into one profile.
    `person`: {field: value} a person has set — always wins. `original_name`: the uploaded file's name,
    so a title that is only a file name ("Receipt48213377") never wins."""
    from . import rules as rules_mod
    from .rules import looks_like_filename
    person = person or {}
    out: dict = {"kind": "", "category": "other", "vendor": "", "title": "", "summary": "",
                 "total": None, "dates": [], "doc_date": None, "ids": [], "people": [], "topics": [],
                 "tags": [], "series": None, "duplicate_of": None, "src": {}, "conf": {}, "why": {},
                 "dropped": [], "suggestions": {}, "review": [], "low_evidence": []}
    for source in ("rules", "ai", "library"):
        card = cards.get(source) or {}
        for d in card.get("dropped") or []:
            if d not in out["dropped"]:
                out["dropped"].append(d)

    def decide(field: str):
        """Set out[field] from the layers. Returns nothing; records src/conf/review/suggestions."""
        if field in person and person[field] not in (None, ""):
            out[field] = person[field]
            out["src"][field], out["conf"][field] = "person", 1.0
            return
        lib = cards.get("library") or {}
        lib_val = _value(lib, field)
        lib_conf = _card_conf(lib, field, lib_val is not None)
        # the library card holds only votes that already passed (spec B.1: >= 0.75 share from
        # >= 2 neighbours, or a shared ID at 0.95), so a value on it is decided; its confidence
        # (share x signal strength) bottoms out at 0.75 x 0.8 = 0.6 for a similarity vote
        if lib_val is not None and lib_conf >= 0.6:
            out[field] = lib_val
            out["src"][field], out["conf"][field] = "library", lib_conf
            if lib.get("why", {}).get(field):
                out["why"][field] = lib["why"][field]
            ai_val = _value(cards.get("ai") or {}, field)
            if ai_val is not None and ai_val != lib_val and field in REVIEW_FIELDS:
                out["review"].append(f"{field}: the library filing says {lib_val!r}, the AI says {ai_val!r}")
            return
        vals = {}
        for source in ("rules", "ai"):
            card = cards.get(source) or {}
            v = _value(card, field)
            if field == "title" and v is not None and looks_like_filename(str(v), original_name):
                v = None
            if v is not None:
                vals[source] = (v, _card_conf(card, field, True))
        if not vals:
            return
        if len(vals) == 2 and field == "vendor" and vals["rules"][0] != vals["ai"][0]:
            # the same company written longer or shorter ("Riverside Medical Group" and its old full
            # name) is agreement; the household's own spelling, which the rules use, wins
            a, b = rules_mod.norm_name(vals["rules"][0]), rules_mod.norm_name(vals["ai"][0])
            if a and b and (a in b or b in a):
                vals["ai"] = (vals["rules"][0], vals["ai"][1])
        if len(vals) == 2 and vals["rules"][0] == vals["ai"][0]:
            out[field] = vals["rules"][0]
            out["src"][field] = "rules+ai"
            out["conf"][field] = max(vals["rules"][1], vals["ai"][1])
            return
        if len(vals) == 2:
            (rv, rc), (av, ac) = vals["rules"], vals["ai"]
            if rc != ac:
                winner = "rules" if rc > ac else "ai"
            else:
                winner = "ai" if field in _WORD_FIELDS else "rules"
            out[field] = vals[winner][0]
            out["src"][field] = winner
            out["conf"][field] = vals[winner][1]
            loser_conf = vals["ai" if winner == "rules" else "rules"][1]
            if field in REVIEW_FIELDS and loser_conf >= MIN_APPLY:  # a weak guess that lost asks nobody
                out["review"].append(f"{field}: AI says {vals['ai'][0]!r}, the text pattern says "
                                     f"{vals['rules'][0]!r}")
            return
        source, (v, c) = next(iter(vals.items()))
        if c >= (MIN_WORD if field in _WORD_FIELDS else MIN_APPLY):
            out[field] = v
            out["src"][field], out["conf"][field] = source, c
            # a single source, even a confident one, is 'low evidence' until a second agrees
            # (listed in Tidy up; it does not send the document to review or to the AI again)
            out["low_evidence"].append(field)
        else:
            out["suggestions"][field] = v
            if field in REVIEW_FIELDS:
                out["review"].append(f"{field}: only a low-confidence {source} value ({v!r})")

    for field in ("kind", "category", "vendor", "title", "summary", "total", "doc_date"):
        decide(field)

    # dates and ids and people and topics accumulate across sources instead of competing
    seen = set()
    for source in ("rules", "ai", "library"):
        for d in (cards.get(source) or {}).get("dates") or []:
            key = (d["label"], d["date"])
            if key not in seen:
                seen.add(key)
                out["dates"].append({"label": d["label"], "date": d["date"]})
    seen = set()
    for source in ("ai", "rules", "library"):  # ids accumulate; the AI's labels are the wording a
        for i in (cards.get(source) or {}).get("ids") or []:  # person reads, so they win a duplicate
            key = re.sub(r"[^a-z0-9]", "", str(i.get("value") or "").lower())
            if key and key not in seen:
                seen.add(key)
                out["ids"].append({"label": i.get("label") or "number", "value": i.get("value") or ""})
    seen = set()
    for source in ("rules", "ai", "library"):
        for p in (cards.get(source) or {}).get("people") or []:
            key = str(p.get("name") or "").lower()
            if key and key not in seen:
                seen.add(key)
                out["people"].append({"label": p.get("label") or "", "name": p.get("name") or ""})
    for source in ("rules", "ai"):
        for t in (cards.get(source) or {}).get("topics") or []:
            if t not in out["topics"]:
                out["topics"].append(t)
    out["topics"] = out["topics"][:5]
    tags = []
    for source in ("ai", "rules", "library"):
        for t in (cards.get(source) or {}).get("tags") or []:
            if t not in tags:
                tags.append(t)
    out["tags"] = tags
    out["vendor_may_file"] = True
    for source, card in cards.items():
        if out["src"].get("vendor") == source and card.get("vendor_may_file") is False:
            out["vendor_may_file"] = False
    out["series"] = series_key(out["ids"], out["vendor"], out["kind"], correspondent_id)
    return out


# ---- filing from a merged profile ----

def _single_source(prof: dict, field: str) -> bool:
    # a library value is not single-source evidence: by construction it rests on >= 2 trusted
    # neighbours (or a shared ID number), so the lone-source bars below don't apply to it
    return prof["src"].get(field) in ("rules", "ai")


def _clears_bar(prof: dict, field: str) -> bool:
    """May this field be filed? Agreement (rules+ai) or a person's value always may; a lone
    source must clear its auto-apply bar (spec section 3)."""
    if not _single_source(prof, field):
        return True
    return prof["conf"].get(field, 0.0) >= _bar(field)


def _may_file(prof: dict, field: str, ai_filing: bool, rules_filing: bool) -> bool:
    """Is the source of this field allowed to file? `ai_filing` governs the AI's values, `rules_filing`
    the text patterns' and the household library's; a value both agree on needs either switch."""
    src = prof["src"].get(field)
    if src == "ai":
        return ai_filing
    if src in ("rules", "library", "label"):
        return rules_filing
    if src == "rules+ai":
        return ai_filing or rules_filing
    return True


def _by(prof: dict, field: str) -> str:
    """Who the audit log names for a change: the AI when its value is part of it, else the system."""
    return "ai" if "ai" in str(prof["src"].get(field) or "").split("+") else "system"


def _reviewed(con: sqlite3.Connection, doc: dict) -> set:
    """The review reasons a person has already looked at. Stored by 'mark reviewed' since 0.3.2; for an
    earlier review it is read back from the audit log (the suggestions as they were when it was cleared)."""
    sug = doc.get("suggestions") or {}
    if "reviewed" in sug:
        return set(sug["reviewed"])
    for r in con.execute("SELECT action, detail FROM audit_log WHERE document_id = ? AND action IN "
                         "('update', 'auto_update', 'ai_update') ORDER BY id DESC", (doc["id"],)):
        detail = json.loads(r["detail"])
        if "needs_review" not in detail:
            continue
        if r["action"] == "update" and detail["needs_review"] == [True, False]:
            old = (detail.get("suggestions") or [{}])[0] or {}
            return set(old.get("review") or [])
        if r["action"] == "update":
            return set()
    return set()


def apply_profile(con: sqlite3.Connection, doc: dict, prof: dict, tag_rows: list[dict], *,
                  file_it: bool, new_correspondents: bool, done_tags: set, ai_filing: bool = True,
                  rules_filing: bool = True) -> dict:
    """File a document from its merged profile, under the same person-protections as the AI path:
    person-set fields are never touched, 'paid'-type tags are never set, and a company the rules
    only glimpsed in a header becomes a suggestion unless new correspondents are allowed. A lone
    text-pattern value never replaces a company or type the document already has: it is suggested.
    Returns the changes made (same shape as analyze.apply)."""
    from .library import Library
    from .textutil import normalize
    lib = Library(con)
    locked = lib.person_fields(doc["id"])
    changes: dict = {}
    by: dict = {}
    suggestions: dict = dict(prof.get("suggestions") or {})
    review: list[str] = list(prof.get("review") or [])

    def ok(field: str) -> bool:
        return _may_file(prof, field, ai_filing, rules_filing)

    if file_it:
        if "title" not in locked and prof["title"] and prof["title"] != doc["title"] and ok("title"):
            changes["title"], by["title"] = prof["title"], _by(prof, "title")
        if "doc_date" not in locked and prof.get("doc_date") and ok("doc_date"):
            if _clears_bar(prof, "doc_date"):
                if prof["doc_date"] != doc["doc_date"]:
                    changes["doc_date"], by["doc_date"] = prof["doc_date"], _by(prof, "doc_date")
            else:
                suggestions["doc_date"] = prof["doc_date"]
        if "correspondent_id" not in locked and ok("vendor"):
            if prof["vendor"]:
                cid = analyze.match_correspondent(con, prof["vendor"])
                replacing = doc["correspondent_id"] is not None and cid != doc["correspondent_id"]
                if not _clears_bar(prof, "vendor") or (replacing and prof["src"].get("vendor") == "rules"):
                    if cid is None or cid != doc["correspondent_id"]:
                        suggestions["correspondent"] = prof["vendor"]
                        if replacing:
                            review.append(f"vendor: the text pattern suggests {prof['vendor']!r}, "
                                          f"filed under {doc['correspondent']!r}")
                else:
                    # a verified name (known company / sender domain / the AI's grounded vendor) may
                    # create its company; a header-only guess may not, unless the setting allows it
                    if cid is None and (prof.get("vendor_may_file", True) or new_correspondents):
                        cid, created = lib.get_or_create_label("correspondent", prof["vendor"])
                        if created:
                            review.append(f"new company added: {prof['vendor']}")
                    if cid is not None:
                        if cid != doc["correspondent_id"]:
                            changes["correspondent_id"], by["correspondent_id"] = cid, _by(prof, "vendor")
                    else:
                        suggestions["correspondent"] = prof["vendor"]
            elif prof["src"].get("vendor"):
                review.append("no company name found")
        if "document_type_id" not in locked and prof["kind"] and ok("kind"):
            name = analyze.TYPE_NAMES.get(prof["kind"], prof["kind"])
            replacing = doc["document_type_id"] is not None and (doc["document_type"] or "") != name
            if not _clears_bar(prof, "kind") or (replacing and prof["src"].get("kind") == "rules"):
                if replacing or doc["document_type_id"] is None:
                    suggestions["document_type"] = name
            else:
                kind_ok = prof["kind"] in analyze.KIND_EVIDENCE and \
                    analyze.kind_supported(prof["kind"], normalize(f"{doc['title']}\n{doc['content']}"))
                tid = analyze._type_id(con, lib, prof["kind"]) if kind_ok else None
                if tid is not None:
                    if tid != doc["document_type_id"]:
                        changes["document_type_id"], by["document_type_id"] = tid, _by(prof, "kind")
                elif doc["document_type_id"] is None:
                    review.append("couldn't tell what kind of document this is")
        if "tags" not in locked and (ai_filing or rules_filing):
            done = {t.lower() for t in done_tags}
            rows = [t for t in tag_rows if t["name"].lower() not in done]
            by_name = {t["name"].lower(): t["id"] for t in rows}
            chosen = {by_name[t.lower()] for t in prof.get("tags") or [] if t.lower() in by_name}
            if rules_filing:
                chosen |= set(analyze._category_tags(rows, prof["category"], f"{doc['title']}\n{doc['content']}"))
            current = {t["id"] for t in doc["tags"]}
            if "tags" in doc["ai_fields"]:
                if chosen != current:
                    changes["tags"], by["tags"] = sorted(chosen), "ai"
            elif chosen - current:
                changes["tags"], by["tags"] = sorted(current | chosen), "ai" if prof.get("tags") else "system"
            if not chosen and prof["category"] != "other" and not current \
                    and analyze.category_supported(prof["category"], normalize(doc["content"] or "")):
                suggestions["new_tags"] = [prof["category"].title()]
    if prof.get("low_evidence"):
        suggestions["low_evidence"] = list(prof["low_evidence"])
    # a person's "not a copy" (Tidy up, spec E) is a decision, not a suggestion: it outlives refiles
    if (doc.get("suggestions") or {}).get("not_copy_of"):
        suggestions["not_copy_of"] = list(doc["suggestions"]["not_copy_of"])
    # a person's "reviewed" holds until a reason appears that they haven't seen (0.3.2)
    reviewed = _reviewed(con, doc)
    suggestions["review"] = review
    suggestions["reviewed"] = sorted(reviewed)
    fresh = [r for r in review if r not in reviewed]
    from . import duplicates as dup_mod
    with transaction(con):
        for who in ("ai", "system"):
            part = {k: v for k, v in changes.items() if by.get(k, "system") == who}
            if part:
                lib.update(doc["id"], by=who, **part)
        lib.update(doc["id"], by="system", category=prof["category"] or None,
                   series_key=prof.get("series"),
                   simhash=dup_mod.to_signed(dup_mod.simhash(doc["content"] or "")),
                   suggestions=suggestions,
                   needs_review=bool(fresh) and file_it)
        con.execute("DELETE FROM document_topics WHERE document_id = ?", (doc["id"],))
        con.executemany("INSERT INTO document_topics(document_id, topic) VALUES (?, ?)",
                        [(doc["id"], t) for t in prof.get("topics") or []])
    return changes


def refile(con: sqlite3.Connection, doc_id: int, tag_rows: list[dict] | None = None,
           done_tags: set | None = None) -> dict | None:
    """Recompute the rules card for one document, merge all its cards, and file from the merged
    profile. Returns the changes made, or None when there is nothing to do."""
    from . import rules as rules_mod
    from . import settings as settings_mod
    from .library import Library, NotFound
    lib = Library(con)
    try:
        doc = lib.get(doc_id)
    except NotFound:
        return None
    if doc["deleted_at"] or doc["status"] != "ready" or not (doc["content"] or "").strip():
        return None
    card = rules_mod.extract(doc["content"], doc["title"], doc["original_name"],
                             correspondents=[c["name"] for c in lib.labels("correspondent")],
                             aliases=lib.aliases(), current=doc["correspondent"])
    with transaction(con):
        save_card(con, doc_id, "rules", doc["content"], "", rules_mod.RULES_VERSION, card)
    # the library layer (spec B.1): what the household's trusted filing says about this
    # document, computed fresh each refile from the cards and filing as they now stand
    from . import neighbours as neighbours_mod
    lib_card = neighbours_mod.compute(con, doc_id)
    with transaction(con):
        save_card(con, doc_id, "library", doc["content"], "", neighbours_mod.NEIGHBOURS_VERSION,
                  lib_card)
    cards = get_cards(con, doc_id)
    locked = lib.person_fields(doc_id)
    prof = merge(cards, person=_person_values(con, doc, locked),
                 correspondent_id=doc["correspondent_id"], original_name=doc["original_name"])
    if tag_rows is None:
        done_tags = {t.lower() for t in settings_mod.get(con, "done_tags")}
        tag_rows = [t for t in lib.labels("tag") if t["name"].lower() not in done_tags]
    rules_filing = bool(settings_mod.get(con, "rules_filing"))
    ai_filing = bool(settings_mod.get(con, "ai_filing"))
    file_it = rules_filing or (ai_filing and "ai" in cards)
    changes = apply_profile(con, lib.get(doc_id), prof, tag_rows, file_it=file_it,
                            new_correspondents=bool(settings_mod.get(con, "rules_new_correspondents")),
                            done_tags=done_tags if done_tags is not None else set(),
                            ai_filing=ai_filing, rules_filing=rules_filing)
    # near-duplicate check against this document's bucket (spec B.3): flag, never trash
    from . import duplicates as dup_mod
    dup_mod.check_doc(con, doc_id)
    # the A–Z index (spec C.2) is rewritten from the profile that was just filed
    from . import indexing as indexing_mod
    indexing_mod.reindex_document(con, doc_id, prof, lib.get(doc_id))
    return changes


def make_profile() -> "jobs.Handler":
    def profile_job(con: sqlite3.Connection, job: dict) -> None:
        refile(con, job["document_id"])

    return profile_job


# ---- storage (facts table, one row per source) ----

def save_card(con: sqlite3.Connection, doc_id: int, source: str, content: str, model: str,
              version: int, card: dict) -> None:
    con.execute("INSERT INTO facts(document_id, source, content_hash, model, version, card) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(document_id, source) DO UPDATE SET "
                "content_hash = excluded.content_hash, model = excluded.model, version = excluded.version, "
                "card = excluded.card, extracted_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
                (doc_id, source, analyze.content_hash(content), model, version,
                 json.dumps(card, ensure_ascii=False)))
    set_setting(con, analyze.FACTS_GENERATION,
                str(int(get_setting(con, analyze.FACTS_GENERATION, "0")) + 1))


def get_cards(con: sqlite3.Connection, doc_id: int, content: str | None = None) -> dict[str, dict]:
    """The stored cards for one document that still match its text, by source."""
    row = con.execute("SELECT content FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        return {}
    text = row["content"] if content is None else content
    want = analyze.content_hash(text or "")
    out = {}
    for r in con.execute("SELECT source, content_hash, card FROM facts WHERE document_id = ?", (doc_id,)):
        if r["content_hash"] == want:
            out[r["source"]] = json.loads(r["card"])
    return out


def _person_values(con: sqlite3.Connection, doc: dict, locked: set) -> dict:
    from .analyze import TYPE_NAMES
    values = {}
    if "title" in locked:
        values["title"] = doc["title"]
    if "doc_date" in locked:
        values["doc_date"] = doc["doc_date"]
    if "correspondent_id" in locked and doc["correspondent"]:
        values["vendor"] = doc["correspondent"]
    if "document_type_id" in locked and doc["document_type"]:
        for kind, name in TYPE_NAMES.items():
            if name == doc["document_type"]:
                values["kind"] = kind
    if "category" in locked and doc["category"]:
        values["category"] = doc["category"]
    return values


def current_profiles(con: sqlite3.Connection) -> dict[int, dict]:
    """Merged profiles for every live document, from the stored cards (+ the person's own filing)."""
    from .library import Library
    lib = Library(con)
    docs = {}
    for r in con.execute("SELECT d.id, d.content, d.correspondent_id FROM documents d "
                         "WHERE d.deleted_at IS NULL"):
        docs[r["id"]] = r
    cards_by_doc: dict[int, dict] = {}
    for r in con.execute("SELECT f.document_id, f.source, f.content_hash, f.card FROM facts f "
                         "JOIN documents d ON d.id = f.document_id WHERE d.deleted_at IS NULL"):
        if r["document_id"] in docs and r["content_hash"] == analyze.content_hash(docs[r["document_id"]]["content"] or ""):
            cards_by_doc.setdefault(r["document_id"], {})[r["source"]] = json.loads(r["card"])
    out = {}
    full: dict[int, dict] = {}
    for doc_id, row in docs.items():
        doc = lib.get(doc_id)
        full[doc_id] = doc
        locked = lib.person_fields(doc_id)
        merged = merge(cards_by_doc.get(doc_id, {}), person=_person_values(con, doc, locked),
                       correspondent_id=row["correspondent_id"], original_name=doc["original_name"])
        if not merged["vendor"] and doc["correspondent"]:
            merged["vendor"] = doc["correspondent"]
            merged["src"]["vendor"] = "label"
        out[doc_id] = merged
    # copies are visible on the profile (spec B.3): Ask leaves them out of the model's
    # documents and totals leave them out of the sum; series members all still count
    from . import duplicates as dup_mod
    meta = {doc_id: dup_mod._meta_of(full[doc_id], prof) for doc_id, prof in out.items()}
    dup_mod.assign(out, meta)
    return out
