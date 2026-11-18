"""Matching the words people type against the library's own vocabulary — companies
(including their other names), topics, document types, people and ID numbers.

One matcher serves three readers: the header search suggestions (spec C.3), Ask's
entity narrowing (C.4) and the household people mapping (C.6). Matching is
deliberately conservative — whole-word hits only, never stemming — because a
wrong narrowing is worse than none.
"""
import re

from . import indexing, rules, topics as topics_mod


def _padded(text: str) -> str:
    return " " + rules.norm_name(text) + " "


def _hits(padded_text: str, name: str, possessive: bool = False) -> bool:
    n = rules.norm_name(name)
    if not n:
        return False
    if f" {n} " in padded_text:
        return True
    # for people's names only: norm_name deletes apostrophes, so a possessive arrives
    # merged ("boppa's papers" becomes "boppas papers") — name + s counts as naming them.
    # (Not for companies/types/topics: there name + s is usually just a plural.)
    return possessive and f" {n}s " in padded_text


def household_people(con) -> list[dict]:
    """The household list (spec C.6): [{names, words, user_ids}] from settings."""
    from . import settings as settings_mod
    return settings_mod.get(con, "household_people") or []


def find_entities(con, text: str, people_names: list[str] | None = None,
                  user_id: int | None = None) -> dict:
    """The entities confidently named in `text`:
    {companies, topics, types, people} — company names are canonical (an alias
    mention resolves to its company), people are full names from the index plus
    the household list: a household person is also named by any of their other
    words ('Dad'), and by 'my' when the signed-in account is linked to them."""
    padded = _padded(text or "")
    companies, seen = [], set()

    def add_company(name):
        if name and rules.norm_name(name) not in seen:
            seen.add(rules.norm_name(name))
            companies.append(name)

    for r in con.execute("SELECT name FROM correspondents"):
        if _hits(padded, r[0]):
            add_company(r[0])
    for r in con.execute("SELECT a.alias AS alias, c.name AS name FROM correspondent_aliases a "
                         "JOIN correspondents c ON c.id = a.correspondent_id"):
        if _hits(padded, r["alias"]):
            add_company(r["name"])
    types = [r[0] for r in con.execute("SELECT name FROM document_types") if _hits(padded, r[0])]
    from . import settings as settings_mod
    topic_list = [t["name"] for t in topics_mod.all_topics(settings_mod.get(con, "custom_topics"))]
    topics = [t for t in topic_list if _hits(padded, t)]
    names = {r[0] for r in con.execute(
        "SELECT DISTINCT term FROM index_terms WHERE kind = 'person'")}
    names.update(people_names or [])
    household = household_people(con)
    for p in household:
        names.update(p.get("names") or [])
    people = match_people(padded, sorted(names), text)

    def bring_in(person):
        for n in person.get("names") or []:
            if n not in people:
                people.append(n)

    for p in household:
        # a hit on any of a person's names or words stands for all their names, so
        # matching finds papers filed under any of them (a maiden name, say)
        if any(n in people for n in p.get("names") or []) or any(
                _hits(padded, w, possessive=True) for w in p.get("words") or []):
            bring_in(p)
    # a first name that belongs to exactly one household person brings them in too —
    # their several surnames are one person, not an ambiguity
    firsts: dict[str, set] = {}
    for i, p in enumerate(household):
        for n in p.get("names") or []:
            parts = rules.norm_name(n).split()
            if parts:
                firsts.setdefault(parts[0], set()).add(i)
    for first, owners in firsts.items():
        if len(owners) == 1 and _hits(padded, first, possessive=True):
            bring_in(household[next(iter(owners))])
    if user_id is not None and (" my " in padded or " mine " in padded):
        for p in household:
            if user_id in (p.get("user_ids") or []):
                bring_in(p)
    return {"companies": companies, "topics": topics, "types": types, "people": people}


# First names that are also ordinary words (review 0.4.0 #2): months are capitalised
# mid-sentence for grammar and words like "bill" are everyday nouns in a paper
# library, so a capitalised mention is not evidence enough for these — only a
# possessive counts. "will" is deliberately not listed: the modal verb is lowercase
# or sentence-initial (both already excluded below), while a capitalised
# mid-sentence "Will" is a name mention in practice ("papers for Will").
_AMBIGUOUS_FIRST_NAMES = {"may", "june", "april", "august", "bill", "art", "pat",
                          "grant", "mark", "grace", "faith", "hope"}


def _first_name_mentioned(original: str, padded_text: str, first: str) -> bool:
    """Evidence that `first` (normalised) names a person in the original text: a
    possessive ("Will's", or the merged "wills" the normaliser leaves behind), or
    the word capitalised somewhere other than the start of a sentence. A lowercase
    or sentence-initial word is just a word — "Will my car insurance renew in May?"
    names nobody (review 0.4.0 #2)."""
    if f" {first}s " in padded_text:
        return True   # the merged possessive: "sam's" normalises to "sams"
    if not original:
        return False
    for m in re.finditer(r"[^\W\d_]+(?:['’]s)?", original):
        token = m.group(0)
        if rules.norm_name(re.sub(r"['’]s$", "", token.lower())) != first:
            continue
        if token.lower().endswith(("'s", "’s")):
            return True   # a possessive in the original spelling
        if not token[0].isupper() or first in _AMBIGUOUS_FIRST_NAMES:
            continue
        before = original[:m.start()].rstrip().rstrip("\"'“”‘’([{")
        if not before or before[-1] in ".!?":
            continue   # sentence-initial: every sentence starts capitalised
        return True
    return False


def match_people(padded_text: str, names: list[str], original_text: str | None = None) -> list[str]:
    """Full-name word hits, plus a first name on its own when exactly one known
    person carries it AND the text really mentions them (see _first_name_mentioned):
    'papers for Will' finds Will Smith, an ambiguous 'Jo' doesn't, and neither does
    the modal verb in 'Will my car insurance renew?'."""
    out = [n for n in names if _hits(padded_text, n, possessive=True)]
    if out:
        return out
    firsts: dict[str, list[str]] = {}
    for n in names:
        parts = rules.norm_name(n).split()
        if parts:
            firsts.setdefault(parts[0], []).append(n)
    return [owners[0] for first, owners in firsts.items()
            if len(owners) == 1
            and _first_name_mentioned(original_text or "", padded_text, first)]


def number_suffix_matches(con, text: str) -> list[str]:
    """Index number norms (full values, server-side only) whose digits end with a
    run of 4+ digits typed in `text` — 'the card ending 4821' finds its papers."""
    runs = {r for r in re.findall(r"\d[\d ]*\d|\d", text or "") if len(indexing.squash(r)) >= 4}
    if not runs:
        return []
    suffixes = [indexing.squash(r) for r in runs]
    return [r[0] for r in con.execute("SELECT DISTINCT norm FROM index_terms WHERE kind = 'number'")
            if any(r[0].endswith(s) for s in suffixes)]


def _prefix_match(name: str, q_words: list[str], q_squash: str) -> int:
    """2 = the name starts with the query, 1 = a word of it does, 0 = no match.
    Numbers also match on a substring of their digits (handled by the caller)."""
    words = rules.norm_name(name).split()
    if not words or not q_words:
        return 0
    if words[0].startswith(q_words[0]) or indexing.squash(name).startswith(q_squash):
        return 2
    if any(w.startswith(q_words[0]) for w in words):
        return 1
    return 0


def suggest(con, q: str, per_group: int = 6) -> dict:
    """Header-search suggestions (spec C.3): {groups: [{kind, items: [{id, name,
    count}]}]}. Prefix matching on the library's vocabulary; numbers match on their
    label or on part of the number itself and are always shown masked."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"groups": []}
    q_words = rules.norm_name(q).split()
    q_squash = indexing.squash(q)
    groups: list[dict] = []

    def emit(kind, scored):
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: (-s[0], -s[1]["count"], s[1]["name"].lower()))
        if scored:
            groups.append({"kind": kind, "items": [s[1] for s in scored[:per_group]]})

    # Matching runs in Python over each vocabulary's names (few), and only the
    # survivors are counted in the database — counting every company, topic and
    # number up front cost ~90 ms on a 20k-document library for a handful of hits.
    companies = []
    aliases: dict[int, list] = {}
    for a in con.execute("SELECT correspondent_id, alias FROM correspondent_aliases"):
        aliases.setdefault(a["correspondent_id"], []).append(a["alias"])
    for r in con.execute("SELECT id, name FROM correspondents"):
        best = _prefix_match(r["name"], q_words, q_squash)
        for alias in aliases.get(r["id"], []):
            best = max(best, _prefix_match(alias, q_words, q_squash))
        if best:
            count = con.execute("SELECT COUNT(*) FROM documents WHERE correspondent_id = ? "
                                "AND deleted_at IS NULL", (r["id"],)).fetchone()[0]
            companies.append((best, {"id": r["id"], "name": r["name"], "count": count}))
    emit("company", companies)

    people: dict[str, list] = {}
    for r in con.execute(
            "SELECT it.norm AS norm, MIN(it.term) AS term, COUNT(*) AS count "
            "FROM index_terms it JOIN documents d ON d.id = it.document_id "
            "WHERE it.kind = 'person' AND d.deleted_at IS NULL GROUP BY it.norm"):
        people[r["norm"]] = [_prefix_match(r["term"], q_words, q_squash),
                             {"id": indexing.term_key(con, "person", r["norm"]),
                              "name": r["term"], "count": r["count"]}]
    for p in household_people(con):   # household people also suggest by name and their other words
        names = p.get("names") or []
        if not names:
            continue
        score = 0
        for candidate in [*names, *(p.get("words") or [])]:
            score = max(score, _prefix_match(candidate, q_words, q_squash))
        for name in names:
            norm = indexing.squash(name)
            if norm in people:
                people[norm][0] = max(people[norm][0], score)
            else:
                people[norm] = [score, {"id": indexing.term_key(con, "person", norm),
                                        "name": name, "count": 0}]
    emit("person", [(s, item) for s, item in people.values()])

    from . import settings as settings_mod
    topic_items = []
    for t in topics_mod.all_topics(settings_mod.get(con, "custom_topics")):
        score = _prefix_match(t["name"], q_words, q_squash)
        if score:
            count = con.execute(
                "SELECT COUNT(DISTINCT dt.document_id) FROM document_topics dt "
                "JOIN documents d ON d.id = dt.document_id "
                "WHERE dt.topic = ? AND d.deleted_at IS NULL", (t["name"],)).fetchone()[0]
            topic_items.append((score, {"id": t["name"], "name": t["name"], "count": count}))
    emit("topic", topic_items)

    types = []
    for r in con.execute("SELECT id, name FROM document_types"):
        score = _prefix_match(r["name"], q_words, q_squash)
        if score:
            count = con.execute("SELECT COUNT(*) FROM documents WHERE document_type_id = ? "
                                "AND deleted_at IS NULL", (r["id"],)).fetchone()[0]
            types.append((score, {"id": r["id"], "name": r["name"], "count": count}))
    emit("type", types)

    tags = []
    for r in con.execute("SELECT id, name FROM tags"):
        score = _prefix_match(r["name"], q_words, q_squash)
        if score:
            count = con.execute("SELECT COUNT(*) FROM document_tags dt JOIN documents d "
                                "ON d.id = dt.document_id WHERE dt.tag_id = ? "
                                "AND d.deleted_at IS NULL", (r["id"],)).fetchone()[0]
            tags.append((score, {"id": r["id"], "name": r["name"], "count": count}))
    emit("tag", tags)

    numbers = []
    if q_squash and q_words:
        # Candidates are picked and roughly scored in SQL — on a big library one label
        # ("account number") can cover most documents, and scoring every candidate in
        # Python cost more than the search itself. The SQL score mirrors _prefix_match
        # (2 = the label starts with the query, its squashed form does, or the digits
        # contain it; 1 = a later label word does); Python re-scores the survivors
        # exactly, so only an exact tie at the 200-row cut could order differently.
        w = q_words[0].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        for r in con.execute(
                "SELECT it.norm AS norm, MIN(it.term) AS term, MIN(it.label) AS label, "
                "COUNT(*) AS count FROM index_terms it "
                "WHERE it.kind = 'number' AND "
                "it.document_id NOT IN (SELECT id FROM documents WHERE deleted_at IS NOT NULL) AND "
                "(instr(it.norm, ?) > 0 OR it.label LIKE ? ESCAPE '\\' OR it.label LIKE ? ESCAPE '\\' "
                "OR replace(it.label, ' ', '') LIKE ? ESCAPE '\\') "
                "GROUP BY it.norm "
                "ORDER BY (CASE WHEN it.label LIKE ? ESCAPE '\\' "
                "OR replace(it.label, ' ', '') LIKE ? ESCAPE '\\' OR instr(it.norm, ?) > 0 "
                "THEN 2 ELSE 1 END) DESC, count DESC, term ASC LIMIT 200",
                (q_squash, w + "%", "% " + w + "%", q_squash + "%",
                 w + "%", q_squash + "%", q_squash)):
            score = _prefix_match(r["label"] or "", q_words, q_squash)
            if q_squash in r["norm"]:
                score = max(score, 2)
            name = f"{(r['label'] or 'number').capitalize()} {indexing.mask(r['term'])}"
            numbers.append((score, {"id": indexing.term_key(con, "number", r["norm"]),
                                    "name": name, "count": r["count"]}))
    emit("number", numbers)

    paper = []
    if q_squash.isdigit() and not q_squash.startswith("0"):
        # Paper numbers are ints >= 1, so a leading zero can match nothing — and it
        # must never reach the range builder below: with p == 0 the while loop never
        # ends (an all-zero search — "00", "0.", "0-0" — hung the app, review 0.4.0 #1).
        # a digit prefix is a set of numeric ranges (12 -> 12, 120-129, 1200-1299, …),
        # which the paper-number index answers directly
        p, ranges, m = int(q_squash), [], 1
        while p * m <= 999999:
            ranges.append((p * m, (p + 1) * m))
            m *= 10
        cond = " OR ".join("paper_no >= ? AND paper_no < ?" for _ in ranges)
        params = [v for lo, hi in ranges for v in (lo, hi)]
        for r in con.execute(
                f"SELECT paper_no FROM documents WHERE paper_no IS NOT NULL "
                f"AND deleted_at IS NULL AND ({cond}) ORDER BY paper_no LIMIT ?",
                (*params, per_group)):
            paper.append((2, {"id": r[0], "name": f"Paper #{r[0]}", "count": 1}))
    emit("paper", paper)

    years = []
    if q_squash.isdigit() and len(q_squash) <= 4:
        # The typed digits narrow to a date range. Counts come from the date indexes
        # alone (a deleted_at filter in the same query makes SQLite scan the whole
        # table instead), with the trash — a small set — subtracted back out.
        lo = q_squash.ljust(4, "0")
        hi = str(int(lo) + 10 ** (4 - len(q_squash)))
        counts: dict[str, int] = {}
        for r in con.execute(
                "SELECT substr(doc_date, 1, 4) AS y, COUNT(*) AS count FROM documents "
                "WHERE doc_date >= ? AND doc_date < ? GROUP BY y", (lo, hi)):
            counts[r["y"]] = counts.get(r["y"], 0) + r["count"]
        for r in con.execute(
                "SELECT substr(added_at, 1, 4) AS y, COUNT(*) AS count FROM documents "
                "WHERE doc_date IS NULL AND added_at >= ? AND added_at < ? GROUP BY y",
                (lo, hi)):
            counts[r["y"]] = counts.get(r["y"], 0) + r["count"]
        for r in con.execute("SELECT COALESCE(doc_date, added_at) AS dt FROM documents "
                             "WHERE deleted_at IS NOT NULL"):
            y = (r["dt"] or "")[:4]
            if y in counts:
                counts[y] -= 1
        for y in sorted((y for y, c in counts.items() if c > 0), reverse=True)[:per_group]:
            years.append((2, {"id": y, "name": y, "count": counts[y]}))
    emit("year", years)

    from .library import fts_query
    match = fts_query(q)
    if match:
        # two steps, so a common word doesn't join against every matching document:
        # the FTS index ranks first, titles are fetched for the survivors only
        ids = [r[0] for r in con.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ? "
            "ORDER BY rank LIMIT ?", (match, per_group * 4))]
        docs = []
        if ids:
            marks = ", ".join("?" * len(ids))
            titles = {r["id"]: r["title"] for r in con.execute(
                f"SELECT id, title FROM documents WHERE id IN ({marks}) "
                "AND deleted_at IS NULL AND status = 'ready'", ids)}
            docs = [{"id": i, "name": titles[i] or f"Document {i}", "count": None}
                    for i in ids if i in titles][:per_group]
        if docs:   # kept in the full-text search's own order, not re-sorted
            groups.append({"kind": "document", "items": docs})
    return {"groups": groups}
