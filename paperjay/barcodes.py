"""QR codes on scanned pages: separator sheets and paper-number labels.

Separator sheets (GET /api/paper/separator.pdf) carry the code SEPARATOR. Put one between documents, scan
the whole stack as one PDF, and PaperJay splits it into one document per stretch of pages; the separator
pages themselves are left out. The stack is moved to the trash once its parts are added (it can be
restored for 30 days).

The blank paper labels PaperJay prints carry ".../library?paper_no=124". When such a label is on the first
page of a document (or of a part of a stack), the document gets that paper number, if it's free.

Looking costs a quick low-resolution render of every page, so it's done for PDFs of up to MAX_PAGES pages,
and only while the `barcodes` setting is on (the default). OpenCV reads the codes (it's already used by the
camera scanner).
"""
import re
import subprocess
import tempfile
from pathlib import Path

SEPARATOR = "PAPERJAY:NEXT"
MAX_PAGES = 200
DPI = 150
_PAPER = re.compile(r"[?&]paper_no=(\d{1,9})\b")


def page_codes(pdf: Path, pages: int | None = None, dpi: int = DPI) -> list[list[str]]:
    """The QR codes on each page, as text, in page order."""
    import cv2
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["pdftoppm", "-gray", "-r", str(dpi), "-png"]
        if pages:
            cmd += ["-l", str(pages)]
        r = subprocess.run(cmd + [str(pdf), str(Path(tmp) / "p")], capture_output=True, timeout=600)
        if r.returncode != 0:
            return []
        detector = cv2.QRCodeDetector()
        out = []
        for png in sorted(Path(tmp).glob("p-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1])):
            img = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
            found = []
            if img is not None:
                try:
                    ok, texts, _, _ = detector.detectAndDecodeMulti(img)
                    found = [t for t in texts if t] if ok else []
                except cv2.error:
                    found = []
                if not found:   # one code fills the page (a separator sheet): the single decoder finds it
                    try:
                        text, _, _ = detector.detectAndDecode(img)
                        found = [text] if text else []
                    except cv2.error:
                        pass
            out.append(found)
        return out


def paper_number(codes: list[str]) -> int | None:
    for c in codes:
        m = _PAPER.search(c)
        if m and int(m.group(1)) >= 1:
            return int(m.group(1))
    return None


def parts(codes_by_page: list[list[str]]) -> list[list[int]]:
    """Page indexes of each document in a stack, separator pages left out. One part means: no separators."""
    out, cur = [], []
    for i, codes in enumerate(codes_by_page):
        if SEPARATOR in codes:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(i)
    if cur:
        out.append(cur)
    return out


def has_separator(codes_by_page: list[list[str]]) -> bool:
    return any(SEPARATOR in codes for codes in codes_by_page)
