"""Where words are on the page: boxes around the words of a search, for highlighting them on the page images.

The searchable PDF knows where every word it read sits (`pdftotext -bbox`). A search word matches a word on
the page when they're the same, or the page's word starts with it (4 letters or more: "insur" finds
"insurance"), ignoring case, accents and punctuation; numbers match without their spaces and dashes
("EM-4471-2290" for em44712290). Boxes are fractions of the page, so they fit any size of image.
"""
import html
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from . import ocr

_PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">')
_WORD = re.compile(r'<word xMin="([\d.-]+)" yMin="([\d.-]+)" xMax="([\d.-]+)" yMax="([\d.-]+)">(.*?)</word>')
MAX_BOXES = 300
_STOP = set("the and for with from that this what when where which your our you was were are have has had "
            "how much did does about into show find any all".split())


def squash(word: str) -> str:
    w = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in w if c.isalnum() and not unicodedata.combining(c))


def terms_of(text: str) -> list[str]:
    out = []
    for raw in re.split(r"[\s,;]+", text or ""):
        t = squash(raw)
        if t and t not in _STOP and (len(t) >= 3 or t.isdigit()) and t not in out:
            out.append(t)
    return out[:20]


@lru_cache(maxsize=32)
def _words(path: str, mtime: float) -> list[tuple[float, float, list]]:
    r = ocr.run(["pdftotext", "-bbox", path, "-"], 120)
    if r.returncode != 0:
        return []
    try:   # pdftotext gives the words where they show on a turned page, but the page's size unturned
        import pikepdf
        with pikepdf.open(path) as pdf:
            turns = [int(pg.obj.get("/Rotate", 0)) % 360 for pg in pdf.pages]
    except Exception:
        turns = []
    pages = []
    for i, chunk in enumerate(r.stdout.split("<page ")[1:]):
        m = _PAGE.match("<page " + chunk)
        if not m:
            continue
        w, h = float(m.group(1)), float(m.group(2))
        if i < len(turns) and turns[i] in (90, 270):
            w, h = h, w
        words = [(float(a), float(b), float(c), float(d), squash(html.unescape(t))) for a, b, c, d, t in
                 _WORD.findall(chunk)]
        pages.append((w, h, words))
    return pages


def _hit(word: str, term: str) -> bool:
    return word == term or (len(term) >= 4 and word.startswith(term))


def find(pdf: Path, terms: list[str]) -> dict:
    """{"pages": [{"n", "boxes": [[x0, y0, x1, y1] as fractions]}], "count", "terms"}"""
    if not terms or not pdf.exists():
        return {"pages": [], "count": 0, "terms": terms}
    out, count = [], 0
    for n, (w, h, words) in enumerate(_words(str(pdf), pdf.stat().st_mtime), start=1):
        boxes = []
        for x0, y0, x1, y1, word in words:
            if word and any(_hit(word, t) for t in terms):
                boxes.append([round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)])
                if len(boxes) >= MAX_BOXES:
                    break
        if boxes:
            out.append({"n": n, "boxes": boxes})
            count += len(boxes)
    return {"pages": out, "count": count, "terms": terms}
