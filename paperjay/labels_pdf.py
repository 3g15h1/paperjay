"""Printable paper-number labels (spec Phase D task 5).

Sheets for sticky-label stock — Avery 5160 (Letter, 30 up) and L7160 (A4, 21 up) —
plus one 62 mm label per page for a label printer. Each label carries the big paper
number, a short title, the date, and a QR code: a document's label points at the
document (/documents/<id>); a blank-ahead label (no document yet) points at the
library filtered to that number, which finds the document once it has been added
with the number from the label.

PDF by fpdf2 and QR codes by segno — both already in the image (fpdf2 comes with
ocrmypdf; segno is a declared dependency since 0.4.2).
"""
import io
import sqlite3

import segno
from fpdf import FPDF

# name -> page (w, h), label (w, h), first label (x, y), pitch (x, y), columns — millimetres
LAYOUTS = {
    "5160": {"page": (215.9, 279.4), "label": (66.675, 25.4), "first": (4.76, 12.7),
             "pitch": (69.85, 25.4), "cols": 3},
    "7160": {"page": (210.0, 297.0), "label": (63.5, 38.1), "first": (7.25, 15.15),
             "pitch": (66.0, 38.1), "cols": 3},
    "single62": {"page": (62.0, 42.0), "label": (58.0, 38.0), "first": (2.0, 2.0),
                 "pitch": (62.0, 42.0), "cols": 1},
}


def label_for(con: sqlite3.Connection, doc_id: int, base_url: str) -> dict:
    """A label for a document that has a paper number. base_url ends with '/'."""
    row = con.execute("SELECT title, doc_date, added_at, paper_no FROM documents WHERE id = ?",
                      (doc_id,)).fetchone()
    return {"no": row[3], "title": row[0], "date": row[1] or (row[2] or "")[:10],
            "url": f"{base_url}documents/{doc_id}"}


def blank_label(n: int, base_url: str) -> dict:
    """A label for a number with no document yet (printed ahead, stuck on paper)."""
    return {"no": n, "title": None, "date": None, "url": f"{base_url}library?paper_no={n}"}


def qr_png(url: str) -> bytes:
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=1)
    return buf.getvalue()


def _plain(text: str) -> str:
    """Label text in the core font's latin-1: curly quotes and dashes become plain,
    anything else unprintable becomes '?' — a label must never fail to print."""
    text = (text.replace("…", "...").replace("‘", "'").replace("’", "'")
            .replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-"))
    return text.encode("latin-1", "replace").decode("latin-1")


def _fit(pdf: FPDF, text: str, width: float) -> str:
    """text, shortened with '...' until it fits width at the current font."""
    text = _plain(text)
    if pdf.get_string_width(text) <= width:
        return text
    while text and pdf.get_string_width(text + "...") > width:
        text = text[:-1]
    return text + "..."


def _draw_label(pdf: FPDF, lab: dict, x: float, y: float, w: float, h: float, big: bool) -> None:
    qr = min(h - 3, 30 if big else 21)
    pdf.image(io.BytesIO(qr_png(lab["url"])), x=x + w - qr - 1.5, y=y + (h - qr) / 2, w=qr, h=qr)
    text_w = w - qr - 5
    pdf.set_font("helvetica", "B", 30 if big else 24)
    pdf.set_xy(x + 2, y + 1.5)
    pdf.cell(text_w, 11 if big else 9, str(lab["no"]), new_x="LEFT", new_y="TOP")
    pdf.set_font("helvetica", "", 9)
    lines = []
    if lab["title"]:
        lines.append(_fit(pdf, " ".join(str(lab["title"]).split()), text_w))
    if lab["date"]:
        lines.append(_plain(str(lab["date"])))
    if not lines:
        lines.append("PaperJay paper number")
    for i, line in enumerate(lines[:2]):
        pdf.set_xy(x + 2, y + (13.5 if big else 11.5) + i * 4.6)
        pdf.cell(text_w, 4.2, line, new_x="LEFT", new_y="TOP")


def build(labels: list[dict], layout: str) -> bytes:
    """The PDF for these labels (each {no, title, date, url}) on the named stock."""
    spec = LAYOUTS[layout]
    pw, ph = spec["page"]
    lw, lh = spec["label"]
    fx, fy = spec["first"]
    px, py = spec["pitch"]
    per_page = spec["cols"] * int((ph - fy + 0.01) // py) if layout != "single62" else 1
    pdf = FPDF(unit="mm", format=(pw, ph))
    pdf.set_margins(0, 0, 0)
    pdf.set_auto_page_break(False)
    for i, lab in enumerate(labels):
        slot = i % per_page
        if slot == 0:
            pdf.add_page()
        x = fx + (slot % spec["cols"]) * px
        y = fy + (slot // spec["cols"]) * py
        _draw_label(pdf, lab, x, y, lw, lh, big=(layout != "5160"))
    return bytes(pdf.output())
