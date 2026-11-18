"""The 'analyze' job: one AI call per document that both files it and reads its key facts.

The model fills in a fixed form (structured output): kind, category, company, a title, a summary, the
total, labelled dates, ID numbers, and which of the existing tags fit (it can only choose from the list,
so it can't invent tags; it may propose one new tag, which waits for a person to approve).

Nothing it says is trusted as is (ground()): the company, every amount, date and ID number must appear in
the document's own text, or it is dropped. Then filing (apply()):
  title, date, company, type, tags   set only if no person has set that field
  company                            matched to an existing one, else added (setting ai_new_correspondents)
  type                               from a fixed list (Receipt, Bill, Insurance…), added when first needed
  needs review                       when something looks uncertain, with the reasons in suggestions.review
The fact card is kept in the facts table and powers instant answers, totals and "coming up".
"""
import difflib
import hashlib
import json
import logging
import re
import sqlite3
from datetime import date

import httpx

from . import ai, jobs, settings, totals
from .db import get_setting, set_setting, transaction
from .library import Library, NotFound
from .textutil import date_variants, grounded, normalize, num_variants, parse_amount

log = logging.getLogger("paperjay")

ANALYZE_JOB = "analyze"
FACTS_GENERATION = "facts_generation"  # bumped on every saved card, so answer indexes know to reload
EXTRACTOR_VERSION = 4  # bump when the prompt or grounding changes, so every card is redone
MIN_CARD_VERSION = 3   # older cards stay valid (spec B.4): fields they lack are filled by rules
MAX_CHARS = 4000
KINDS = ["receipt", "invoice", "bill", "statement", "insurance", "tax", "medical", "pay", "contract", "id",
         "letter", "other"]
CATEGORIES = ["medical", "insurance", "utilities", "shopping", "home", "vehicle", "tax", "income", "travel",
              "food", "finance", "other"]
DATE_LABELS = ["issued", "due", "expires", "paid", "service", "effective", "other"]
TYPE_NAMES = {"receipt": "Receipt", "invoice": "Invoice", "bill": "Bill", "statement": "Statement",
              "insurance": "Insurance", "tax": "Tax form", "medical": "Medical record", "pay": "Pay stub",
              "contract": "Contract", "id": "ID document", "letter": "Letter"}
# Which of a document's dates is "its" date, in order of preference
DOC_DATE_LABELS = ["issued", "service", "paid", "effective"]

SYSTEM_PROMPT = """Read one scanned document (OCR text, may contain errors) and file it. Reply with JSON only.
- kind: what the document is. category: what area of life it belongs to.
- vendor: the company, shop, insurer, clinic or agency that issued it.
- title: a short title a person would give it, at most 8 words, like "Acme Energy bill August 2025" or "Auto insurance card 2026".
- summary: one short sentence saying what the document is.
- total: the final amount paid or due, copied exactly as written, or null if there is none.
- dates: the important dates and what they mean (issued = the date of the document itself, due = payment due, expires = coverage/validity ends), written as YYYY-MM-DD.
- ids: policy, account, invoice, order, member or reference numbers, copied exactly. Never put names here.
- tags: only labels from the allowed list that clearly describe this document; usually none or one.
Leave out anything that is not in the text."""


def schema(tag_names: list[str], topic_names: list[str] | None = None) -> dict:
    props = {
        "kind": {"type": "string", "enum": KINDS},
        "category": {"type": "string", "enum": CATEGORIES},
        "vendor": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "total": {"type": ["string", "null"]},
        "dates": {"type": "array", "items": {
            "type": "object", "required": ["label", "date"],
            "properties": {"label": {"type": "string", "enum": DATE_LABELS}, "date": {"type": "string"}}}},
        "ids": {"type": "array", "items": {
            "type": "object", "required": ["label", "value"],
            "properties": {"label": {"type": "string"}, "value": {"type": "string"}}}},
        "tags": {"type": "array", "items": {"type": "string", "enum": tag_names}} if tag_names
        else {"type": "array", "maxItems": 0, "items": {"type": "string"}},
        # topics from the household vocabulary only (spec B.4), at most 3
        "topics": {"type": "array", "maxItems": 3,
                   "items": {"type": "string", "enum": list(topic_names)}} if topic_names
        else {"type": "array", "maxItems": 0, "items": {"type": "string"}},
        # people only as labelled in the text ("Patient: ..."), never guessed (spec section 3)
        "people": {"type": "array", "items": {
            "type": "object", "required": ["label", "name"],
            "properties": {"label": {"type": "string"}, "name": {"type": "string"}}}},
    }
    return {"type": "object", "properties": props, "required": list(props)}


def messages(title: str, content: str, tag_names: list[str], hints: list[str] | None = None) -> list[dict]:
    text = " ".join(content.split())[:MAX_CHARS]
    allowed = ", ".join(tag_names) if tag_names else "(none yet)"
    hint_block = ""
    if hints:
        hint_block = ("\n\nHints from the document's own text patterns and from similar "
                      "documents already filed (they can be wrong; the document text wins):\n"
                      + "\n".join(f"- {h}" for h in hints))
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Allowed tags: {allowed}{hint_block}\n\n"
                                        f"File name or title: {title}\n\n{text}"}]


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:24]


# ---- grounding (from Paperless Ask's fact cards) ----

_GENERIC_VENDOR_WORDS = set("""the and inc llc ltd corp corporation company group services service insurance bank
card cards financial medical center centre clinic store stores shop family health care department dept co""".split())


def _squash(s: str) -> str:
    return re.sub(r"[\s\-./]", "", s).lower()


def ground(raw: dict, text: str, title: str = "", tag_names=(), correspondent: str | None = None,
           topic_names=()) -> dict:
    """Keep only what the document's text supports. Returns the card plus `dropped`: notes on what was
    thrown away. ID numbers must be in the text itself; the title (often a file name) only counts for the
    company, amounts and dates."""
    squashed = _squash(text)
    norm = normalize(f"{title}\n{text}")
    dropped = []

    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    category = raw.get("category") if raw.get("category") in CATEGORIES else "other"

    vendor = " ".join(str(raw.get("vendor") or "").split()).strip(" ,;:")[:80]
    words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", vendor)]
    words = [w for w in words if w not in _GENERIC_VENDOR_WORDS] or words
    if vendor and not (words and all(re.search(rf"\b{re.escape(w)}", norm) for w in words)):
        dropped.append(f"vendor {vendor!r}")
        vendor = ""
    vendor = vendor or (correspondent or "")

    total_text = raw.get("total")
    total = parse_amount(total_text) if total_text not in (None, "", "null", "None") else None
    if total_text not in (None, "", "null", "None"):
        # written as money in the text: with cents ("142.37") or a dollar sign ("$79"), so an account
        # number can't pass as an amount
        money_forms = {f"{total:.2f}", f"${total:.2f}", f"${total:.0f}"} if total else set()
        if total is None or total <= 0 or not grounded(norm, money_forms):
            dropped.append(f"total {total_text!r}")
            total = None

    dates, seen = [], set()
    for d in raw.get("dates") or []:
        label = d.get("label") if d.get("label") in DATE_LABELS else "other"
        try:
            when = date.fromisoformat(str(d.get("date", "")).strip()[:10])
        except ValueError:
            dropped.append(f"date {d.get('date')!r}")
            continue
        if not grounded(norm, date_variants(when.year, when.month, when.day)):
            dropped.append(f"{label} date {when.isoformat()}")
            continue
        if (label, when) not in seen:
            seen.add((label, when))
            dates.append({"label": label, "date": when.isoformat()})

    ids, seen_ids = [], set()
    for i in raw.get("ids") or []:
        value = " ".join(str(i.get("value") or "").split()).strip("()[]{}:;,.#")
        label = " ".join(str(i.get("label") or "").split())[:40] or "number"
        if len(value) < 4 or not re.search(r"\d", value) or _squash(value) not in squashed:
            dropped.append(f"id {label}={value!r}")
            continue
        if _squash(value) not in seen_ids:
            seen_ids.add(_squash(value))
            ids.append({"label": label, "value": value})

    # A tag the model picked counts only if its word is in the document (models like to pick something:
    # every bill got "Coupon" for its payment stub), or it names the document's category or kind.
    allowed = {t.lower(): t for t in tag_names}
    tags = []
    for t in raw.get("tags") or []:
        name = allowed.get(str(t).lower())
        if name and name not in tags:
            if _tag_supported(name, norm, kind, category):
                tags.append(name)
            else:
                dropped.append(f"tag {name!r}")

    # topics: only names from the household vocabulary, at most 3 (spec B.4)
    vocab = {t.lower(): t for t in topic_names}
    topics_out = []
    for t in raw.get("topics") or []:
        name = vocab.get(str(t).strip().lower())
        if name and name not in topics_out:
            topics_out.append(name)
        elif not name:
            dropped.append(f"topic {t!r}")
    topics_out = topics_out[:3]

    # people: labelled only, and the name must appear in the document's own text (section 3)
    people, seen_people = [], set()
    for p in raw.get("people") or []:
        label = " ".join(str(p.get("label") or "").split())[:40]
        name = " ".join(str(p.get("name") or "").split()).strip(" ,;:")[:80]
        if not label or not name or name.lower() in seen_people:
            if name:
                dropped.append(f"person {name!r}")
            continue
        if not grounded(norm, {name}):
            dropped.append(f"person {name!r}")
            continue
        seen_people.add(name.lower())
        people.append({"label": label, "name": name})

    new_title = " ".join(str(raw.get("title") or "").split()).strip(" \"'“”.")[:80]
    if new_title and not re.search(r"[^\W\d_]{3}", new_title):
        new_title = ""
    summary = " ".join(str(raw.get("summary") or "").split())[:200]
    return {"kind": kind, "category": category, "vendor": vendor, "title": new_title, "summary": summary,
            "total": total, "dates": dates, "ids": ids, "tags": tags, "topics": topics_out,
            "people": people, "dropped": dropped}


# The evidence patterns live in rules.py now (spec A.1: one source of truth, shared with the rule
# extractor); they are re-exported here so existing users of this module keep working. A category is
# only "supported" when the document's own text backs it up (see rules.category_supported).
from .rules import CATEGORY_EVIDENCE, KIND_EVIDENCE, category_supported, kind_supported  # noqa: E402,F401


def _tag_supported(tag: str, norm: str, kind: str, category: str) -> bool:
    words = re.findall(r"[^\W\d_]{3,}", tag.lower())
    if not words:
        return False
    if any(w.rstrip("s") in (category, category.rstrip("s")) for w in words) and category_supported(category, norm):
        return True
    if any(w.rstrip("s") == kind for w in words) and kind_supported(kind, norm):
        return True
    return all(re.search(rf"\b{re.escape(w.rstrip('s'))}", norm) for w in words)


# ---- fact cards ----

def get_card(con: sqlite3.Connection, doc_id: int, content: str | None = None) -> dict | None:
    """The document's AI fact card if it matches its current text, else None. Cards from
    EXTRACTOR_VERSION 3 on stay valid after a bump (spec B.4): the fields a v3 card lacks
    (topics, people) are filled by the rules layer instead of re-reading everything."""
    row = con.execute("SELECT f.*, d.content FROM facts f JOIN documents d ON d.id = f.document_id "
                      "WHERE f.document_id = ? AND f.source = 'ai'", (doc_id,)).fetchone()
    if not row or row["version"] < MIN_CARD_VERSION:
        return None
    if row["content_hash"] != content_hash(row["content"] if content is None else content):
        return None
    return json.loads(row["card"])


def current_cards(con: sqlite3.Connection) -> dict[int, dict]:
    out = {}
    for r in con.execute("SELECT f.document_id, f.card, f.content_hash, f.version, d.content FROM facts f "
                         "JOIN documents d ON d.id = f.document_id WHERE d.deleted_at IS NULL "
                         "AND f.source = 'ai'"):
        if r["version"] >= MIN_CARD_VERSION and r["content_hash"] == content_hash(r["content"]):
            out[r["document_id"]] = json.loads(r["card"])
    return out


def needs_reread(prof: dict, ai_version: int | None = None) -> bool:
    """Selective re-reads (spec B.4): queue an AI re-read only when the merged profile shows a real
    gap in what a person files by — the rules and the AI disagree on the company, kind or date, or
    there is no company or kind at all — and only once per extractor version: a document whose AI
    card is already from this EXTRACTOR_VERSION (for its current text) is never re-read again.
    Single-source fields and missing topics are not gaps (0.3.2: they made 32 of 34 documents
    re-read every night)."""
    if ai_version is not None and ai_version >= EXTRACTOR_VERSION:
        return False
    if any("AI says" in r for r in prof.get("review") or []):
        return True
    return not prof.get("vendor") or not prof.get("kind")


def in_batch_window(con: sqlite3.Connection, now=None) -> bool:
    """Inside `ai_batch_hours` (server-local time; the window may wrap past midnight)?"""
    from datetime import datetime, time
    spec = str(settings.get(con, "ai_batch_hours") or "01:00-06:00")
    try:
        start_s, end_s = spec.split("-", 1)
        start = time.fromisoformat(start_s.strip())
        end = time.fromisoformat(end_s.strip())
    except ValueError:
        return False
    t = (now or datetime.now()).time()
    return start <= t < end if start <= end else (t >= start or t < end)


def build_hints(con: sqlite3.Connection, doc: dict, lib: Library) -> list[str]:
    """The Hints block for the analyze prompt (spec B.4): what the rules read, how similar
    documents were filed, and the companies that already exist (at most 8)."""
    from . import neighbours as neighbours_mod
    from . import profile as profile_mod
    hints = []
    cards = profile_mod.get_cards(con, doc["id"])
    rules_card = cards.get("rules") or {}
    bits = []
    if rules_card.get("vendor"):
        bits.append(f"company {rules_card['vendor']}")
    if rules_card.get("kind"):
        bits.append(f"kind {TYPE_NAMES.get(rules_card['kind'], rules_card['kind'])}")
    if rules_card.get("total") is not None:
        bits.append(f"total {rules_card['total']}")
    if rules_card.get("doc_date"):
        bits.append(f"dated {rules_card['doc_date']}")
    if bits:
        hints.append("Text patterns read: " + ", ".join(bits) + ".")
    tops = neighbours_mod.top_filings(con, doc["id"])
    if tops:
        parts = []
        for top in tops:
            kind = TYPE_NAMES.get(top["kind"], top["kind"]) if top["kind"] else ""
            parts.append(f"{top['vendor']} ({top['count']})" + (f", type {kind}" if kind else ""))
        hints.append("Similar documents were filed as: " + "; ".join(parts) + ".")
    names = [c["name"] for c in lib.labels("correspondent")][:8]
    if names:
        hints.append("Companies already in the library: " + ", ".join(names) + ".")
    return hints


def save_card(con: sqlite3.Connection, doc_id: int, content: str, model: str, card: dict) -> None:
    con.execute("INSERT INTO facts(document_id, source, content_hash, model, version, card) VALUES (?, 'ai', ?, ?, ?, ?) "
                "ON CONFLICT(document_id, source) DO UPDATE SET content_hash = excluded.content_hash, model = excluded.model, "
                "version = excluded.version, card = excluded.card, extracted_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
                (doc_id, content_hash(content), model, EXTRACTOR_VERSION, json.dumps(card, ensure_ascii=False)))
    set_setting(con, FACTS_GENERATION, str(int(get_setting(con, FACTS_GENERATION, "0")) + 1))


# ---- filing ----

from .rules import norm_name as _norm_name  # noqa: E402,F401  ('AT&T Inc.' -> 'att', etc.; see rules)


def match_correspondent(con: sqlite3.Connection, vendor: str) -> int | None:
    """An existing correspondent that is clearly the same company ('DTE Energy Co.' = 'DTE Energy')."""
    want = _norm_name(vendor)
    if not want:
        return None
    best, best_score = None, 0.0
    for r in con.execute("SELECT id, name FROM correspondents"):
        have = _norm_name(r["name"])
        if not have:
            continue
        if have == want:
            return r["id"]
        short, long_ = sorted((have, want), key=len)
        if len(short) >= 3 and re.search(rf"\b{re.escape(short)}\b", long_):
            score = 0.9 + len(short) / len(long_) / 10
        else:
            score = difflib.SequenceMatcher(None, have, want).ratio()
        if score > best_score:
            best, best_score = r["id"], score
    return best if best_score >= 0.88 else None


def _type_id(con: sqlite3.Connection, lib: Library, kind: str) -> int | None:
    name = TYPE_NAMES.get(kind)
    if not name:
        return None
    for candidate in (name, kind, kind + "s", name + "s"):
        found = lib.find_label("document_type", candidate)
        if found:
            return found
    return lib.get_or_create_label("document_type", name)[0]


def _category_tags(tag_rows: list[dict], category: str, text: str) -> list[int]:
    """An existing tag named after the document's category ('Medical', 'Utilities') applies, if the text
    backs the category up."""
    if category == "other" or not category_supported(category, normalize(text)):
        return []
    names = {category, category.rstrip("s"), category + "s"}
    return [t["id"] for t in tag_rows if t["name"].lower() in names]


def apply(con: sqlite3.Connection, doc: dict, card: dict, tag_rows: list[dict], *, file_it: bool,
          new_correspondents: bool, done_tags=()) -> dict:
    """File the document from its card, leaving alone anything a person has set. Returns the changes made."""
    lib = Library(con)
    locked = lib.person_fields(doc["id"])
    review = []
    suggestions = dict(doc["suggestions"])
    changes: dict = {}
    if file_it:
        if "title" not in locked and card["title"]:
            changes["title"] = card["title"]
        if "doc_date" not in locked:
            for label in DOC_DATE_LABELS:
                found = [d["date"] for d in card["dates"] if d["label"] == label]
                if found:
                    changes["doc_date"] = found[0]
                    break
        if "correspondent_id" not in locked:
            if card["vendor"]:
                cid = match_correspondent(con, card["vendor"])
                if cid is None and new_correspondents:
                    cid, created = lib.get_or_create_label("correspondent", card["vendor"])
                    if created:
                        review.append(f"new company added: {card['vendor']}")
                if cid is not None:
                    changes["correspondent_id"] = cid
                else:
                    suggestions["correspondent"] = card["vendor"]
            else:
                review.append("no company name found")
        if "document_type_id" not in locked:
            # the type too must fit the text (the model called a sales receipt a "bill")
            ok = card["kind"] in KIND_EVIDENCE and kind_supported(card["kind"], normalize(f"{doc['title']}\n{doc['content']}"))
            tid = _type_id(con, lib, card["kind"]) if ok else None
            if tid is not None:
                changes["document_type_id"] = tid
            else:
                review.append("couldn't tell what kind of document this is")
        if "tags" not in locked:
            # "paid"-type tags record what a person did; the AI must never set them (a wrong "Paid" would
            # hide a bill from the due-date reminders)
            done = {t.lower() for t in done_tags}
            tag_rows = [t for t in tag_rows if t["name"].lower() not in done]
            by_name = {t["name"].lower(): t["id"] for t in tag_rows}
            chosen = {by_name[t.lower()] for t in card["tags"] if t.lower() in by_name}
            chosen |= set(_category_tags(tag_rows, card["category"], f"{doc['title']}\n{doc['content']}"))
            current = {t["id"] for t in doc["tags"]}
            if "tags" in doc["ai_fields"]:
                # the current tags are all the AI's own (a person or an import would have taken them over):
                # filing again replaces them rather than piling more on
                if chosen != current:
                    changes["tags"] = sorted(chosen)
            elif chosen - current:
                changes["tags"] = sorted(current | chosen)
            if not chosen and card["category"] != "other" and not current \
                    and category_supported(card["category"], normalize(doc["content"] or "")):
                suggestions["new_tags"] = [card["category"].title()]
                review.append(f"suggested a new tag: {card['category'].title()}")
    if len(re.findall(r"[^\W\d_]", doc["content"] or "")) < 40:
        review.append("very little text was found; check the scan")
    suggestions["review"] = review
    with transaction(con):
        if changes:
            lib.update(doc["id"], by="ai", **changes)
        lib.update(doc["id"], by="system", suggestions=suggestions, needs_review=bool(review) and file_it)
    return changes


# ---- the job ----

def make_analyze() -> jobs.Handler:
    def analyze(con: sqlite3.Connection, job: dict) -> None:
        cfg = ai.Config.load(con)
        if not cfg.on:
            return
        if ai.people_waiting():
            raise jobs.Defer(60)
        lib = Library(con)
        try:
            doc = lib.get(job["document_id"])
        except NotFound:
            return
        if doc["deleted_at"] or doc["status"] != "ready" or not (doc["content"] or "").strip():
            return
        if get_card(con, doc["id"], doc["content"]) and not job["payload"].get("force") \
                and not job["payload"].get("reread"):
            return
        done = {t.lower() for t in settings.get(con, "done_tags")}
        tag_rows = [t for t in lib.labels("tag") if t["name"].lower() not in done]
        tag_names = [t["name"] for t in tag_rows]
        from . import topics as topics_mod
        topic_names = [t["name"] for t in topics_mod.all_topics(settings.get(con, "custom_topics"))]
        hints = build_hints(con, doc, lib)
        try:
            raw = ai.chat(cfg, messages(doc["title"], doc["content"], tag_names, hints=hints),
                          schema=schema(tag_names, topic_names), max_tokens=1000, wait=5)
        except TimeoutError:
            raise jobs.Defer(30)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise jobs.Defer(600, f"AI server unreachable: {e}")
        card = ground(raw, doc["content"], doc["title"], tag_names, doc["correspondent"],
                      topic_names=topic_names)
        with transaction(con):
            save_card(con, doc["id"], doc["content"], cfg.model, card)
        # filing now happens from the merged profile (spec A.4): the AI card is one layer of it
        from . import profile
        changes = profile.refile(con, doc["id"], tag_rows=tag_rows, done_tags=done) or {}
        log.info("analyzed document %d: %s/%s, %s; set %s%s", doc["id"], card["kind"], card["category"],
                 card["vendor"] or "no company", ", ".join(sorted(changes)) or "nothing",
                 f"; dropped {len(card['dropped'])}" if card["dropped"] else "")

    return analyze
