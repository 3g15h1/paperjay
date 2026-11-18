"""Answers to plain-language questions, with citations. Ported from Paperless Ask.

Per question:
  1. an in-memory term index of every document (reloaded per document when its `rev` changes)
  2. rank with BM25 + synonyms, prefix and typo matching, month/year and recency boosts, fused with the
     database's own full-text search and (optionally) search by meaning
  3. if a key word appears in no document at all, say so instead of guessing
  4. answer without the AI when possible: lists ("show me…"), totals (added up in code from fact cards)
     and single facts ("what's my policy number?") straight from fact cards
  5. otherwise the AI writes a short answer from the best passages of the top documents; every number and
     date in it is checked against those documents, and anything it can't find there is flagged
If the AI is off or fails, the best passages and the key amounts and dates are the answer.
"""
import difflib
import json
import math
import re
import sqlite3
import sys
import threading
from collections import Counter
from datetime import date

from . import ai, analyze, entities, followups, indexing, meaning, settings, totals
from .db import get_setting
from .library import fts_query
from .textutil import normalize
from .textutil import (ANSWER_DATE_RE, DATE_RE, MONEY_RE, MONTH_NAMES, MONTHS, date_variants, match_to_date,
                       num_variants)

LLM_DOCS = 3
MAX_SOURCES = 5
# Similarity of real matches and of unrelated questions overlap, so meaning only re-ranks: documents within
# SEMANTIC_BAND of the best match (and above a per-model floor) get a boost; it never overrules "not found".
# The built-in model's floor: with its search_query/search_document prefixes, right documents scored >= 0.58
# and unrelated questions <= 0.48 on a labelled set (0.5.0).
SEMANTIC_FLOOR = {"all-minilm": 0.25, "nomic-embed-text": 0.45, "builtin:nomic-embed-text-v1.5": 0.52}
SEMANTIC_BAND = 0.06

# ---------------------------------------------------------------- text

STOP = set("""
a about above after again against all also am an and any are as at be been before being below
between both but by can could did do does doing done during each few for from further get got had
has have having he her here hers him his how i if in into is it its itself just me more most my
myself no nor not now of off on once only or other our ours out over own same she should so some
such than that the their them then there these they this those through to too under until up very
was we were what whats when where which while who whom why will with would you your yours
yourself show find tell give list see look please much many anything something thing things
document documents doc docs paperless paperjay file files filed paper papers ive im what's i'm per each every
far yet ever already still since overall altogether combined together entire whole lately anymore
""".split())

# Question word -> related words worth searching for too (weighted lower). Households add their own in
# the ask_synonyms setting ("electric=acme; internet=fastnet").
EXPANSIONS = {
    "car": ["auto", "vehicle"], "auto": ["vehicle"], "vehicle": ["auto"],
    "insurance": ["policy", "insurer"], "insurer": ["insurance"],
    "w2": ["wage", "w-2"], "salary": ["wage", "compensation", "w2"], "wage": ["w2", "compensation"],
    "income": ["wage", "w2", "earning"], "earning": ["wage", "w2"], "pay": ["wage", "paid"],
    "paycheck": ["wage", "earning"], "phone": ["iphone", "mobile", "wireless"], "mobile": ["phone", "wireless"],
    "hotel": ["room", "inn", "resort"], "doctor": ["patient", "medical", "clinic"],
    "medical": ["patient", "health", "clinic"], "hospital": ["patient", "health"],
    "electric": ["energy", "electricity", "utility"], "electricity": ["electric", "energy", "utility"],
    "power": ["electric", "energy"], "gas": ["energy", "utility"], "water": ["sewer", "utility"],
    "utility": ["electric", "gas", "water"],
    "lawn": ["mowing", "property"], "landscaping": ["lawn"], "tax": ["irs"], "taxe": ["tax", "irs"],
    "irs": ["tax"], "registration": ["title", "plate"], "plate": ["registration"],
    "internet": ["broadband", "wifi"], "cable": ["tv", "broadband"], "floor": ["flooring"],
    "flooring": ["floor", "installation"],
    "headphone": ["earbud", "airpod"], "earbud": ["headphone", "airpod"], "airpod": ["headphone"],
    "bill": ["invoice", "statement", "due"], "invoice": ["bill"], "receipt": ["purchase"],
    "lawyer": ["attorney", "law"], "attorney": ["law"],
}

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-'.@][a-z0-9]+)*")
AMOUNT_Q = re.compile(r"\b(how much|cost|costs|price|paid|pay|total|amount|owe|spent|spend|charged?|"
                      r"bill|fee|wages?|salary|income|earn(ed|ings)?)\b", re.I)
DATE_Q = re.compile(r"\b(when|date|what day|expire[sd]?|expiration|due|valid)\b", re.I)
NUMBER_Q = re.compile(r"\b(number|no\.?|policy|account|serial|id|vin|plate|imei|reference|confirmation)\b", re.I)
RECENT_Q = re.compile(r"\b(latest|recent|newest|last|most recent|current|currently|now|expire[sd]?|expiration|"
                      r"valid|renew(al)?)\b", re.I)
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
ID_RE = re.compile(r"(?:number|no\.|#)\s*:?\s*([A-Z0-9][A-Z0-9-]{4,})\b", re.I)
LIST_Q = re.compile(r"^\s*(show|list|find|pull up|open|get)( me)?\b|\ball (of )?my\b|\bwhich documents\b", re.I)


def stem(w: str) -> str:
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "xes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def parse_synonyms(spec: str) -> dict[str, list[str]]:
    """"electric=acme,acmeenergy; internet=fastnet" -> {"electric": ["acme", "acmeenergy"], ...} (single words)."""
    out: dict[str, list[str]] = {}
    for entry in (spec or "").split(";"):
        if "=" not in entry:
            continue
        word, related = entry.split("=", 1)
        word = stem(word.strip().lower())
        extra = [w.strip().lower() for w in related.split(",") if w.strip()]
        if word and extra:
            out[word] = list(dict.fromkeys(out.get(word, []) + extra))
    return out


def expansions(synonyms: str) -> dict[str, list[str]]:
    out = {k: list(v) for k, v in EXPANSIONS.items()}
    for k, v in parse_synonyms(synonyms).items():
        out[k] = list(dict.fromkeys(out.get(k, []) + v))
    return out


# Question words that describe *what kind* of answer is wanted rather than *which* document.
# A query term outside this list that appears in no document at all means "not in the library".
GENERIC = {stem(w) for w in """
cost costs price prices paid pay paying spend spent buy bought purchase purchased total amount amounts
owe owed due charge charged fee fees number numbers date dates day days year years month months
week weeks time times bill bills invoice invoices receipt receipts statement statements expire expires
expired expiration last latest recent newest first info information detail details summary copy
mine record records get got send sent come came recently ago old new current renew renewal
""".split()}


def _words(text: str):
    for w in TOKEN_RE.findall(text.lower().replace("’", "'")):
        w = w.strip(".'")
        if w:
            yield w


def tokenize(text: str) -> list[str]:
    out = []
    for w in _words(text):
        s = stem(w)
        out.append(s)
        if "-" in s:
            out.append(s.replace("-", ""))
    return out


def query_terms(question: str) -> tuple[list[str], list[str]]:
    """Content terms (stemmed, stopwords removed) and the raw words to highlight."""
    terms, raw = [], []
    for w in _words(question):
        if w in STOP or (len(w) < 2 and not w.isdigit()):
            continue
        s = stem(w)
        if s in STOP:
            continue
        terms.append(s)
        raw.append(w)
        if "-" in s:
            terms.append(s.replace("-", ""))
    return terms, raw


def wants_month(question: str) -> list[int]:
    q = question.lower()
    found = set()
    for w in re.findall(r"[a-z]+", q):
        if w in MONTHS and w not in ("may", "mar"):
            found.add(MONTHS[w])
    if re.search(r"\b(in|of|during|since) may\b|\bmay \d{4}\b", q):
        found.add(5)
    if re.search(r"\b(in|of|during|since) mar\b", q):
        found.add(3)
    return sorted(found)


def wants_year(question: str, today: date) -> list[int]:
    years = {int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-4]\d)\b", question)}
    q = question.lower()
    if re.search(r"\bthis year\b", q):
        years.add(today.year)
    if re.search(r"\blast year\b", q):
        years.add(today.year - 1)
    return sorted(years)


def split_pieces(content: str, size: int = 220) -> list[str]:
    pieces = []
    for line in content.splitlines():
        line = " ".join(line.split())
        while len(line) > size:
            cut = line.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pieces.append(line[:cut])
            line = line[cut:].strip()
        if line:
            pieces.append(line)
    return pieces


def make_headline(content: str) -> str:
    for line in content.splitlines():
        line = " ".join(line.split())
        letters = sum(c.isalpha() for c in line)
        if len(line) >= 12 and letters / len(line) > 0.6 and not line.lower().startswith("form_") and "<" not in line:
            return line[:90]
    return " ".join(content.split())[:90]


# ---------------------------------------------------------------- the in-memory index

class Doc:
    """What answering needs to know about one document. Built from the database; `tags` are names."""
    __slots__ = ("id", "rev", "title", "created", "content", "correspondent", "document_type", "tags", "chash",
                 "tf", "length", "_pieces", "headline", "modified", "topics", "series", "paper_no")

    def __init__(self, row: dict, tags: list[str], topics: list[str] | None = None,
                 topic_words: dict | None = None):
        self.id = row["id"]
        self.rev = row["rev"]
        self.paper_no = row["paper_no"] if "paper_no" in row.keys() else None
        self.title = row["title"] or f"Document {self.id}"
        self.created = row["doc_date"] or (row["added_at"] or "")[:10]
        self.series = row["series_key"] if "series_key" in row.keys() else None
        self.modified = str(row["rev"])  # what reminders and totals compare; any change is a new version
        self.content = row["content"] or ""
        self.correspondent = row["correspondent"]
        self.document_type = row["document_type"]
        self.tags = tags
        self.topics = topics or []
        self.chash = analyze.content_hash(self.content)
        # topics give the document the words people actually ask with ("dentist") even when its own
        # text never uses them (spec A.2): topic names and their search words join the meta tokens
        topic_text = " ".join(self.topics + [w for t in self.topics
                                             for w in str((topic_words or {}).get(t, "")).split()])
        meta = " ".join(filter(None, [self.title.replace("_", " "), self.correspondent or "",
                                      self.document_type or "", " ".join(self.tags), topic_text]))
        toks = tokenize(self.content) + tokenize(meta) * 3
        # term -> count as a plain dict with interned keys: the index holds one per document, so this matters
        self.tf = {sys.intern(t): n for t, n in Counter(toks).items()}
        self.length = max(len(toks), 1)
        self._pieces = None
        self.headline = make_headline(self.content)

    @property
    def pieces(self) -> list[str]:
        if self._pieces is None:  # only needed for the few documents that make it into an answer
            self._pieces = split_pieces(self.content)
        return self._pieces

    @property
    def created_date(self):
        try:
            return date.fromisoformat(self.created)
        except ValueError:
            return None

    def card(self, n: int, snippet: str, score: float) -> dict:
        return {
            "n": n, "id": self.id, "title": self.title, "headline": self.headline,
            "created": self.created, "type": self.document_type, "correspondent": self.correspondent,
            "tags": self.tags, "snippet": snippet, "score": round(score, 3), "paper_no": self.paper_no,
            "url": f"/documents/{self.id}", "thumb": f"/api/documents/{self.id}/thumb",
        }


class Index:
    def __init__(self):
        self.docs: dict[int, Doc] = {}
        self.df: Counter = Counter()
        self.avglen = 1.0
        self.vocab: list[str] = []
        self.cards: dict[int, dict] = {}
        self.topic_words: dict[str, str] = {}
        self._cards_key = None
        self._lock = threading.Lock()

    def refresh(self, con: sqlite3.Connection) -> None:
        """Reload documents whose rev changed, drop trashed and deleted ones, and reload fact cards if needed."""
        with self._lock:
            live = {r[0]: r[1] for r in con.execute("SELECT id, rev FROM documents WHERE deleted_at IS NULL "
                                                    "AND status = 'ready'")}
            gone = [i for i in self.docs if i not in live]
            changed = [i for i, rev in live.items() if i not in self.docs or self.docs[i].rev != rev]
            for i in gone:
                self.df.subtract(self.docs.pop(i).tf.keys())
            from . import topics as topics_mod
            topic_words = {t["name"]: t["words"] for t in topics_mod.all_topics(settings.get(con, "custom_topics"))}
            self.topic_words = topic_words
            for start in range(0, len(changed), 500):
                ids = changed[start:start + 500]
                marks = ", ".join("?" * len(ids))
                tags: dict[int, list[str]] = {}
                for r in con.execute("SELECT dt.document_id, t.name FROM document_tags dt JOIN tags t "
                                     f"ON t.id = dt.tag_id WHERE dt.document_id IN ({marks})", ids):
                    tags.setdefault(r[0], []).append(r[1])
                doc_topics: dict[int, list[str]] = {}
                for r in con.execute(f"SELECT document_id, topic FROM document_topics "
                                     f"WHERE document_id IN ({marks})", ids):
                    doc_topics.setdefault(r[0], []).append(r[1])
                for r in con.execute(
                        "SELECT d.id, d.rev, d.title, d.doc_date, d.added_at, d.content, d.series_key, d.paper_no, "
                        "c.name AS correspondent, "
                        "t.name AS document_type FROM documents d "
                        "LEFT JOIN correspondents c ON c.id = d.correspondent_id "
                        f"LEFT JOIN document_types t ON t.id = d.document_type_id WHERE d.id IN ({marks})", ids):
                    if r["id"] in self.docs:
                        self.df.subtract(self.docs[r["id"]].tf.keys())
                    doc = Doc(dict(r), sorted(tags.get(r["id"], [])), doc_topics.get(r["id"], []), topic_words)
                    self.docs[doc.id] = doc
                    self.df.update(doc.tf.keys())
            if gone or changed:
                self.df = +self.df  # drop zero counts
                self.avglen = sum(d.length for d in self.docs.values()) / max(len(self.docs), 1)
                self.vocab = sorted(self.df)
            key = get_setting(con, analyze.FACTS_GENERATION, "0")
            if key != self._cards_key or changed or gone:
                # Ask reads merged profiles now (spec A.4): fact answers work from rules alone,
                # with the AI off, exactly as they do with it on
                from . import profile as profile_mod
                self.cards = {}
                for doc_id, prof in profile_mod.current_profiles(con).items():
                    if doc_id in self.docs and (prof.get("kind") or prof.get("total") is not None
                                                or prof.get("dates") or prof.get("ids")):
                        self.cards[doc_id] = prof
                self._cards_key = key

    def idf(self, t: str) -> float:
        n, df = len(self.docs), self.df.get(t, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5)) if df else 0.0


INDEX = Index()


# ---------------------------------------------------------------- retrieval

class Asker:
    """One question's worth of context: the index, settings and database connection."""

    def __init__(self, con: sqlite3.Connection, index: Index | None = None, today: date | None = None,
                 user_id: int | None = None):
        self.con = con
        self.idx = index if index is not None else INDEX
        self.today = today or date.today()
        self.user_id = user_id  # who is asking — the household mapping (C.6) resolves "my"
        self.exp = expansions(settings.get(con, "ask_synonyms"))
        self.facts_on = True

    # -- ranking --

    def expand_terms(self, terms: list[str]) -> tuple[dict[str, float], list[str]]:
        """Weighted search terms (with synonyms, prefixes, typo matches) and the terms nothing matched."""
        weights: dict[str, float] = {}
        unmatched = []
        df, vocab = self.idx.df, self.idx.vocab

        def add(t, w):
            if weights.get(t, 0) < w:
                weights[t] = w

        for t in terms:
            found = t in df
            add(t, 1.0)
            for e in self.exp.get(t, []):
                add(stem(e), 0.45)
                found = found or stem(e) in df
            if t not in df and len(t) >= 4:
                pref = [v for v in vocab if v.startswith(t) and v != t][:3]
                for v in pref:
                    add(v, 0.7)
                if not pref:
                    pref = difflib.get_close_matches(t, vocab, n=2, cutoff=0.84)
                    for v in pref:
                        add(v, 0.55)
                found = found or bool(pref)
            if not found and t not in GENERIC and not t.isdigit():
                unmatched.append(t)
        return weights, unmatched

    def bm25(self, doc: Doc, weights: dict[str, float], k1=1.2, b=0.75) -> float:
        s = 0.0
        for t, w in weights.items():
            tf = doc.tf.get(t)
            if tf:
                s += w * self.idx.idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc.length / self.idx.avglen))
        return s

    @staticmethod
    def month_hit(doc: Doc, m: int) -> bool:
        cd = doc.created_date
        if cd and cd.month == m:
            return True
        if re.search(rf"\b{MONTH_NAMES[m][:3]}[a-z]*\.? \d{{1,2}}\b", doc.content, re.I):
            return True
        return re.search(rf"\b0?{m}/\d{{1,2}}/\d{{2,4}}\b", doc.content) is not None

    @staticmethod
    def year_hit(doc: Doc, y: int) -> bool:
        cd = doc.created_date
        return bool(cd and cd.year == y) or str(y) in doc.content

    def best_windows(self, doc: Doc, weights: dict[str, float], question: str, k: int = 2, width: int = 360):
        """Up to k non-overlapping passages of the document most relevant to the question."""
        pieces = doc.pieces
        if not pieces:
            return []
        want_amount = bool(AMOUNT_Q.search(question))
        want_date = bool(DATE_Q.search(question))
        want_number = bool(NUMBER_Q.search(question))
        w_idf = {t: w * self.idx.idf(t) for t, w in weights.items() if self.idx.df.get(t)}
        cands = []
        for i in range(len(pieces)):
            text, j = pieces[i], i + 1
            while j < len(pieces) and len(text) + len(pieces[j]) < width:
                text += " " + pieces[j]
                j += 1
            toks = set(tokenize(text))
            s = sum(v for t, v in w_idf.items() if t in toks)
            if want_amount:
                s += 0.6 * min(len(MONEY_RE.findall(text)), 3)
            if want_date and DATE_RE.search(text):
                s += 0.6
            if want_number and re.search(r"\d{5,}", text):
                s += 0.6
            s -= i * 0.002  # gentle preference for earlier text
            cands.append((s, i, j, text))
        cands.sort(key=lambda c: -c[0])
        chosen = []
        for s, i, j, text in cands:
            if s <= 0 and chosen:
                break
            if all(j <= ci or i >= cj for _, ci, cj, _ in chosen):
                chosen.append((s, i, j, text))
            if len(chosen) >= k:
                break
        chosen.sort(key=lambda c: c[1])
        return [c[3] for c in chosen]

    def fulltext(self, terms: list[str]) -> list[int]:
        """The database's own full-text index as a second opinion (fused below)."""
        if not terms:
            return []
        q = fts_query(" ".join(dict.fromkeys(terms)), any_word=True)
        if not q:
            return []
        return [r[0] for r in self.con.execute(
            "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ? ORDER BY bm25(documents_fts, 10.0, 4.0, 1.0) "
            "LIMIT 10", (q,))]

    def retrieve(self, question: str, older: bool = False, doc_id: int | None = None, sem: dict | None = None) -> dict:
        """Rank the library for `question` (for a follow-up: the combined question). With doc_id, the answer
        comes from that one document. older=True turns off the "latest version only" handling.
        sem: search-by-meaning scores, computed by the caller (it may be slow)."""
        docs = self.idx.docs
        if doc_id is not None and doc_id in docs:
            return self.scoped_retrieve(question, docs[doc_id])
        terms, raw = query_terms(question)
        months, years = wants_month(question), wants_year(question, self.today)
        date_words = {str(y) for y in years}
        for m in months:
            date_words |= {MONTH_NAMES[m], MONTH_NAMES[m][:3]}
        content_terms = [t for t in terms if t not in date_words and t not in MONTHS]
        weights, unmatched = self.expand_terms(content_terms)
        matched_specific = {t for t in content_terms if t not in unmatched and t not in GENERIC}
        missing = []
        for w in re.findall(r"[\w'’-]+", question):
            if stem(w.lower().strip("'’")) in unmatched and w not in missing:
                missing.append(w)
        refuse = bool(unmatched) and len(matched_specific) <= len(unmatched)
        for t in terms:
            if t in date_words:
                weights.setdefault(t, 0.5)
        ft_ids = self.fulltext(content_terms)
        sem = (sem or {}) if content_terms else {}
        sem_best = max(sem.values(), default=0.0)
        cfg = ai.Config.load(self.con)
        embed_model = cfg.embed_key if cfg.embed_source == "builtin" else cfg.embed_model.split(":")[0]
        sem_min = SEMANTIC_FLOOR.get(embed_model, 0.4)

        scores = {}
        for doc in docs.values():
            s = self.bm25(doc, weights)
            if not content_terms and (months or years):
                s = max(s, 1.0)
            if s <= 0:
                continue
            if months:
                s *= 1.6 if any(self.month_hit(doc, m) for m in months) else 0.55
            if years:
                s *= 1.6 if any(self.year_hit(doc, y) for y in years) else 0.45
            scores[doc.id] = s
        # entity narrowing (spec C.4): when the question names a company, topic, type or
        # person that exists in the library — or a number by its last digits — documents
        # about it count for more and the rest for less, before the scores are normalized
        # and fused. Soft on purpose (matching ×1.5, the rest ×0.6, exactly the shape of
        # the month/year factors above): never a hard filter, so a question whose words
        # point somewhere else entirely still answers from everywhere.
        found = entities.find_entities(self.con, question, user_id=self.user_id)
        number_norms = entities.number_suffix_matches(self.con, question)
        if any(found.values()) or number_norms:
            matching = self._entity_doc_ids(found, number_norms, docs)
            if matching:
                for i in scores:
                    scores[i] *= 1.5 if i in matching else 0.6
        top = max(scores.values(), default=0)
        final = {i: s / top for i, s in scores.items()} if top else {}
        for rank, i in enumerate(ft_ids):
            if i in docs:
                final[i] = final.get(i, 0) + 0.3 / (1 + rank)
        by_meaning = [(i, s) for i, s in sorted(sem.items(), key=lambda kv: -kv[1])[:3]
                      if i in docs and s >= sem_min and s >= sem_best - SEMANTIC_BAND]
        for i, sim in by_meaning:
            # within the band, differences are noise between documents on the same topic: the same boost for
            # all (plus a tie-break), so word matches and recency still decide their order
            final[i] = final.get(i, 0) + 0.3 + 0.05 * (1 - (sem_best - sim) / SEMANTIC_BAND)
        recent = bool(RECENT_Q.search(question)) and not older
        if recent:
            for i in final:
                cd = docs[i].created_date
                if cd:
                    final[i] += 0.15 * max(0, 1 - (self.today - cd).days / 3650)
        ranked = sorted(final.items(), key=lambda kv: -kv[1])
        best = ranked[0][1] if ranked else 0
        ranked = [(i, s) for i, s in ranked if s >= max(0.3 * best, 0.12)]
        # sources are grouped by series (spec B.3) for "current/latest"-type questions: the
        # newest member stands for the series and "+N earlier" counts the rest. Any other
        # question may be about a specific older member (a named month, last year's bill),
        # so every member competes on its own — grouping there cost real answers in the
        # 0.3.1 acceptance eval (hit@3 97.4% -> 86.8% on the snapshot) and was narrowed to
        # current-type questions only. An "older" follow-up always asks for every member.
        grouped = self._group_series(ranked) if recent else [(i, s, None, 0) for i, s in ranked]
        grouped = grouped[:MAX_SOURCES]

        sources, llm_docs, seen = [], [], set()
        for n, (i, s, series, earlier) in enumerate(grouped, 1):
            doc = docs[i]
            wins = self.best_windows(doc, weights, question, k=3 if n == 1 else 1, width=380 if n == 1 else 320)
            card = doc.card(n, wins[0] if wins else doc.headline, s)
            if series:
                card["series"], card["earlier"] = series, earlier
            sources.append(card)
            key = (doc.title, doc.created)
            if len(llm_docs) < LLM_DOCS and key not in seen and (not llm_docs or s >= 0.45 * best):
                if (self.idx.cards.get(i) or {}).get("duplicate_of"):
                    continue  # a copy never goes to the model (spec B.3)
                # "current"-type questions: an older near-copy (last year's insurance card)
                # only confuses the model. Series grouping already keeps it out; this guards
                # the window before a document's series has been computed at all.
                if recent and any(_similar(doc, d) and (d.created or "") >= (doc.created or "")
                                  for _, d, _ in llm_docs):
                    continue
                seen.add(key)
                llm_docs.append((n, doc, wins))
        listing = bool(LIST_Q.search(question)) and len(sources) > 0

        highlight = {w for w in raw if len(w) > 2} | {t for t, w in weights.items() if w < 1 and len(t) > 2}
        return {
            "question": question, "sources": self.with_categories(sources), "words": raw,
            "llm_docs": [] if refuse or listing else llm_docs,
            "_candidates": llm_docs,  # what the model would read if asked anyway (full=1)
            "highlight": sorted(highlight, key=len, reverse=True)[:20], "months": months, "years": years,
            "missing": missing, "refused": refuse, "listing": listing and not refuse, "scoped": None,
            "semantic": {str(i): round(s, 3) for i, s in by_meaning},
            "extractive": self.extractive_answer(question, sources, llm_docs, missing if refuse else [], listing,
                                                 by_meaning=bool(by_meaning)),
        }

    def _entity_doc_ids(self, found: dict, number_norms: list[str], docs: dict) -> set:
        """The documents the named entities are about: company/type/topic from the index's
        own fields, people and numbers through the A–Z terms (which hold every refiled
        document's profile, including numbers only a fact card knows)."""
        ids = set()
        companies, types = set(found["companies"]), set(found["types"])
        topics = set(found["topics"])
        for doc in docs.values():
            if doc.correspondent in companies or doc.document_type in types or topics & set(doc.topics):
                ids.add(doc.id)
        for kind, norms in (("person", {indexing.squash(n) for n in found["people"]}),
                            ("number", set(number_norms))):
            norms.discard("")
            if not norms:
                continue
            marks = ", ".join("?" * len(norms))
            ids.update(r[0] for r in self.con.execute(
                f"SELECT document_id FROM index_terms WHERE kind = ? AND norm IN ({marks})",
                (kind, *sorted(norms))))
        return ids

    def _group_series(self, ranked: list) -> list:
        """Collapse ranked (id, score) pairs to one entry per series: (representative id,
        group score, series key, earlier count). The representative is the newest member;
        the group keeps the best member's score."""
        groups: dict[str, list] = {}
        order: list[str] = []
        for i, s in ranked:
            doc = self.idx.docs[i]
            series = (self.idx.cards.get(i) or {}).get("series") or doc.series
            if series:
                key = series
            elif doc.correspondent:
                # no series computed yet (the document hasn't been refiled since it arrived):
                # the vendor fallback series is company + kind, so group on those
                key = f"label:{doc.correspondent}|{doc.document_type or ''}"
            else:
                key = f"doc:{i}"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((i, s, series))
        out = []
        for key in order:
            members = groups[key]
            rep = max(members, key=lambda ms: ((self.idx.docs[ms[0]].created or ""), ms[0]))
            out.append((rep[0], max(s for _, s, _ in members), rep[2], len(members) - 1))
        out.sort(key=lambda r: -r[1])
        return out

    def scoped_retrieve(self, question: str, doc: Doc) -> dict:
        """"Ask about this document": the only source is `doc`, and the model reads more of it."""
        terms, raw = query_terms(question)
        weights, _ = self.expand_terms(terms)
        wins = self.best_windows(doc, weights, question, k=4, width=420)
        sources = [doc.card(1, wins[0] if wins else doc.headline, 1.0)]
        llm_docs = [(1, doc, wins)]
        return {
            "question": question, "sources": self.with_categories(sources), "words": raw, "llm_docs": llm_docs,
            "highlight": sorted({w for w in raw if len(w) > 2}, key=len, reverse=True)[:20],
            "months": wants_month(question), "years": wants_year(question, self.today), "missing": [],
            "refused": False, "listing": False, "scoped": doc.id, "semantic": {}, "_candidates": llm_docs,
            "extractive": self.extractive_answer(question, sources, llm_docs, []),
        }

    def card_for(self, doc: Doc) -> dict | None:
        return self.idx.cards.get(doc.id) if self.facts_on else None

    def with_categories(self, sources: list) -> list:
        for s in sources:
            c = self.idx.cards.get(s["id"])
            doc = self.idx.docs.get(s["id"])
            ok = c and c.get("category") != "other" and doc is not None and \
                analyze.category_supported(c["category"], normalize(f"{doc.title}\n{doc.content}"))
            s["category"] = c["category"] if ok else None  # only a category the text backs up
        return sources

    def extractive_answer(self, question: str, sources: list, llm_docs: list, missing: list[str],
                          listing: bool = False, by_meaning: bool = False) -> dict:
        if listing and sources and not missing:
            strong = [s for s in sources if s["score"] >= 0.45 * sources[0]["score"]]
            items = [f"“{s['title']}”" + (f" ({s['created']})" if s["created"] else "") + f" [{s['n']}]"
                     for s in strong]
            if len(items) == 1:
                return {"text": f"I found one matching document: {items[0]}.", "facts": []}
            return {"text": f"I found {len(items)} matching documents: " + "; ".join(items) + ".", "facts": []}
        if missing:
            text = f"I couldn't find anything mentioning {_quote_list(missing)} in your {len(self.idx.docs)} documents."
            if sources and by_meaning:
                text += " The documents below are the closest in meaning, but none of them uses that word."
            elif sources:
                text += " These are the closest matches, but they probably aren't what you're looking for."
            return {"text": text, "facts": []}
        if not sources:
            return {"text": "I couldn't find anything in your documents that matches that. "
                            "Try different words, a company name, or a year.", "facts": []}
        n, _, wins = llm_docs[0] if llm_docs else (1, None, [])
        s = sources[n - 1]
        pool = " ".join(wins) if wins else s["snippet"]
        facts = []
        kinds = [(rx, kind) for rx, kind, intent in ((ID_RE, "number", NUMBER_Q), (MONEY_RE, "amount", AMOUNT_Q),
                                                     (DATE_RE, "date", DATE_Q)) if intent.search(question)]
        for rx, kind in kinds or [(MONEY_RE, "amount"), (DATE_RE, "date")]:
            for m in rx.findall(pool):
                v = " ".join(m.split())
                if v not in [f["value"] for f in facts]:
                    facts.append({"kind": kind, "value": v, "n": n})
                if len([f for f in facts if f["kind"] == kind]) >= 3:
                    break
        when = f" ({s['created']})" if s["created"] else ""
        return {"text": f"Best match: “{s['title']}”{when} [{n}].", "facts": facts[:5]}

    # -- answers without the model --

    def fact_answer(self, question: str, r: dict, own: str | None = None) -> str | None:
        """Answer straight from fact cards when the question asks for one labelled fact and the documents the
        model would have read agree on a single value. None means: let the model answer.
        own: a follow-up's own words, whose intent ("and the policy number?") wins over the combined question."""
        if not r["llm_docs"] or r["refused"] or r["listing"]:
            return None
        want = fact_intent((own if own is not None else question).lower())
        if want is None:
            return None
        subject = self.subject_terms(own if own is not None else question)
        found = {}  # value -> (n, name, card, label)
        for n, doc, _ in r["llm_docs"]:
            card = self.card_for(doc)
            if not card:
                return None  # a document the model would read has no card yet
            if subject and not self.card_is_about(card, doc, subject):
                continue  # e.g. an expiry date on a car lease that merely mentions a passport
            name = card.get("vendor") or doc.title
            if want[0] == "date":
                for d in card["dates"]:
                    if d["label"] == want[1]:
                        found.setdefault(d["date"], (n, name, card))
            elif want[0] == "id":
                for i in card["ids"]:
                    if not want[1] or any(w in i["label"].lower() for w in want[1]):
                        found.setdefault(i["value"], (n, name, card, i["label"]))
            elif card.get("total") is not None:
                found.setdefault(card["total"], (n, name, card))
        if want[0] == "date" and len(found) > 1:
            # "when does it expire / when is it due" means the next one: past dates are history
            today = self.today.isoformat()
            found = {v: x for v, x in found.items() if v >= today}
        if len(found) != 1:
            return None
        value, (n, name, card, *label) = next(iter(found.items()))
        if want[0] == "date" and want[1] == "expires":
            what = {"insurance": " insurance", "id": " ID"}.get(card["kind"], "")
            if what.strip().lower() in name.lower():  # "Example Mutual Insurance insurance"
                what = ""
            return f"{name}{what} expires on {_long_date(value)} [{n}]."
        if want[0] == "date":
            if card.get("total"):
                return f"{totals.money(card['total'])} is due to {name} on {_long_date(value)} [{n}]."
            return f"Payment to {name} is due on {_long_date(value)} [{n}]."
        if want[0] == "id":
            return f"{label[0].rstrip(':').title()}: {value} [{n}] ({name})."
        return f"{name} total: {totals.money(value)} [{n}]."

    def subject_terms(self, question: str) -> list[str]:
        """What the question is about ("car insurance", "passport"), without the words that say what's wanted."""
        terms, _ = query_terms(question)
        return [t for t in terms if t not in GENERIC and t not in _INTENT_WORDS and not t.isdigit() and t not in MONTHS]

    def card_is_about(self, card: dict, doc: Doc, subject: list[str]) -> bool:
        # a document's topics count as what it is about (spec A.2/A.4): the dental statement is
        # 'about' the dentist even though its text never uses the word
        topic_text = " ".join(list(card.get("topics") or []) + list(doc.topics or []) +
                              [self.idx.topic_words.get(t, "") for t in doc.topics or []])
        about = " ".join([card.get("vendor") or "", card.get("kind") or "", card.get("category") or "",
                          card.get("summary") or "", doc.title.replace("_", " "), doc.correspondent or "",
                          " ".join(i["label"] for i in card.get("ids", [])), topic_text])
        words = set(tokenize(about))
        for t in subject:
            options = {t} | {stem(e) for e in self.exp.get(t, [])}
            if any(w == o or (len(o) >= 4 and w.startswith(o)) for o in options for w in words):
                return True
        return False

    def total_answer(self, question: str, r: dict) -> bool:
        """Fill r with an itemised total if this is a "how much did I spend…" question."""
        if not totals.is_total_question(question):
            return False
        words = [w for w in r["words"] if w not in MONTHS]
        with_text = [d for d in self.idx.docs.values() if d.content.strip()]
        res = totals.compute(question, words, with_text, self.idx.cards, r["years"], r["months"], self.today,
                             supports=lambda cat, doc: analyze.category_supported(cat, normalize(f"{doc.title}\n{doc.content}")))
        sources = []
        for n, i in enumerate(res["items"], 1):
            when = f" · {_long_date(i['date'].isoformat())}" if i["date"] else ""
            sources.append(i["doc"].card(n, f"{totals.money(i['amount'])}{when} · {i['vendor']}", 1.0))
        cites = "".join(f"[{s['n']}]" for s in sources[:12])
        r["sources"] = self.with_categories(sources)
        r["llm_docs"] = []
        r["total"] = {
            "total": res["total"], "pending": res["pending"], "categories": res["categories"],
            "vendors": res["vendors"], "years": res["years"], "months": res["months"],
            "items": [{"n": n, "id": i["doc"].id, "amount": i["amount"], "date": i["date"] and i["date"].isoformat(),
                       "vendor": i["vendor"], "kind": i["kind"]} for n, i in enumerate(res["items"], 1)],
            "excluded": [{"id": i["doc"].id, "title": i["doc"].title, "amount": i["amount"],
                          "url": f"/documents/{i['doc'].id}", "date": i["date"] and i["date"].isoformat(),
                          "reason": i["reason"]} for i in res["excluded"]],
        }
        r["extractive"] = {"text": totals.describe(res, MONTH_NAMES, cites), "facts": []}
        return True

    def paper_answer(self, question: str, r: dict, own: str | None = None) -> str | None:
        """Phase D: the paper number is one of a document's answerable facts — for
        questions that are plainly about the paper copy only (REVIEW-0.4.2 #1). The
        shelf phrasings ('paper no. 57', 'paper #57', a bare 'paper 57', '#57') name
        the document outright; 'where's the paper copy of my W-2?' is answered from
        the best-matching document. An ordinary question that merely contains
        'paper' — 'What's the account number on my paper water bill?', 'How much
        was toilet paper 57 pack?' — is not a paper lookup: None means the normal
        paths answer it. Off entirely unless the paper_numbers setting is on."""
        if not settings.get(self.con, "paper_numbers"):
            return None
        asked = (own if own is not None else question).lower()
        m = (re.search(r"\bpaper\s*(?:no\.?|number)\s*#?\s*(\d{1,9})\b", asked)
             or re.search(r"\bpaper\s*#\s*(\d{1,9})\b", asked)
             or re.fullmatch(r"(?:(?:what|which|where|find|show|get|is|the|a)\s+)*"
                             r"paper\s*#?\s*(\d{1,9})\s*\??", asked))
        if not m and "#" in asked and not any(w in asked for w in _ID_WORDS):
            m = re.search(r"#(\d{1,9})\b", asked)  # "#57" alone is the shelf syntax; "account #57" is not
        if m:  # a number was named: find its document, wherever retrieval ranked things
            n = int(m.group(1))
            for doc in self.idx.docs.values():
                if doc.paper_no == n:
                    r["sources"] = self.with_categories([doc.card(1, "", 1.0)])
                    return f"“{doc.title}” is Paper no. {n} [1]."
            return None
        if not r["llm_docs"] or r["refused"] or r["listing"]:
            return None
        # The where-is-it branch: the question must be about the paper copy itself —
        # 'paper copy/original', or which paper number a document has — asked as a
        # where/which/what question. 'Paper' alone (a paper water bill) is not
        # enough, and 'has no paper number recorded' is only said here, where the
        # question is plainly about the paper copy.
        if not re.search(r"\bpaper\s+(?:copy|copies|original|no\.?|number)\b", asked):
            return None
        if not re.search(r"\b(where|which|what)\b", asked):
            return None
        numbered = [(n, doc) for n, doc, _ in r["llm_docs"] if doc.paper_no]
        n0, top, _ = r["llm_docs"][0]
        if top.paper_no and len(numbered) == 1:
            return f"Paper no. {top.paper_no} [{n0}]."
        if top.paper_no:
            return f"“{top.title}” is Paper no. {top.paper_no} [{n0}]."
        return f"“{top.title}” has no paper number recorded [{n0}]."

    def special_answer(self, question: str, r: dict, full: bool, own: str | None = None,
                       older: bool = False) -> str | None:
        """'total' or 'facts' when the question was answered without the model (r is updated), else None."""
        # paper first: a named number ('#57') is answered even when retrieval refused the
        # question — the digits appear in no document's text, so refusal says nothing here.
        # paper_answer's where-is-it branch still honours refused/listing itself.
        if not full and not older and (text := self.paper_answer(question, r, own)):
            r["extractive"] = {"text": text, "facts": []}
            return "facts"
        if r["refused"]:
            return None
        if r["scoped"] is None and self.total_answer(question, r):
            return "total"
        if not full and not older and (text := self.fact_answer(question, r, own)):
            r["extractive"] = {"text": text, "facts": []}
            return "facts"
        return None

    def prepare(self, q: str, prev_q: str | None, prev_a: str | None, doc_id: int | None, full: bool,
                sem: dict | None = None):
        """Retrieval plus any answer that needs no model. Returns (r, mode, context for the prompt)."""
        terms, _ = query_terms(q)
        plan = followups.plan(q, prev_q, len(terms))
        scope = doc_id if doc_id in self.idx.docs else None
        r = self.retrieve(plan["search"], older=plan["older"], doc_id=scope, sem=sem)
        r.update(question=q, context_q=plan["search"], followup=plan["followup"])
        context = {"q": " ".join((prev_q or "").split())[:500], "a": prev_a or ""} if plan["followup"] else None
        mode = self.special_answer(plan["search"], r, full, own=q if plan["followup"] else None, older=plan["older"])
        if full and r["refused"] and not mode:
            r["llm_docs"] = r["_candidates"]  # "Ask the AI anyway": the prompt still says the word appears nowhere
        return r, mode, context

    def search_text(self, q: str, prev_q: str | None) -> str:
        """What will be searched for (needed before prepare() to compute meaning scores in advance)."""
        terms, _ = query_terms(q)
        return followups.plan(q, prev_q, len(terms))["search"]


def semantic_scores(con: sqlite3.Connection, text: str) -> dict[int, float]:
    return meaning.scores(con, text) if query_terms(text)[0] else {}


def _similar(a: Doc, b: Doc) -> bool:
    ka, kb = set(a.tf), set(b.tf)
    return len(ka & kb) / max(len(ka | kb), 1) > 0.4


def _quote_list(words: list[str]) -> str:
    q = [f"“{w}”" for w in words]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " or " + q[-1]


def _long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


_ID_WORDS = ("policy", "account", "invoice", "order", "member", "vin", "reference", "confirmation", "claim",
             "serial", "plate", "tracking", "customer", "group", "case")
_INTENT_WORDS = {stem(w) for w in """expire expires expired expiration expiry renew renewal valid due pay payment
number numbers no policy account invoice order member vin reference confirmation claim serial plate tracking customer
group case cost price total amount paid owe much""".split()}


def fact_intent(q: str):
    if re.search(r"\b(expir\w*|renew\w*|valid (until|through|thru))\b", q):
        return ("date", "expires")
    if re.search(r"\bdue\b|\bpay(ment)? by\b", q) and DATE_Q.search(q):
        return ("date", "due")
    if NUMBER_Q.search(q) and not AMOUNT_Q.search(q):
        return ("id", [w for w in _ID_WORDS if re.search(rf"\b{w}", q)])
    if re.search(r"\b(how much|cost|price|total|amount|paid|owe)\b", q):
        return ("total", None)
    return None


# ---------------------------------------------------------------- the model's answer

SYSTEM_PROMPT = """You are PaperJay, a helpful assistant that answers questions about the user's own scanned documents (bills, receipts, insurance, tax forms).
Rules:
- Use ONLY the numbered sources provided. Never invent facts, amounts, dates or numbers.
- Answer in 1-3 short sentences. Lead with the direct answer.
- Copy amounts, dates and ID numbers exactly as written in the sources.
- Cite the source number in square brackets right after each fact, like [1] or [2].
- If the sources do not contain the answer, say plainly that you couldn't find it in the documents, then mention what the closest document is about.
- When several sources are versions of the same thing from different years (e.g. insurance cards, W-2s), use the most recent one unless the question names a specific date or year.
- The text is OCR output and may contain errors; ignore garbled fragments."""


def build_prompt(question: str, llm_docs: list, missing: list[str] = (), context: dict | None = None,
                 scoped: bool = False, today: date | None = None) -> str:
    parts = []
    for n, doc, wins in llm_docs:
        meta = [f"title: {doc.title}"]
        if doc.created:
            meta.append(f"date: {doc.created}")
        if doc.document_type:
            meta.append(f"type: {doc.document_type}")
        if doc.correspondent:
            meta.append(f"from: {doc.correspondent}")
        if doc.paper_no:
            meta.append(f"paper no: {doc.paper_no}")
        flat = " ".join(doc.content.split())
        if len(flat) <= (3500 if scoped else 1400 if n == llm_docs[0][0] else 500):
            body = [flat]
        else:
            body = [f"(beginning) {doc.headline}"] + [f"... {w} ..." for w in wins if w[:60] not in doc.headline]
        parts.append(f"[{n}] " + " | ".join(meta) + "\n" + "\n".join(body))
    day = (today or date.today()).strftime("%B %d, %Y")
    note = f"\nNote: no document mentions {_quote_list(list(missing))}.\n" if missing else ""
    if context:
        prev_a = " ".join(re.sub(r"\[\d+\]", "", context.get("a") or "").split())[:300]
        note += (f"\nEarlier in this conversation the user asked: {context['q']}\n"
                 + (f"and was told: {prev_a}\n" if prev_a else "")
                 + "The new question follows on from that.\n")
    return (f"Today is {day}.\n\nSources:\n\n" + "\n\n".join(parts) + f"\n{note}\nQuestion: {question}\n\n"
            "Reply with just the answer in 1-3 sentences, using only the sources above. "
            "End each sentence with the number of the source it came from in square brackets, e.g. [1].")


def messages(question: str, llm_docs: list, missing=(), context=None, scoped=False) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(question, llm_docs, missing, context, scoped)}]


def _date_variants(m: re.Match) -> tuple[str, set[str]]:
    d = match_to_date(m)
    return m.group(0), (date_variants(d.year, d.month, d.day) if d else set())


def tidy_citations(text: str) -> str:
    """'[2] Foo bar.' -> 'Foo bar [2].'   'Foo. [1].' -> 'Foo [1].'   'Foo, [1].' -> 'Foo [1].'"""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+(?=\[|\w)", text.strip()):
        m = re.match(r"^((?:\[\d+\]\s*)+)(.+)$", sent)
        if m:
            cites = "".join(re.findall(r"\[\d+\]", m.group(1)))
            body = m.group(2).rstrip()
            end = body[-1] if body[-1] in ".!?" else "."
            sent = f"{body.rstrip('.!?')} {cites}{end}"
        out.append(sent)
    text = " ".join(out)
    for _ in range(3):
        text = re.sub(r"[.,;]\s*((?:\[\d+\]\s*)+)\.?", r" \1.", text)
        text = re.sub(r"\]\s+\[", "][", text)
        text = re.sub(r"(\[\d+\])(?:\1)+", r"\1", text)
        text = re.sub(r"\s+\.", ".", text)
    return text.strip()


def has_words(text: str) -> bool:
    return len(re.findall(r"[A-Za-z]{2,}", re.sub(r"\[[\d,\s]+\]", " ", text))) >= 3


def verify_answer(answer: str, llm_docs: list, today: date | None = None) -> dict:
    """Check every number and date in the AI's answer against the text of the documents it was given.
    Adds a citation when the model forgot to, and reports anything it couldn't ground."""
    texts = {n: (doc.title + " " + doc.created + " " + doc.content).replace(",", "").replace("$ ", "$").lower()
             for n, doc, _ in llm_docs}
    today = today or date.today()
    body = re.sub(r"\[[\d,\s]+\]", " ", answer)
    unverified, cite_for = [], []

    def check(label, variants):
        hits = [n for n, t in texts.items()
                if any(re.search(rf"(?<![\d.]){re.escape(v.lower())}(?!\d)", t) for v in variants)]
        if hits:
            cite_for.append(hits[0])
        elif label not in unverified:
            unverified.append(label)

    for m in list(ANSWER_DATE_RE.finditer(body)):
        label, variants = _date_variants(m)
        if variants and today.strftime("%B %-d %Y").lower() not in {v.lower() for v in variants}:
            check(label, variants)
    body = ANSWER_DATE_RE.sub(" ", body)
    for m in NUM_RE.finditer(body):
        raw = m.group(0).rstrip(".,")
        if sum(c.isdigit() for c in raw) >= 3:
            check(raw, num_variants(raw))

    final = tidy_citations(answer.strip().strip('"“”').strip())
    if llm_docs and not re.search(r"\[\d+", final):
        cites = "".join(f"[{n}]" for n in (sorted(set(cite_for)) or [llm_docs[0][0]]))
        final = f"{final[:-1]} {cites}{final[-1]}" if final[-1:] in (".", "!", "?") else f"{final} {cites}"
    return {"answer": final, "unverified": unverified}


def public(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("llm_docs", "_candidates")}

