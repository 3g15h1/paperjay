"""Find the document's date in its text, the way Paperless-ngx does: the first plausible date wins.

Numeric dates like 03/04/2025 are read in the configured order (MDY in the US, DMY most elsewhere);
when the first number is over 12 there is only one way to read it. Dates before 1990 or more than a
year in the future are ignored (birth dates, expiry dates far ahead, OCR noise).
"""
import re
from datetime import date, timedelta

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")),
    ("num", re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")),
    ("mdy_name", re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)),
    ("dmy_name", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON},?\s+(\d{{4}})\b", re.I)),
]
EARLIEST = date(1990, 1, 1)


def _year(y: str) -> int:
    n = int(y)
    return n + (2000 if n < 70 else 1900) if len(y) == 2 else n


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _read(kind: str, g: tuple, order: str) -> date | None:
    if kind == "iso":
        return _make(int(g[0]), int(g[1]), int(g[2]))
    if kind == "num":
        a, b, y = int(g[0]), int(g[1]), _year(g[2])
        if a > 12:
            return _make(y, b, a)
        if b > 12:
            return _make(y, a, b)
        return _make(y, a, b) if order == "MDY" else _make(y, b, a)
    if kind == "mdy_name":
        return _make(int(g[2]), MONTHS[g[0][:3].lower()], int(g[1]))
    return _make(int(g[2]), MONTHS[g[1][:3].lower()], int(g[0]))


def find_date(text: str, order: str = "MDY", today: date | None = None, max_chars: int = 20000) -> date | None:
    today = today or date.today()
    latest = today + timedelta(days=366)
    found = []
    text = text[:max_chars]
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            d = _read(kind, m.groups(), order)
            if d and EARLIEST <= d <= latest:
                found.append((m.start(), d))
    return min(found)[1] if found else None
