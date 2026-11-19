"""Warranties: when a purchase's warranty ends.

Read from the document's own text: a length ("2-year limited warranty", "Warranty: 24 months", "90 day
guarantee", "extended protection plan, 3 years") is added to the purchase date (the document's date); an
end date written out ("warranty valid until 03/14/2027") is taken as it is, read in the household's date
order. "Lifetime warranty" never ends. When several are written (the maker's year and an extended plan), the
one that runs longest counts. "No warranty" or "sold as is" means none.

A person can set it for one document (documents.warranty_until: a date, or "none"); that always wins.
Warranty ends show in Coming up and the calendar, keep a receipt's paper until then (keeping.py), and are
listed on the Warranties page.
"""
import re
from datetime import date, timedelta
from functools import lru_cache

from .dates import _PATTERNS as DATE_PATTERNS, _read as read_date

LIFETIME = "9999-12-31"
NONE = "none"
_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "twelve": 12, "eighteen": 18, "twenty-four": 24, "thirty": 30, "thirty-six": 36,
        "sixty": 60, "ninety": 90}
_N = r"(\d{1,3}|" + "|".join(sorted(_NUM, key=len, reverse=True)) + r")"
_UNIT = r"(years?|yrs?|months?|mos?|days?)"
_WHAT = r"(?:warrant(?:y|ee)|guarantee|protection plan|service plan|coverage)"
_LENGTH = [
    # "2-year limited warranty", "12 month manufacturer's warranty", "3 yr parts & labor warranty"
    re.compile(rf"\b{_N}[\s-]*{_UNIT}[\s-]+(?:[a-z'&]+[\s-]+){{0,4}}?{_WHAT}\b", re.I),
    # "warranty: 24 months", "warranty period of two years", "protection plan, 3 years",
    # "this guarantee is valid for 5 years"
    re.compile(rf"\b{_WHAT}[\s,:\-–]+(?:[a-z']+[\s,]+){{0,4}}?{_N}[\s-]*{_UNIT}\b", re.I),
]
# "register your warranty within 30 days", "return within 30 days": a deadline, not how long it lasts
_DEADLINE = re.compile(r"\b(?:within|register|registration|return|returns|claim|claims|notify|before)\b", re.I)
_UNTIL = re.compile(rf"\b{_WHAT}[^.\n]{{0,40}}?\b(?:valid|expires?|ends?|until|through|thru|to)\b[^.\n]{{0,30}}", re.I)
_LIFETIME = re.compile(rf"\blifetime\s+(?:limited\s+)?{_WHAT}\b", re.I)
_NO = re.compile(rf"\bno\s+{_WHAT}\b|\bsold\s+as[\s-]+is\b|\bwithout\s+(?:any\s+)?{_WHAT}\b", re.I)
_CLUE = re.compile(r"warrant|guarantee|protection plan|service plan", re.I)


def _months(n: str, unit: str) -> float:
    n_val = int(n) if n.isdigit() else _NUM[n.lower()]
    u = unit.lower()
    if u.startswith("y"):
        return n_val * 12
    if u.startswith("m"):
        return n_val
    return n_val / 30.4375


def add_months(d: date, months: float) -> date:
    if months != int(months):   # a number of days
        return d + timedelta(days=round(months * 30.4375))
    y, m = divmod(d.month - 1 + int(months), 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError("no such day")


@lru_cache(maxsize=8192)
def detect(text: str, purchased: str | None, order: str = "MDY") -> dict | None:
    """{"until": YYYY-MM-DD (LIFETIME = never ends), "phrase": the words it was read from} or None."""
    if not text or not _CLUE.search(text):
        return None
    flat = " ".join(text.split())
    if _NO.search(flat) and not _LIFETIME.search(flat):
        return None
    found = []
    m = _LIFETIME.search(flat)
    if m:
        found.append((LIFETIME, m.group(0)))
    for m in _UNTIL.finditer(flat):
        for kind, rx in DATE_PATTERNS:
            d = next((read_date(kind, x.groups(), order) for x in rx.finditer(m.group(0))), None)
            if d:
                found.append((d.isoformat(), m.group(0).strip()))
                break
    if purchased:
        start = date.fromisoformat(purchased)
        for rx in _LENGTH:
            for m in rx.finditer(flat):
                if _DEADLINE.search(m.group(0)):
                    continue
                months = _months(m.group(1), m.group(2))
                if 0 < months <= 12 * 50:
                    found.append((add_months(start, months).isoformat(), m.group(0).strip()))
    if not found:
        return None
    until, phrase = max(found)
    return {"until": until, "phrase": phrase[:80]}


def end(content: str, purchased: str | None, override: str | None, order: str = "MDY") -> dict | None:
    """The warranty a document has: a person's own setting, or what its text says.
    {"until", "phrase", "source": "person" | "text"} or None."""
    if override == NONE:
        return None
    if override:
        return {"until": override, "phrase": "", "source": "person"}
    found = detect(content or "", purchased, order)
    return {**found, "source": "text"} if found else None
