"""Near-duplicate detection (spec B.3). Monthly bills from one template look alike, so a copy
needs all of: the same series (or the same correspondent), a SimHash within Hamming distance
3, a text length within 10 %, and the same key facts — the same total, the same issued/due/
service dates, or neither document having any key facts at all.

A copy is only ever flagged: `duplicate_of` points at the oldest member and, while the
`duplicate_detection` setting is `log` (the default until the real-data gate passes), that is
all that happens — no review flag, and never a trash. In `review` mode the copy also lands in
the review queue ("looks like a copy of #19").
"""
import hashlib
import logging
import re
import sqlite3
from collections import Counter

from . import settings as settings_mod
from .library import Library, NotFound

log = logging.getLogger("paperjay")

MAX_HAMMING = 3
KEY_DATE_LABELS = ("issued", "due", "service")
_WORDS = re.compile(r"[a-z0-9]+")


def simhash(text: str) -> int:
    """64-bit SimHash of the text's 3-word shingles (unsigned; see to_signed for storage)."""
    words = _WORDS.findall((text or "").lower())
    if not words:
        return 0
    shingles = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)] or [" ".join(words)]
    weights = [0] * 64
    for shingle, n in Counter(shingles).items():
        h = int.from_bytes(hashlib.md5(shingle.encode()).digest()[:8], "big")
        for bit in range(64):
            weights[bit] += n if (h >> bit) & 1 else -n
    out = 0
    for bit in range(64):
        if weights[bit] > 0:
            out |= 1 << bit
    return out


def to_signed(h: int) -> int:
    """SQLite integers are signed 64-bit; the simhash column stores this form."""
    return h - (1 << 64) if h >= (1 << 63) else h


def from_signed(h: int) -> int:
    return h + (1 << 64) if h < 0 else h


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def key_facts(prof: dict) -> tuple[float | None, frozenset]:
    dates = frozenset((d["label"], d["date"]) for d in prof.get("dates") or []
                      if d["label"] in KEY_DATE_LABELS)
    return prof.get("total"), dates


def facts_match(pa: dict, pb: dict) -> bool:
    """The same key facts (spec B.3): the same total, a shared issued/due/service date, or
    neither document having any key facts — and no contradiction: a copy is the *same*
    document, so where both state a total, or a date under the same label, they must agree.
    Without the contradiction guard, yearly renewals of one insurance card (a series, not
    copies) flag as copies: they share no key facts, but their expiry dates differ."""
    ta, da = key_facts(pa)
    tb, db = key_facts(pb)
    if ta is not None and tb is not None and ta != tb:
        return False
    by_label_a: dict[str, set] = {}
    for d in pa.get("dates") or []:
        by_label_a.setdefault(d["label"], set()).add(d["date"])
    by_label_b: dict[str, set] = {}
    for d in pb.get("dates") or []:
        by_label_b.setdefault(d["label"], set()).add(d["date"])
    if any(not (by_label_a[label] & by_label_b[label])
           for label in by_label_a.keys() & by_label_b.keys()):
        return False
    if ta is not None and tb is not None and ta == tb:
        return True
    if da & db:
        return True
    return ta is None and tb is None and not da and not db


def _close(pa: dict, pb: dict, ma: dict, mb: dict) -> bool:
    if hamming(ma["simhash"], mb["simhash"]) > MAX_HAMMING:
        return False
    la, lb = ma["length"], mb["length"]
    if la and lb and abs(la - lb) > 0.1 * max(la, lb):
        return False
    return facts_match(pa, pb)


def find_duplicates(profiles: dict[int, dict], meta: dict[int, dict]) -> dict[int, int]:
    """{copy_id: original_id} over one library. meta[id]: {"simhash", "length", "oldest_key",
    "series", "vendor"}. Bucketed by series (else vendor), so unrelated documents are never
    even compared; within a bucket the oldest matching document is the original."""
    buckets: dict[tuple, list[int]] = {}
    for doc_id, m in meta.items():
        if m.get("series"):
            key = ("series", m["series"])
        elif m.get("vendor"):
            key = ("vendor", m["vendor"].lower())
        else:
            continue
        buckets.setdefault(key, []).append(doc_id)
    out: dict[int, int] = {}
    for ids in buckets.values():
        originals: list[int] = []
        for doc_id in sorted(ids, key=lambda i: meta[i]["oldest_key"]):
            apart = meta[doc_id].get("not_copy_of") or ()   # a person said "not a copy" (E)
            hit = next((o for o in originals if o not in apart
                        and doc_id not in (meta[o].get("not_copy_of") or ())
                        and _close(profiles[doc_id], profiles[o], meta[doc_id], meta[o])), None)
            if hit is None:
                originals.append(doc_id)
            else:
                out[doc_id] = hit
    return out


def assign(profiles: dict[int, dict], meta: dict[int, dict]) -> None:
    """Fill each profile's `duplicate_of` in place (used by profile.current_profiles, so Ask
    and totals see copies without a database column for them)."""
    for doc_id, original in find_duplicates(profiles, meta).items():
        profiles[doc_id]["duplicate_of"] = original


# ---- per-document check after a refile ----

def _profile_of(con: sqlite3.Connection, doc_id: int) -> dict | None:
    from . import profile as profile_mod
    lib = Library(con)
    try:
        doc = lib.get(doc_id)
    except NotFound:
        return None
    cards = profile_mod.get_cards(con, doc_id)
    if not cards:
        return None
    return profile_mod.merge(cards, correspondent_id=doc["correspondent_id"])


def _meta_of(doc: dict, prof: dict) -> dict:
    return {"simhash": from_signed(doc["simhash"]) if doc["simhash"] is not None
            else simhash(doc["content"] or ""),
            "length": len(doc["content"] or ""),
            "oldest_key": ((doc["doc_date"] or (doc["added_at"] or "")[:10]), doc["id"]),
            "series": prof.get("series"), "vendor": prof.get("vendor") or doc["correspondent"],
            "not_copy_of": set((doc.get("suggestions") or {}).get("not_copy_of") or ())}


def check_doc(con: sqlite3.Connection, doc_id: int) -> int | None:
    """Re-check one document's bucket (same series or correspondent) after its refile and flag
    any copies found, per the `duplicate_detection` setting. Returns the original's id when
    this document is itself a copy, else None."""
    lib = Library(con)
    try:
        doc = lib.get(doc_id)
    except NotFound:
        return None
    if doc["deleted_at"] or doc["status"] != "ready":
        return None
    peers = [r[0] for r in con.execute(
        "SELECT id FROM documents WHERE deleted_at IS NULL AND status = 'ready' AND id != ? "
        "AND ((series_key IS NOT NULL AND series_key = ?) "
        "OR (correspondent_id IS NOT NULL AND correspondent_id = ?))",
        (doc_id, doc["series_key"], doc["correspondent_id"]))]
    ids = [doc_id, *peers]
    profiles, meta = {}, {}
    for i in ids:
        prof = _profile_of(con, i)
        if prof is None:
            continue
        profiles[i] = prof
        meta[i] = _meta_of(lib.get(i), prof)
    if doc_id not in profiles:
        return None
    mode = settings_mod.get(con, "duplicate_detection")
    found = find_duplicates(profiles, meta)
    for copy_id, original_id in found.items():
        _flag(con, lib, copy_id, original_id, mode)
    return found.get(doc_id)


def _flag(con: sqlite3.Connection, lib: Library, copy_id: int, original_id: int, mode: str) -> None:
    doc = lib.get(copy_id)
    suggestions = dict(doc["suggestions"])
    if suggestions.get("duplicate_of") == original_id and mode != "review":
        return
    suggestions["duplicate_of"] = original_id
    fields: dict = {"suggestions": suggestions}
    if mode == "review":
        note = f"looks like a copy of #{original_id}"
        review = [r for r in suggestions.get("review") or [] if "looks like a copy of" not in r]
        suggestions["review"] = review + [note]
        fields["needs_review"] = True
    else:
        log.info("document %d looks like a copy of document %d (duplicate_detection=log)",
                 copy_id, original_id)
    lib.update(copy_id, by="system", **fields)
