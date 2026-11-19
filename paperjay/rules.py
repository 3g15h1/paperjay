"""The rule extractor (spec A.1): a fact card from the document's text alone — no AI, no I/O
except the label lookups passed in. Pure functions, so the whole library can be re-filed in
seconds when the rules improve (RULES_VERSION).

This module also owns the shared pattern lists (kind/category evidence, labelled-date patterns):
analyze.py and reminders.py import them from here, so there is one source of truth.
"""
import re
from datetime import date

from . import topics as topics_mod
from .dates import find_date
from .textutil import ANSWER_DATE_RE, DATE_RE, MONEY_RE, MONTH_NAMES, normalize, parse_amount

RULES_VERSION = 3  # bump when extraction changes, so every document is re-filed on upgrade (2: 0.3.2; 3: 0.7.5 legal topic)

# ---- shared evidence patterns (moved from analyze.py) ----

# Words that back up a category (from totals.CATEGORY_WORDS, plus extras). A category is only
# "supported" when the text itself contains one of these words.
def _category_evidence() -> dict[str, set]:
    from .totals import CATEGORY_WORDS
    out = {cat: set(words.split()) for cat, words in CATEGORY_WORDS.items()}
    for cat, extra in {"utilities": "energy kwh meter broadband wireless",
                       "home": "apartment apartments rent rental lease tenant landlord plumbing roofing hvac mortgage",
                       "medical": "dds md physician optometry vision lab",
                       "vehicle": "vin mileage tire tires oil dealership",
                       "finance": "statement card credit balance payment",
                       "insurance": "policy coverage insured premium"}.items():
        out[cat] = out.get(cat, set()) | set(extra.split())
    return out


CATEGORY_EVIDENCE: dict[str, set] = _category_evidence()

KIND_EVIDENCE = {"bill": "amount due|due by|due date|bill|balance due|pay by|please pay", "receipt": "receipt|paid|thank you",
                 "invoice": "invoice", "statement": "statement", "insurance": "insurance|policy|coverage",
                 "tax": "tax|irs|w-2|1099-", "medical": "patient|clinic|doctor|pharmacy|hospital",
                 "pay": "pay stub|earnings|gross pay|net pay", "contract": "agreement|contract|lease|terms",
                 "id": "license|passport|identification", "letter": "dear|sincerely"}


def _evidence(w: str) -> str:
    if len(w) <= 3:
        return rf"\b{re.escape(w)}\b(?![\s,.]*\d{{5}})"
    return rf"\b{re.escape(w)}"


def kind_supported(kind: str, norm: str) -> bool:
    return bool(KIND_EVIDENCE.get(kind)) and re.search(rf"\b({KIND_EVIDENCE[kind]})", norm) is not None


def category_supported(category: str, norm: str) -> bool:
    words = CATEGORY_EVIDENCE.get(category, set()) | {category}
    return any(re.search(_evidence(w), norm) for w in words)


# ---- labelled dates (due/expires shared with reminders.py) ----

_LABELS = {
    "expires": r"expiration date|expiry date|exp\.? date|expires(?: on)?|expiring(?: on)?|valid (?:until|thru|through)"
               r"|coverage ends|renewal date|renews(?: on)?",
    "due": r"payment due date|payment due(?: by| on)?|due date|due by|due on|pay(?:ment)? by|please pay by"
           r"|amount due by|balance due by",
}
_DATE = ANSWER_DATE_RE.pattern
DATE_PATTERNS = [(label, re.compile(rf"\b(?:{words})\b[^\d\n]{{0,20}}?(?:{_DATE})", re.I))
                 for label, words in _LABELS.items()]

_EXTRA_DATE_LABELS = {
    "issued": r"statement date|invoice date|bill date|billing date|date of issue|issued(?: on)?",
    "service": r"date of service|service date|date of visit|visit date",
    "effective": r"effective date|coverage (?:begins|starts|effective)",
    "paid": r"date paid|payment date|paid on",
}
EXTRA_DATE_PATTERNS = [(label, re.compile(rf"\b(?:{words})\b[^\d\n]{{0,20}}?(?:{_DATE})", re.I))
                       for label, words in _EXTRA_DATE_LABELS.items()]
_BARE_DATE = re.compile(rf"(?:^|\n)\s*date\s*[:#]\s*({_DATE})", re.I)

DOC_DATE_LABELS = ["issued", "service", "paid", "effective"]

# ---- names ----

_CORP_WORDS = re.compile(r"\b(inc|llc|ltd|co|corp|corporation|company|the|plc|gmbh|lp|llp)\b\.?", re.I)


def norm_name(name: str) -> str:
    name = re.sub(r"[&'’.]", "", _CORP_WORDS.sub(" ", (name or "").lower()))
    return " ".join(re.sub(r"[^\w\s]", " ", name).split())


_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|co|pc|dds|md|bank|insurance)\.?$", re.I)
_PHONEISH = re.compile(r"\d{3}[-.\s]\d{3}[-.\s]\d{4}|\d{3}[-.\s]\d{4}\b|www\.|https?|@|\b\d{5}(?:-\d{4})?\b")


def _header_vendor(lines: list[str]) -> str | None:
    for line in lines[:10]:
        line = " ".join(line.split()).strip(" ,;:")
        if not line or len(line) > 60 or _PHONEISH.search(line):
            continue
        words = line.replace(",", "").split()
        if not (2 <= len(words) <= 5):
            continue
        if _SUFFIX.search(line):
            return line
        if all(w[:1].isupper() or w.isupper() for w in words) and any(c.isalpha() for w in words for c in w):
            return line
    return None


# ---- ids and people ----

_ID_LABELS = ("account", "acct", "policy", "member", "subscriber", "group", "invoice", "order", "claim",
              "reference", "confirmation", "customer", "vin", "case")
_ID_RE = re.compile(r"\b(account|acct|policy|member|subscriber|group|invoice|order|claim|reference|"
                    r"confirmation|customer|vin|case)\s*(?:no\.?|number|#|id)?\s*[:#]?\s*"
                    r"([A-Za-z0-9][A-Za-z0-9\- ]{2,}[A-Za-z0-9])", re.I)
_PEOPLE_RE = re.compile(r"\b((?i:patient|insured|employee(?:'s)? name|member|account holder|policyholder|"
                        r"subscriber|employee))\s*[:#]\s*([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){0,2})")


def _squash(s: str) -> str:
    return re.sub(r"[\s\-./]", "", s).lower()


# The linkable kinds of ID, in the spec's specificity order (B.3). A label names its kind by
# any of its words, so the AI's verbose labels ("Policy Number", "Account No.") and the rule
# extractor's short ones ("policy", "account") mean the same thing.
_ID_KIND_WORDS = (("account", "account"), ("acct", "account"), ("policy", "policy"),
                  ("member", "member"), ("subscriber", "member"), ("customer", "customer"),
                  ("cust", "customer"), ("group", "group"))


def id_kind(label: str) -> str | None:
    """The linkable kind of an ID label, or None for labels that never link documents
    (invoice, order, claim, reference, confirmation, ...)."""
    words = re.findall(r"[a-z]+", (label or "").lower())
    for want, kind in _ID_KIND_WORDS:
        if want in words:
            return kind
    return None


def _extract_ids(flat: str) -> list[dict]:
    out, seen = [], set()
    for m in _ID_RE.finditer(flat):
        label, value = m.group(1).lower(), " ".join(m.group(2).split()).strip(" .:;,#")
        if len(_squash(value)) < 4 or not re.search(r"\d", value):
            continue
        first = value.split(" ", 1)[0]
        if not re.search(r"\d", first) and len(first) > 4:
            continue  # "Member since 2020-…" - the word after the label is prose, not a number
        if DATE_RE.search(value) or re.fullmatch(r"\d{3}[- ]\d{3}[- ]\d{4}", value):
            continue
        if _squash(value) in seen:
            continue
        seen.add(_squash(value))
        out.append({"label": label, "value": value})
        if len(out) >= 10:
            break
    return out


def _extract_people(flat: str) -> list[dict]:
    out, seen = [], set()
    for m in _PEOPLE_RE.finditer(flat):
        name = m.group(2).strip()
        if name.lower() in seen or len(name) < 3:
            continue
        seen.add(name.lower())
        out.append({"label": m.group(1).lower(), "name": name})
    return out


# ---- kind / category scoring ----

_STRONG_KIND = {
    "tax": r"wage and tax statement|form w-?2\b|form 1099|\b1099-(?:misc|nec|int|div|r)\b",
    "insurance": r"insurance identification card|declarations page",
    "medical": r"explanation of benefits",
    "pay": r"pay ?stub|pay ?check stub",
    "bill": r"amount due|balance due|total amount due",
}


def _score_kinds(norm: str, head: str) -> tuple[str, float]:
    scores: dict[str, float] = {}
    for kind, pattern in KIND_EVIDENCE.items():
        rx = re.compile(rf"\b(?:{pattern})", re.I)
        s = min(len(rx.findall(norm)), 3) + 2 * min(len(rx.findall(head)), 3)
        if kind in _STRONG_KIND and re.search(_STRONG_KIND[kind], norm, re.I):
            s += 4
        if s:
            scores[kind] = float(s)
    if not scores:
        return "", 0.0
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    winner, ws = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    strong = winner in _STRONG_KIND and re.search(_STRONG_KIND[winner], norm, re.I)
    if ws < 1.5 * max(runner, 0.01) and not strong:
        return "", 0.0
    return winner, 0.85 if strong else min(0.8, 0.5 + 0.05 * ws)


def _score_category(norm: str, head: str, matched_topics: list[dict],
                    vendor_categories: dict | None) -> tuple[str, float]:
    scores: dict[str, float] = {}
    for cat, words in CATEGORY_EVIDENCE.items():
        hits = sum(1 for w in words if re.search(_evidence(w), norm))
        head_hits = sum(1 for w in words if re.search(_evidence(w), head))
        if hits:
            scores[cat] = hits + head_hits
    for t in matched_topics:
        scores[t["category"]] = scores.get(t["category"], 0) + 2
    for cat, n in (vendor_categories or {}).items():
        if n:
            scores[cat] = scores.get(cat, 0) + 2
    scores.pop("other", None)
    if not scores:
        return "other", 0.4
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    winner, ws = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    if ws < 1.5 * max(runner, 0.01):
        return "other", 0.4
    return winner, min(0.8, 0.5 + 0.03 * ws)


# ---- totals ----

_TOTAL_LABELS = [("amount due", 0.9), ("total due", 0.9), ("balance due", 0.9), ("new balance", 0.9),
                 ("amount paid", 0.8), ("payment amount", 0.8), ("grand total", 0.7), ("total", 0.7)]


def _extract_total(lines: list[str], kind: str) -> tuple[float | None, float]:
    labelled = []
    for label, conf in _TOTAL_LABELS:
        rx = re.compile(rf"\b{re.escape(label)}\b", re.I)
        hits = [ln for ln in lines if rx.search(ln)]
        if hits:
            labelled.append((label, conf, hits))
    if not labelled:
        return None, 0.0
    if kind == "receipt":
        for label, conf, hits in labelled:
            if label in ("total", "grand total", "amount paid"):
                money = MONEY_RE.findall(hits[-1])
                if money:
                    return parse_amount(money[-1]), conf
    label, conf, hits = labelled[0]
    money = MONEY_RE.findall(hits[0])
    if not money:
        # the amount may sit on the next line ("Amount due\n$229.92")
        for i, ln in enumerate(lines):
            if re.search(rf"\b{re.escape(label)}\b", ln, re.I) and i + 1 < len(lines):
                money = MONEY_RE.findall(lines[i + 1])
                if money:
                    break
    return (parse_amount(money[-1]) if money else None), (conf if money else 0.0)


# ---- file-name titles (A.3) ----

_GENERIC_TOKENS = re.compile(r"^.{0,3}$")


def _stem_generic(stem: str) -> bool:
    """Does this file stem itself look like a scanner's name rather than words a person chose?
    (A real title that merely equals its file's name — 'Electronics receipt.pdf' — is a title.)"""
    from .intake import _GENERIC_NAME
    if _GENERIC_NAME.match(stem):
        return True
    if stem.lower() in {"pdf", "jpg", "jpeg", "png", "scan", "img", "image", "doc", "document", "file", "page"}:
        return True
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", stem, re.I) or re.search(r"\d{8,}", stem):
        return True
    tokens = stem.replace("_", " ").replace("-", " ").split()
    if len(stem.split()) == 1:
        tok = stem
        if len(tok) >= 6 and re.search(r"[A-Za-z]", tok) and re.search(r"[\d_\-]", tok):
            return True
        if len(tok) >= 6 and tok.isalpha() and tok.lower() == tok:
            return True
    return len(tokens) >= 2 and all(len(t) <= 3 and t.isupper() for t in tokens)


def looks_like_filename(title: str, original_name: str = "") -> bool:
    """True when a title is really a file name (IMG_4021, pdf, 2104W2, emailreceipt_2024…)."""
    from .intake import _GENERIC_NAME
    title = (title or "").strip()
    if not title:
        return False
    stem = original_name.rsplit(".", 1)[0] if original_name else ""
    if stem and title == stem and _stem_generic(stem):
        return True
    if _GENERIC_NAME.match(title):
        return True
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", title, re.I) or re.search(r"\d{8,}", title):
        return True
    tokens = title.split()
    if len(tokens) == 1:
        tok = tokens[0]
        if len(tok) >= 6 and re.search(r"[A-Za-z]", tok) and re.search(r"[\d_\-]", tok):
            return True
        if len(tok) >= 6 and re.search(r"\d", tok) is None and tok.isalpha() and tok.lower() == tok:
            return True
        # words run together, as file names do: "ABCInsuranceIDCardAuto", "MonthlyStatementOct"
        if len(tok) >= 12 and tok.isalpha() and sum(c.isupper() for c in tok[1:]) >= 2 \
                and any(c.islower() for c in tok):
            return True
    # all-caps abbreviation runs ("UMM UA MP") read as scan gibberish, not a person's title
    if len(tokens) >= 2 and all(len(t) <= 3 and t.isupper() for t in tokens):
        return True
    return False


# ---- the extractor ----

# Well-known companies (spec A.1). Finding one of these in the text only *suggests* it (owner decision Q2:
# with the AI off, PaperJay never creates a company on its own); it files only once the household has that
# company. Agencies, card networks and carriers are left out: they are usually mentioned, not the sender
# (the IRS in a lawyer's fax, the Secretary of State on an insurance card, Visa as a payment method).
KNOWN_VENDORS = [
    "Acme Energy", "Amazon", "American Express", "Amex", "Apple", "AT&T", "Bank of America",
    "Best Buy", "Capital One", "Chase", "Cigna", "Citi", "Citibank", "Comcast", "Xfinity",
    "Costco", "CVS", "Discover", "DTE Energy", "Duke Energy", "Fidelity", "Geico",
    "Google", "Home Depot", "Humana", "Kaiser Permanente",
    "Kroger", "Lowe's", "Microsoft", "Netflix", "Pacific Gas and Electric",
    "PG&E", "Progressive", "Spectrum", "State Farm", "Target",
    "T-Mobile", "United Airlines", "UnitedHealthcare", "Verizon",
    "Walmart", "Wells Fargo", "Blue Cross Blue Shield", "Aetna", "Allstate", "Liberty Mutual",
    "Delta Air Lines", "American Airlines", "Southwest Airlines", "Marriott", "Hilton",
]
# Names that are mostly *mentioned* in other people's documents. Even when the household has one as a
# company, it only counts in the letterhead (first lines), and it is never learned as an alias.
MENTIONED = {norm_name(n) for n in (
    "IRS", "Internal Revenue Service", "Secretary of State", "Department of Motor Vehicles", "DMV",
    "Social Security Administration", "Medicare", "Medicaid", "USPS", "UPS", "FedEx", "Visa",
    "Mastercard", "Department of the Treasury", "State of Michigan")}
LETTERHEAD_LINES = 3


def is_mentioned_name(name: str) -> bool:
    return norm_name(name) in MENTIONED or norm_name(name) in {norm_name(n) for n in KNOWN_VENDORS}


def extract(text: str, title: str = "", original_name: str = "", *, correspondents=(), aliases=None,
            sender: str | None = None, sender_map: dict | None = None, custom_topics_setting=None,
            disabled_topics=(), vendor_categories: dict | None = None, date_order: str = "MDY",
            current: str | None = None) -> dict:
    """The rules fact card for one document. `correspondents`: existing company names; `aliases`:
    {alias name: company name}; `sender_map`: {email domain: company name}; `current`: the company the
    document is filed under now (kept when the text names it)."""
    text = text or ""
    flat = " " + " ".join(text.split()) + " "
    norm = normalize(f"{title}\n{text}")
    head = norm[:1500]
    lines = [ln for ln in (l.strip() for l in text.splitlines()) if ln]
    conf: dict[str, float] = {}
    why: dict[str, str] = {}

    # vendor, best first: (1) the company the document is filed under now, when the text names it;
    # (2) one of the household's companies or aliases; (3) a well-known company (suggestion only);
    # then the sender domain and the letterhead. Names count in the head of the document only (the
    # first ~30 lines), compared in the same normalised form as company names ("P.C.", "&", "Inc").
    vendor, vendor_may_file = "", False
    head_lines = [title] + lines[:30]
    head_names = norm_name(" ".join(head_lines))
    letterhead = norm_name(" ".join(lines[:LETTERHEAD_LINES]))

    def found(written: str):
        nn = norm_name(written)
        if not nn:
            return None
        where = letterhead if nn in MENTIONED else head_names
        return re.search(rf"\b{re.escape(nn)}\b", where)

    household = [(n, n) for n in correspondents] + list((aliases or {}).items())
    if current and any(company == current and found(written) for written, company in
                       household + [(current, current)]):
        vendor, vendor_may_file = current, True
        conf["vendor"] = 0.9
        why["vendor"] = f"the company it is filed under, {current!r}, appears in the text"
    if not vendor:
        best = None
        for written, company in household:
            m = found(written)
            if m:
                score = (-(m.start() // 200), len(norm_name(written)))  # earlier first, then longer
                if best is None or score > best[0]:
                    best = (score, company, written)
        if best:
            vendor, vendor_may_file = best[1], True
            conf["vendor"] = 0.9
            why["vendor"] = f"the company name {best[2]!r} appears in the text"
    if not vendor:
        best = None
        for written in KNOWN_VENDORS:
            m = found(written)
            if m:
                score = (-(m.start() // 200), len(norm_name(written)))
                if best is None or score > best[0]:
                    best = (score, written)
        if best:
            vendor = best[1]  # vendor_may_file stays False: a suggestion until the company exists
            conf["vendor"] = 0.75
            why["vendor"] = f"the well-known company {best[1]!r} appears in the text (suggestion only)"
    if (not vendor or not vendor_may_file) and sender and sender_map:
        domain = sender.rsplit("@", 1)[-1].lower().strip()
        if domain in sender_map:
            vendor, vendor_may_file = sender_map[domain], True
            conf["vendor"] = 0.85
            why["vendor"] = f"the email came from {domain}"
    if not vendor:
        header = _header_vendor(lines)
        if header:
            vendor = header
            conf["vendor"] = 0.5
            why["vendor"] = "the letterhead at the top of the page (suggestion only)"
    if not vendor:
        conf["vendor"] = 0.0

    matched_topics = topics_mod.match(f"{title}\n{text}", custom=custom_topics_setting,
                                      disabled=disabled_topics)
    kind, kind_conf = _score_kinds(norm, head)
    if kind == "invoice" and re.search(r"\b(amount|balance) due\b", norm):
        kind = "bill"  # an invoice with an amount due, sent to a household, is filed as a bill
    conf["kind"] = kind_conf
    category, cat_conf = _score_category(norm, head, matched_topics, vendor_categories)
    conf["category"] = cat_conf

    total, total_conf = _extract_total(lines, kind)
    conf["total"] = total_conf

    from .textutil import match_to_date
    dates, seen_dates = [], set()
    for label, rx in DATE_PATTERNS + EXTRA_DATE_PATTERNS:
        for m in rx.finditer(flat):
            d = ANSWER_DATE_RE.search(m.group(0))
            when = match_to_date(d) if d else None
            if when and (label, when) not in seen_dates:
                seen_dates.add((label, when))
                dates.append({"label": label, "date": when.isoformat()})
    doc_date, doc_conf = None, 0.0
    for label in DOC_DATE_LABELS:
        found = [d["date"] for d in dates if d["label"] == label]
        if found:
            doc_date, doc_conf = found[0], 0.9
            break
    if doc_date is None:
        m = _BARE_DATE.search(text)
        found = None
        if m:
            from .textutil import match_to_date as _mtd
            dm = ANSWER_DATE_RE.search(m.group(0))
            found = _mtd(dm) if dm else None
        if found:
            dates.append({"label": "issued", "date": found.isoformat()})
            doc_date, doc_conf = found.isoformat(), 0.7
    if doc_date is None:
        fallback = find_date(text, date_order)
        if fallback:
            doc_date, doc_conf = fallback.isoformat(), 0.5
    conf["dates"] = 0.9 if dates and doc_conf >= 0.9 else (0.5 if doc_date else 0.0)

    ids, _seen_ids = [], set()
    for ln in lines:  # per line: a value that ran into the next label would not be an ID
        for found in _extract_ids(ln):
            if _squash(found["value"]) not in _seen_ids:
                _seen_ids.add(_squash(found["value"]))
                ids.append(found)
    if ids:
        conf["ids"] = 0.9
    people, _seen_people = [], set()
    for ln in lines:
        for found in _extract_people(ln):
            if found["name"].lower() not in _seen_people:
                _seen_people.add(found["name"].lower())
                people.append(found)
    if people:
        conf["people"] = 0.8

    new_title = ""
    # the title names the company the document is filed under, or one the rules are sure of; never
    # a letterhead guess (that picked a "Bill to" name off an invoice)
    title_vendor = vendor if conf.get("vendor", 0) >= 0.75 else (current or "")
    if looks_like_filename(title, original_name) and title_vendor:
        from .analyze import TYPE_NAMES
        kind_name = TYPE_NAMES.get(kind, "")
        month_year = ""
        if doc_date:
            try:
                d = date.fromisoformat(doc_date)
                month_year = f" {MONTH_NAMES[d.month].title()} {d.year}"
            except ValueError:
                pass
        if kind_name:
            new_title = f"{title_vendor} {kind_name}{month_year}"
        elif matched_topics:
            new_title = f"{title_vendor} {matched_topics[0]['name'].title()}"
        if new_title:
            conf["title"] = 0.6

    return {
        "kind": kind, "category": category, "vendor": vendor, "title": new_title, "summary": "",
        "total": total, "dates": dates, "doc_date": doc_date, "ids": ids, "people": people,
        "topics": [t["name"] for t in matched_topics], "tags": [], "dropped": [],
        "conf": conf, "why": why, "vendor_may_file": vendor_may_file, "version": RULES_VERSION,
    }
