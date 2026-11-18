"""Paper numbers (spec Phase D): assignment at upload/scan, uniqueness, reservations."""
import io
import threading

import pytest
from fastapi.testclient import TestClient

from paperjay import db, intake, papers, settings
from paperjay.app import create_app
from paperjay.config import Config
from conftest import sign_in_client

PDF = b"%PDF-1.4 a made-up document"


def test_setting_defaults_off(con):
    assert settings.get(con, "paper_numbers") is False
    settings.save(con, {"paper_numbers": True})
    assert settings.get(con, "paper_numbers") is True


def test_next_free_counts_documents_and_reservations(con, add_doc):
    assert papers.next_free(con) == 1
    add_doc(paper_no=7)
    assert papers.next_free(con) == 8
    papers.reserve_blanks(con, 20, 29)
    assert papers.next_free(con) == 30


def test_add_with_explicit_number(lib, add_doc):
    doc_id = add_doc(paper_no=124)
    assert lib.get(doc_id)["paper_no"] == 124
    assert papers.holder(lib.con, 124) == doc_id


@pytest.mark.parametrize("bad", [0, -3, True, "12", 2.5])
def test_add_rejects_bad_numbers(lib, add_doc, bad):
    with pytest.raises(ValueError):
        add_doc(paper_no=bad)


def test_add_rejects_a_taken_number(lib, add_doc):
    add_doc(paper_no=5)
    with pytest.raises(ValueError, match="paper number 5 already belongs"):
        add_doc(name="other.pdf", paper_no=5)


def test_claims_are_sequential(lib, add_doc):
    a = add_doc(claim_paper=True)
    b = add_doc(name="b.pdf", claim_paper=True)
    assert (lib.get(a)["paper_no"], lib.get(b)["paper_no"]) == (1, 2)


def test_ingest_explicit_and_claimed(con, store):
    up = intake.ingest(con, store, io.BytesIO(PDF), "a.pdf", source="web", max_bytes=1 << 20,
                       paper_no=42)
    assert up.outcome == "accepted" and up.paper_no == 42
    up = intake.ingest(con, store, io.BytesIO(PDF + b"2"), "b.pdf", source="web", max_bytes=1 << 20,
                       paper_next=True)
    assert up.outcome == "accepted" and up.paper_no == 43


def test_ingest_email_gets_no_number_unless_it_passes_one(con, store):
    up = intake.ingest(con, store, io.BytesIO(PDF), "a.pdf", source="email", max_bytes=1 << 20)
    assert up.outcome == "accepted" and up.paper_no is None
    up = intake.ingest(con, store, io.BytesIO(PDF + b"3"), "c.pdf", source="email", max_bytes=1 << 20,
                       paper_no=9)
    assert up.paper_no == 9


def test_ingest_taken_number_is_a_clear_rejection(con, store, add_doc):
    add_doc(paper_no=5)
    up = intake.ingest(con, store, io.BytesIO(PDF), "a.pdf", source="web", max_bytes=1 << 20,
                       paper_no=5)
    assert up.outcome == "rejected"
    assert "paper number 5 already belongs" in up.message
    assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_reserved_number_claimed_exactly_once(con, store, add_doc):
    papers.reserve_blanks(con, 10, 12)
    up = intake.ingest(con, store, io.BytesIO(PDF), "a.pdf", source="web", max_bytes=1 << 20,
                       paper_no=11)
    assert up.outcome == "accepted" and up.paper_no == 11
    up = intake.ingest(con, store, io.BytesIO(PDF + b"4"), "d.pdf", source="web", max_bytes=1 << 20,
                       paper_no=11)
    assert up.outcome == "rejected" and "already belongs" in up.message
    # a claim for the next free number steps over the whole reserved range
    assert papers.next_free(con) == 13


def test_concurrent_claims_never_share_a_number(tmp_path, con, store):
    path = tmp_path / "paperjay.db"
    results, errors = [], []

    def claim(i):
        try:
            c = db.connect(path)
            try:
                up = intake.ingest(c, store, io.BytesIO(PDF + str(i).encode()), f"t{i}.pdf",
                                   source="web", max_bytes=1 << 20, paper_next=True)
                results.append(up.paper_no)
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(results) == list(range(1, 9))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)),
                    follow_redirects=False) as c:
        sign_in_client(c)
        yield c


def _upload(client, name="bill.pdf", **data):
    return client.post("/api/documents",
                       files={"file": (name, io.BytesIO(PDF + name.encode()), "application/pdf")},
                       data=data)


def test_api_next_and_claim_gated_by_the_setting(client):
    assert client.get("/api/paper/next").json() == {"enabled": False, "next": 1}
    r = _upload(client, paper_next="true")  # setting off: no claim
    assert r.json()["uploads"][0]["paper_no"] is None
    client.put("/api/settings", json={"paper_numbers": True})
    assert client.get("/api/paper/next").json() == {"enabled": True, "next": 1}
    r = _upload(client, "two.pdf", paper_next="true")
    assert r.json()["uploads"][0]["paper_no"] == 1
    r = _upload(client, "three.pdf", paper_no="50")
    assert r.json()["uploads"][0]["paper_no"] == 50
    assert client.get("/api/paper/next").json()["next"] == 51


def test_upload_page_prefill_is_not_an_explicit_claim():
    """REVIEW-0.4.2 #2: the prefilled next number is a suggestion, not the person's
    claim — an untouched box must send paper_next (the race-safe server claim), on
    the upload form and the scan page alike. The suite has no JS harness, so this
    pins the shipped source of upload.js."""
    import re
    from pathlib import Path
    js = (Path(__file__).parent.parent / "paperjay" / "static" / "js" / "upload.js").read_text()
    assert re.search(r"let paperTyped = false;", js)  # starts unclaimed
    assert js.count("paperTyped = true") == 1  # only the edit listener sets it
    assert 'addEventListener("input", () => { paperTyped = true; })' in js
    assert 'form.append("paper_next", "true")' in js  # upload form falls back to the claim
    assert "body.paper_next = true" in js  # scan page too


def test_api_taken_number_rejected_with_message(client):
    client.put("/api/settings", json={"paper_numbers": True})
    assert _upload(client, paper_no="5").json()["uploads"][0]["outcome"] == "accepted"
    r = _upload(client, "dupe.pdf", paper_no="5")
    up = r.json()["uploads"][0]
    assert up["outcome"] == "rejected" and "paper number 5 already belongs" in up["message"]


# ---- task 3: find it by number ----

def test_search_by_hash_and_paper_phrase(lib, add_doc):
    add_doc(title="Water bill", paper_no=124)
    add_doc(name="b.pdf", title="W-2 form", paper_no=57)
    add_doc(name="c.pdf", title="Water bill", paper_no=125)
    for q in ("#124", "paper 124", "Paper no. 124", "paper #124"):
        r = lib.search(q)
        assert [d["id"] for d in r["results"]] and r["total"] == 1, q
        assert r["results"][0]["paper_no"] == 124, q
    assert lib.search("#999")["total"] == 0
    assert lib.search("paper")["total"] == 0  # no digits: not a paper lookup, no crash


def test_suggest_paper_group_uses_real_numbers(con, add_doc):
    from paperjay import entities
    add_doc(paper_no=124)
    add_doc(name="b.pdf", paper_no=125)
    add_doc(name="c.pdf", paper_no=57)
    groups = entities.suggest(con, "#12")["groups"]
    paper = [g for g in groups if g["kind"] == "paper"]
    assert paper and {i["id"] for i in paper[0]["items"]} == {124, 125}


def _asker(con):
    from paperjay import ask
    idx = ask.Index()
    idx.refresh(con)
    return ask.Asker(con, index=idx)


def _ready(lib, data, title, text, **kw):
    return lib.add(title=title, checksum=data.hex().ljust(64, "0")[:64],
                   original_name="doc.pdf", original_ext=".pdf", mime_type="application/pdf",
                   size=10, content=text, status="ready", doc_date="2026-05-01", **kw)


W2_TEXT = "W-2 Wage and Tax Statement. Employer: Acme Corp. Wages and tips. Copy for the employee's records."
WATER_TEXT = "City Water utility bill. Account summary for June. Amount due for water service at Maple Street."


def test_ask_where_is_the_paper_copy(lib, con):
    from paperjay import settings
    settings.save(con, {"paper_numbers": True})
    _ready(lib, b"w2", "2025 W-2", W2_TEXT, paper_no=57)
    _ready(lib, b"water", "Water bill June", WATER_TEXT)
    asker = _asker(con)
    r, mode, _ = asker.prepare("Where's the paper copy of my W-2?", None, None, None, False)
    assert mode == "facts" and r["extractive"]["text"] == "Paper no. 57 [1]."
    r, mode, _ = asker.prepare("Where is the paper copy of the water bill?", None, None, None, False)
    assert mode == "facts" and "has no paper number recorded" in r["extractive"]["text"]


def test_ask_by_number_and_account_hash_guard(lib, con):
    from paperjay import settings
    settings.save(con, {"paper_numbers": True})
    _ready(lib, b"w2", "2025 W-2", W2_TEXT, paper_no=57)
    asker = _asker(con)
    r, mode, _ = asker.prepare("#57", None, None, None, False)
    assert mode == "facts" and r["extractive"]["text"] == "“2025 W-2” is Paper no. 57 [1]."
    r, mode, _ = asker.prepare("paper 57", None, None, None, False)
    assert mode == "facts" and "Paper no. 57" in r["extractive"]["text"]
    bare = {"llm_docs": [], "refused": False, "listing": False, "sources": [], "scoped": None}
    assert asker.paper_answer("What is account #57 about?", bare) is None  # not the shelf syntax


def test_ask_paper_answer_ignores_ordinary_paper_questions(lib, con):
    """REVIEW-0.4.2 #1: a question that merely contains 'paper' is not a paper-copy
    lookup — not even when a digit follows 'paper' somewhere inside it."""
    from paperjay import settings
    settings.save(con, {"paper_numbers": True})
    _ready(lib, b"w2", "2025 W-2", W2_TEXT, paper_no=57)
    _ready(lib, b"water", "Water bill June", WATER_TEXT)
    asker = _asker(con)
    bare = {"llm_docs": [], "refused": False, "listing": False, "sources": [], "scoped": None}
    assert asker.paper_answer("What's the account number on my paper water bill?", bare) is None
    assert asker.paper_answer("Is there a paper water bill with no amount due?", bare) is None
    assert asker.paper_answer("How much was toilet paper 57 pack?", bare) is None
    # end to end: the toilet-paper question is not answered with the W-2's number
    r, mode, _ = asker.prepare("How much was toilet paper 57 pack?", None, None, None, False)
    assert (r.get("extractive") or {}).get("text", "") != "“2025 W-2” is Paper no. 57 [1]."
    # the genuine lookup still answers, in the same library
    r, mode, _ = asker.prepare("Where's the paper copy of my W-2?", None, None, None, False)
    assert mode == "facts" and r["extractive"]["text"] == "Paper no. 57 [1]."


def test_ask_paper_answer_needs_the_setting(lib, con):
    """REVIEW-0.4.2 #1: with paper_numbers off, Ask gives no paper answers at all."""
    _ready(lib, b"w2", "2025 W-2", W2_TEXT, paper_no=57)
    asker = _asker(con)
    bare = {"llm_docs": [], "refused": False, "listing": False, "sources": [], "scoped": None}
    assert asker.paper_answer("paper 57", bare) is None
    assert asker.paper_answer("#57", bare) is None


# ---- task 4: import/export round-trip ----

def test_paperless_meta_maps_asn_to_paper_no():
    from paperjay import importer
    names = {"correspondents": {}, "document_types": {}, "tags": {}, "custom_fields": {}}
    meta = importer.paperless_meta({"id": 9, "title": "Tax form", "archive_serial_number": 231,
                                      "notes": [], "custom_fields": []}, names)
    assert meta["paper_no"] == 231
    assert all("Archive serial number" not in n for n in meta["notes"])
    meta = importer.paperless_meta({"id": 10, "title": "No ASN"}, names)
    assert meta["paper_no"] is None


def test_import_one_keeps_asn_and_survives_a_clash(con, store, tmp_path):
    from paperjay import importer
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4 imported one")
    meta = {"id": 501, "title": "Imported tax form", "date": "2025-01-01", "added": "2025-01-02T00:00:00Z",
            "correspondent": None, "document_type": None, "tags": [], "notes": [], "content": "tax text",
            "original_name": "a.pdf", "mime_type": "application/pdf", "pages": 1, "paper_no": 231}
    doc_id = importer.import_one(con, store, meta, src, None, None, "test")
    from paperjay.library import Library
    assert Library(con).get(doc_id)["paper_no"] == 231
    src2 = tmp_path / "b.pdf"
    src2.write_bytes(b"%PDF-1.4 imported two")
    meta2 = dict(meta, id=502, title="Second import", paper_no=231)  # same ASN: imports unnumbered
    doc_id2 = importer.import_one(con, store, meta2, src2, None, None, "test")
    assert Library(con).get(doc_id2)["paper_no"] is None


def test_backfill_asn_notes(lib, add_doc):
    a = add_doc(title="Old import A")
    lib.add_note(a, "Imported from Paperless-ngx\nArchive serial number: 77")
    b = add_doc(name="b.pdf", title="Old import B")
    lib.add_note(b, "Imported from Paperless-ngx\nArchive serial number: 78")
    c = add_doc(name="c.pdf", title="Old import C")
    lib.add_note(c, "Imported from Paperless-ngx\nArchive serial number: 78")  # two claimants: neither wins
    d = add_doc(name="d.pdf", title="Old import D", paper_no=79)
    lib.add_note(d, "Imported from Paperless-ngx\nArchive serial number: 80")  # already numbered: untouched
    assert papers.backfill_asn_notes(lib.con) == 1  # only A's claim is unique and free
    assert lib.get(a)["paper_no"] == 77
    assert lib.get(b)["paper_no"] is None and lib.get(c)["paper_no"] is None
    assert lib.get(d)["paper_no"] == 79
    assert papers.backfill_asn_notes(lib.con) == 0  # idempotent
    assert any(n["body"].startswith("Imported from Paperless") for n in lib.notes(a))  # the note stays


def test_backfill_never_renumbers_a_cleared_paper_number(lib, add_doc):
    """REVIEW-0.4.2 #3: the backfill gives a number, a person clears it on
    purpose — no later startup (the step is one-time) may bring it back."""
    a = add_doc(title="Old import A")
    lib.add_note(a, "Imported from Paperless-ngx\nArchive serial number: 12")
    assert papers.backfill_asn_notes(lib.con) == 1
    assert lib.get(a)["paper_no"] == 12
    lib.update(a, paper_no=None)  # cleared on purpose
    assert lib.get(a)["paper_no"] is None
    assert papers.backfill_asn_notes(lib.con) == 0
    assert lib.get(a)["paper_no"] is None


def test_backfill_skips_a_number_cleared_before_it_ever_ran(lib, add_doc):
    """REVIEW-0.4.2 #3: the guard, not just the one-time flag — a document that
    was numbered and cleared before any backfill is skipped on the first run."""
    a = add_doc(title="Old import A", paper_no=12)
    lib.add_note(a, "Imported from Paperless-ngx\nArchive serial number: 12")
    lib.update(a, paper_no=None)
    assert papers.backfill_asn_notes(lib.con) == 0
    assert lib.get(a)["paper_no"] is None


def test_backfill_still_numbers_a_fresh_import_exactly_once(lib, add_doc):
    """REVIEW-0.4.2 #3: a pre-0.4.2-style document (ASN note, never numbered)
    still receives its number — exactly once."""
    a = add_doc(title="Old import A")
    lib.add_note(a, "Imported from Paperless-ngx\nArchive serial number: 21")
    assert papers.backfill_asn_notes(lib.con) == 1
    assert lib.get(a)["paper_no"] == 21
    assert papers.backfill_asn_notes(lib.con) == 0
    assert lib.get(a)["paper_no"] == 21


def test_export_import_round_trip_keeps_paper_no(con, store, tmp_path, add_doc):
    import json

    from paperjay import export, importer
    from paperjay.library import Library
    from paperjay.store import FileStore
    doc_id = add_doc(title="Round trip bill", content="Acme bill text", paper_no=88)
    out = tmp_path / "export"
    export.export(con, store, out)
    m = json.loads((out / "manifest.json").read_text())
    assert next(d for d in m["documents"] if d["id"] == doc_id)["paper_no"] == 88
    db.migrate(tmp_path / "q.db", tmp_path / "qb")
    res = importer.import_export(tmp_path / "q.db", FileStore(tmp_path / "media2"), out)
    assert res["imported"] == 1 and res["failed"] == 0
    assert Library(db.connect(tmp_path / "q.db")).get(doc_id)["paper_no"] == 88


# ---- tasks 5+6: printable labels and reserved ranges ----

def _pages(pdf_bytes):
    import pikepdf
    return len(pikepdf.open(io.BytesIO(pdf_bytes)).pages)


def _labels(n, start=1):
    return [{"no": i, "title": f"Made-up document {i}", "date": "2025-08-14",
             "url": f"http://labels.test/documents/{i}"} for i in range(start, start + n)]


def test_label_sheet_page_counts():
    from paperjay import labels_pdf
    assert _pages(labels_pdf.build(_labels(30), "5160")) == 1
    assert _pages(labels_pdf.build(_labels(31), "5160")) == 2
    assert _pages(labels_pdf.build(_labels(21), "7160")) == 1
    assert _pages(labels_pdf.build(_labels(22), "7160")) == 2
    assert _pages(labels_pdf.build(_labels(3), "single62")) == 3


def _decode_qr(pdf_bytes):
    import cv2
    import numpy as np
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    img = doc[0].render(scale=3).to_pil().convert("RGB")
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(np.array(img))
    return data


def test_label_qr_decodes_to_the_document_url(con, add_doc):
    from paperjay import labels_pdf
    doc_id = add_doc(title="Labelled bill", paper_no=31, doc_date="2025-08-14")
    lab = labels_pdf.label_for(con, doc_id, "http://labels.test/")
    assert lab == {"no": 31, "title": "Labelled bill", "date": "2025-08-14",
                   "url": f"http://labels.test/documents/{doc_id}"}
    assert _decode_qr(labels_pdf.build([lab], "single62")) == lab["url"]
    blank = labels_pdf.blank_label(64, "http://labels.test/")
    assert blank["url"] == "http://labels.test/library?paper_no=64"
    assert _decode_qr(labels_pdf.build([blank], "single62")) == blank["url"]


def test_reserve_blanks_skips_used_and_already_reserved(con, add_doc):
    add_doc(paper_no=12)
    assert papers.reserve_blanks(con, 10, 14) == [(10, 11), (13, 14)]
    assert papers.reserve_blanks(con, 10, 16) == [(15, 16)]  # only the fresh span
    assert papers.reserve_blanks(con, 10, 16) == []
    rows = con.execute("SELECT first, last FROM paper_reservations ORDER BY first").fetchall()
    assert [tuple(r) for r in rows] == [(10, 11), (13, 14), (15, 16)]


def test_labels_api_and_reservation(client):
    doc_id = _upload(client, "labelled.pdf", paper_no="7").json()["uploads"][0]["document_id"]
    r = client.get("/api/paper-labels.pdf", params={"ids": doc_id})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert _pages(r.content) == 1
    plain = _upload(client, "plain.pdf").json()["uploads"][0]["document_id"]
    r = client.get("/api/paper-labels.pdf", params={"ids": plain})
    assert r.status_code == 400 and "no paper number" in r.json()["detail"]
    # a blank-ahead range reserves its numbers, once
    assert client.get("/api/paper/next").json()["next"] == 8
    r = client.get("/api/paper-labels.pdf", params={"from": 8, "to": 10, "layout": "7160"})
    assert r.status_code == 200 and _pages(r.content) == 1
    assert client.get("/api/paper/next").json()["next"] == 11
    client.get("/api/paper-labels.pdf", params={"from": 8, "to": 10})
    con = db.connect(client.app.state.config.db_path)
    try:
        rows = con.execute("SELECT first, last FROM paper_reservations").fetchall()
    finally:
        con.close()
    assert [tuple(r) for r in rows] == [(8, 10)]
    r = client.get("/api/paper-labels.pdf", params={"from": 5, "to": 2})
    assert r.status_code == 400


# ---- task 2: document page field + bulk numbering ----

def test_assign_paper_numbers_in_order(lib, add_doc):
    a = add_doc()
    b = add_doc(name="b.pdf", paper_no=3)  # already numbered: skipped, keeps its number
    c = add_doc(name="c.pdf")
    papers.reserve_blanks(lib.con, 4, 4)    # next free is 5, not 4
    assigned = lib.assign_paper_numbers([a, b, c])
    assert assigned == {a: 5, c: 6}
    assert lib.get(b)["paper_no"] == 3
    assert lib.assign_paper_numbers([a, c]) == {}  # nothing left to number


def test_assign_paper_numbers_skips_trashed(lib, add_doc):
    a = add_doc()
    lib.trash(a)
    assert lib.assign_paper_numbers([a]) == {}
    assert lib.get(a)["paper_no"] is None


def test_bulk_number_in_order_api(client):
    ids = []
    for name in ("one.pdf", "two.pdf", "three.pdf"):
        up = _upload(client, name).json()["uploads"][0]
        ids.append(up["document_id"])
    r = client.post("/api/documents/bulk", json={"ids": ids, "action": "number_in_order"})
    assert r.status_code == 200
    assert r.json()["assigned"] == {str(ids[0]): 1, str(ids[1]): 2, str(ids[2]): 3}
    docs = client.get("/api/documents").json()["results"]
    assert {d["id"]: d["paper_no"] for d in docs} == {ids[0]: 1, ids[1]: 2, ids[2]: 3}


def test_patch_paper_no_and_taken_conflict(client):
    first = _upload(client, "first.pdf").json()["uploads"][0]["document_id"]
    second = _upload(client, "second.pdf").json()["uploads"][0]["document_id"]
    r = client.patch(f"/api/documents/{first}", json={"paper_no": 57})
    assert r.status_code == 200 and r.json()["paper_no"] == 57
    r = client.patch(f"/api/documents/{second}", json={"paper_no": 57})
    assert r.status_code == 400 and "already belongs" in r.json()["detail"]
    r = client.patch(f"/api/documents/{first}", json={"paper_no": None})
    assert r.status_code == 200 and r.json()["paper_no"] is None
