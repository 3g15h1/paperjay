"""Fill a data folder with made-up household documents, for trying PaperJay and for screenshots.

    python scripts/demo.py ./demo-data          # then: DATA_DIR=./demo-data paperjay serve
                                                # and sign in as "demo" with the password "demo-password"

Every company, name and number here is invented. Documents go through the real upload and OCR pipeline;
then they are filed the way the AI would file them (so this works without an AI server).
"""
import io
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TODAY = date.today()
SOON = TODAY + timedelta(days=9)
LATER = TODAY + timedelta(days=24)
EXPIRES = TODAY + timedelta(days=41)


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in ("/usr/share/fonts/truetype/dejavu/", "/Library/Fonts/", "C:/Windows/Fonts/"):
        if Path(d, name).exists():
            return ImageFont.truetype(str(Path(d, name)), size)
    return ImageFont.load_default(size)


def page(head: str, sub: str, lines: list, accent=(40, 90, 160)) -> bytes:
    """A letter-size page at 200 dpi: a coloured letterhead, then lines. A line is text, or (left, right),
    or ("**", text) for bold, or "" for space."""
    im = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1700, 22), fill=accent)
    d.text((140, 120), head, font=font(64, True), fill=accent)
    d.text((140, 210), sub, font=font(30), fill=(90, 90, 90))
    y = 340
    for line in lines:
        if line == "":
            y += 30
            continue
        if isinstance(line, tuple) and line[0] == "**":
            d.text((140, y), line[1], font=font(36, True), fill=(20, 20, 20))
        elif isinstance(line, tuple):
            d.text((140, y), line[0], font=font(34), fill=(30, 30, 30))
            w = d.textlength(line[1], font=font(34))
            d.text((1560 - w, y), line[1], font=font(34), fill=(30, 30, 30))
        else:
            d.text((140, y), line, font=font(34), fill=(30, 30, 30))
        y += 58
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88, dpi=(200, 200))
    return buf.getvalue()


us = lambda d: d.strftime("%m/%d/%Y")  # noqa: E731
long = lambda d: f"{d:%B} {d.day}, {d.year}"  # noqa: E731

DOCS = [
    ("IMG_4012.jpg", page("Acme Energy", "Residential electric service", [
        ("Statement date", long(TODAY - timedelta(days=12))), ("Account number", "7730 1184 52"), "",
        ("**", "Your monthly summary"), ("Previous balance", "$118.04"), ("Payment received - thank you", "-$118.04"),
        ("Electric delivery", "$61.20"), ("Electric supply", "$73.55"), ("Taxes and fees", "$9.62"), "",
        ("**", f"Amount due {us(SOON)}            $144.37"), "", "Please pay by " + long(SOON) + " to avoid a late fee."]),
     dict(title="Acme Energy electric bill", correspondent="Acme Energy", type="Bill", tags=["Utilities", "Bills"],
          card=dict(kind="bill", category="utilities", total=144.37, dates=[("issued", TODAY - timedelta(days=12)), ("due", SOON)],
                    ids=[("account", "7730 1184 52")], summary="Monthly electric bill."))),
    ("Insurance card.pdf", page("Example Mutual Insurance", "Automobile insurance identification card", [
        ("Policy number", "EM-4471-2290"), ("Effective date", us(EXPIRES - timedelta(days=182))), ("Expiration date", us(EXPIRES)), "",
        ("Named insured", "Alex Rivera"), ("Vehicle", "2021 Compact Sedan"), ("VIN", "1XJ4K21R7MP000417"), "",
        "Keep this card in your vehicle.", "Report claims 24/7 at 1-800-555-0137."], accent=(30, 120, 90)),
     dict(title="Auto insurance card", correspondent="Example Mutual", type="Insurance", tags=["Car", "Insurance"],
          card=dict(kind="insurance", category="insurance", total=None, dates=[("effective", EXPIRES - timedelta(days=182)), ("expires", EXPIRES)],
                    ids=[("Policy number", "EM-4471-2290")], summary="Auto insurance ID card."))),
    ("scan0007.jpg", page("Riverside Family Clinic", "Patient receipt", [
        ("Date of service", us(TODAY - timedelta(days=40))), ("Patient", "Alex Rivera"), ("Visit", "Annual check-up"), "",
        ("Office visit", "$145.00"), ("Lab work", "$62.30"), ("Insurance adjustment", "-$106.65"), "",
        ("**", "Amount paid                          $100.65"), "", "Thank you for choosing Riverside."], accent=(150, 60, 90)),
     dict(title="Clinic visit receipt", correspondent="Riverside Family Clinic", type="Receipt", tags=["Medical"],
          card=dict(kind="receipt", category="medical", total=100.65, dates=[("service", TODAY - timedelta(days=40))], ids=[],
                    summary="Receipt for an annual check-up."))),
    ("receipt.jpg", page("Gadget Store", "Main Street · Sales receipt", [
        ("Order", "GS-118204"), ("Date", us(TODAY - timedelta(days=75))), "",
        ("Phone X Pro 256GB", "$999.00"), ("Protective case", "$39.00"), ("Sales tax", "$61.08"), "",
        ("**", "Total                                      $1,099.08"), "", "Returns accepted within 30 days with receipt."], accent=(80, 80, 80)),
     dict(title="Phone X Pro receipt", correspondent="Gadget Store", type="Receipt", tags=["Electronics"],
          card=dict(kind="receipt", category="shopping", total=1099.08, dates=[("issued", TODAY - timedelta(days=75))],
                    ids=[("order", "GS-118204")], summary="Receipt for a phone and case."))),
    ("IMG_4019.jpg", page("City Water Department", "Water and sewer bill", [
        ("Service address", "12 Maple Court"), ("Billing period", "60 days"), ("Account", "WTR-55021"), "",
        ("Water", "$38.10"), ("Sewer", "$23.10"), "", ("**", f"Amount due by {us(LATER)}      $61.20")], accent=(20, 110, 170)),
     dict(title="Water bill", correspondent="City Water Department", type="Bill", tags=["Utilities", "Bills"],
          card=dict(kind="bill", category="utilities", total=61.20, dates=[("due", LATER)], ids=[("account", "WTR-55021")],
                    summary="Water and sewer bill."))),
    ("lawn invoice.pdf", page("Greenleaf Lawn Care", "Invoice", [
        ("Invoice", "GL-2207"), ("Date", us(TODAY - timedelta(days=20))), "",
        ("Mowing, edging, cleanup (4 visits)", "$180.00"), ("Hedge trimming", "$65.00"), "",
        ("**", "Total due                                  $245.00"), "", "Thank you for your business!"], accent=(60, 130, 50)),
     dict(title="Greenleaf lawn care invoice", correspondent="Greenleaf Lawn Care", type="Invoice", tags=["Home"],
          card=dict(kind="invoice", category="home", total=245.00, dates=[("issued", TODAY - timedelta(days=20))], ids=[("invoice", "GL-2207")],
                    summary="Lawn care invoice."), review=["new company added: Greenleaf Lawn Care"])),
    ("IMG_4033.jpg", page("Riverside Apartments", "Residential lease agreement", [
        ("Tenant", "Alex Rivera"), ("Unit", "4B"), ("Term", f"{us(TODAY - timedelta(days=200))} to {us(TODAY + timedelta(days=165))}"), "",
        "Monthly rent of $1,450.00 is due on the first of each month.", "Landlord allows one cat.", "Parking space 17 is included."], accent=(110, 80, 40)),
     dict(title="Apartment lease", correspondent="Riverside Apartments", type="Contract", tags=["Home"],
          card=dict(kind="contract", category="home", total=None, dates=[("effective", TODAY - timedelta(days=200)), ("expires", TODAY + timedelta(days=165))],
                    ids=[], summary="Apartment lease agreement."))),
]
TAG_COLORS = {"Utilities": "#6ea8fe", "Bills": "#f2c14e", "Car": "#5ccfe6", "Insurance": "#7fd1a1", "Medical": "#f07178",
              "Electronics": "#c792ea", "Home": "#d4a373"}


def main(data_dir: str) -> None:
    from fastapi.testclient import TestClient

    from paperjay import analyze, auth, db
    from paperjay.app import create_app
    from paperjay.config import Config
    from paperjay.library import Library

    data = Path(data_dir).resolve()
    data.mkdir(parents=True, exist_ok=True)
    db.migrate(data / "paperjay.db", data / "backups")
    con = db.connect(data / "paperjay.db")
    if not auth.has_users(con):
        auth.create_user(con, "demo", "demo-password", "admin", "Demo")
    _, token = auth.create_token(con, con.execute("SELECT id FROM users WHERE username = 'demo'").fetchone()[0], "demo script")
    con.close()
    with TestClient(create_app(Config(data_dir=data, consume_dir=None)), headers={"Authorization": f"Token {token}"}) as c:
        ids = []
        for name, content, _ in DOCS:
            r = c.post("/api/documents", files={"file": (name, content)}).json()
            ids.append(r["uploads"][0]["document_id"])
            print("uploaded", name)
        while any(c.get(f"/api/documents/{i}").json()["status"] in ("pending", "processing") for i in ids if i):
            time.sleep(1)
    con = db.connect(data / "paperjay.db")
    lib = Library(con)
    for doc_id, (_, _, f) in zip(ids, DOCS):
        if not doc_id:
            continue
        tag_ids = []
        for t in f["tags"]:
            tid, created = lib.get_or_create_label("tag", t)
            if created:
                lib.set_tag_color(tid, TAG_COLORS.get(t, "#8b8f99"))
            tag_ids.append(tid)
        card = {"kind": f["card"]["kind"], "category": f["card"]["category"], "vendor": f["correspondent"], "title": f["title"],
                "summary": f["card"]["summary"], "total": f["card"]["total"], "tags": [],
                "dates": [{"label": label, "date": d.isoformat()} for label, d in f["card"]["dates"]],
                "ids": [{"label": label, "value": v} for label, v in f["card"]["ids"]], "dropped": []}
        first = next((d for label, d in f["card"]["dates"] if label in ("issued", "service", "effective")), None)
        lib.update(doc_id, by="ai", title=f["title"], tags=tag_ids,
                   correspondent_id=lib.get_or_create_label("correspondent", f["correspondent"])[0],
                   document_type_id=lib.get_or_create_label("document_type", f["type"])[0],
                   **({"doc_date": first.isoformat()} if first else {}))
        if f.get("review"):
            lib.update(doc_id, by="system", needs_review=True, suggestions={"review": f["review"]})
        with db.transaction(con):
            analyze.save_card(con, doc_id, lib.get(doc_id)["content"], "demo", card)
    print(f"demo library ready in {data}: {len([i for i in ids if i])} documents")
    print(json.dumps(Library(con).counts()))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo-data")
