"""The tax pack (0.7.0): which documents belong to a tax year, the totals, the zip and its spreadsheet,
and the Library's "Download list"."""
import csv
import io
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from paperjay import analyze, db, settings, taxpack
from paperjay.app import create_app
from paperjay.config import Config
from conftest import sign_in_client


def _doc(con, add_doc, lib, title, day, category, total=None, kind="receipt", text="", topics=(), tags=()):
    doc_id = add_doc(name=f"{title}.pdf", title=title, doc_date=day, status="ready",
                     content=text or f"{title} paid {total} on {day}")
    lib.update(doc_id, by="system", category=category, tags=list(tags))
    card = {"kind": kind, "category": category, "vendor": "", "title": title, "summary": "", "total": total,
            "tags": [], "dates": [{"label": "issued", "date": day}], "ids": [], "dropped": []}
    with db.transaction(con):
        analyze.save_card(con, doc_id, lib.get(doc_id)["content"], "test", card)
        for t in topics:
            con.execute("INSERT INTO document_topics(document_id, topic) VALUES (?, ?)", (doc_id, t))
    return doc_id


def test_period_and_labels():
    assert taxpack.period(2025) == (date(2025, 1, 1), date(2025, 12, 31), "2025")
    assert taxpack.period(2025, "04-06") == (date(2025, 4, 6), date(2026, 4, 5), "2025–26")
    assert taxpack.period(2025, "07-01")[2] == "2025–26"
    assert taxpack.current_year(date(2026, 11, 19)) == 2025
    assert taxpack.current_year(date(2026, 3, 1), "04-06") == 2024     # 2025–26 hasn't ended yet
    assert taxpack.current_year(date(2026, 4, 6), "04-06") == 2025


@pytest.mark.parametrize("bad", ["1-1", "13-01", "02-29", "02-30", "", "jan"])
def test_tax_year_start_is_validated(con, bad):
    with pytest.raises(ValueError):
        settings.save(con, {"tax_year_start": bad})
    settings.save(con, {"tax_year_start": "04-06"})
    assert settings.get(con, "tax_year_start") == "04-06"


def test_gather_picks_the_year_and_late_tax_forms(con, add_doc, lib):
    a = _doc(con, add_doc, lib, "Pharmacy", "2025-03-02", "medical", 42.18)
    _doc(con, add_doc, lib, "Pharmacy next year", "2026-02-01", "medical", 10.0)
    w2 = _doc(con, add_doc, lib, "W-2", "2026-01-24", "income", 58240.0, kind="tax",
              text="Form W-2 Wage and Tax Statement 2025")
    _doc(con, add_doc, lib, "W-2 for 2026", "2026-01-24", "income", 1.0, kind="tax", text="Form W-2 2026")
    _doc(con, add_doc, lib, "Old", "2024-12-31", "medical", 5.0)
    docs = {d["id"]: d for d in taxpack.gather(con, 2025)["documents"]}
    assert set(docs) == {a, w2}
    assert docs[a]["counted"] and docs[a]["amount"] == 42.18
    assert not docs[w2]["counted"] and docs[w2]["late_form"]
    assert "names 2025" in docs[w2]["note"] and "not spending" in docs[w2]["note"]


def test_choose_and_totals(con, add_doc, lib):
    tag = lib.create_label("tag", "Taxes")
    med = _doc(con, add_doc, lib, "Clinic", "2025-05-01", "medical", 100.65)
    gift = _doc(con, add_doc, lib, "Food bank", "2025-06-14", "other", 250.0, topics=["donation"])
    tagged = _doc(con, add_doc, lib, "Car tax", "2025-07-01", "vehicle", 80.0, tags=[tag])
    _doc(con, add_doc, lib, "Groceries", "2025-07-02", "food", 12.0)
    est = _doc(con, add_doc, lib, "Dentist estimate", "2025-08-01", "medical", 900.0,
               text="Good faith estimate for crowns 900.00")
    docs = taxpack.gather(con, 2025)["documents"]
    picked = taxpack.choose(docs, ["medical"], ["donation"], [tag])
    assert {d["id"] for d in picked} == {med, gift, tagged, est}
    sums = {t["category"]: t for t in taxpack.totals(picked)}
    assert sums["medical"] == {"category": "medical", "documents": 2, "total": 100.65, "counted": 1}
    assert sums["donation"]["total"] == 250.0 and sums["vehicle"]["total"] == 80.0   # "other" goes under its topic
    assert next(d for d in picked if d["id"] == gift)["group"] == "donation"


def test_csv_cells_never_run_as_formulas():
    assert taxpack.cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    assert taxpack.cell("+1") == "'+1" and taxpack.cell("@SUM") == "'@SUM" and taxpack.cell("-2") == "'-2"
    assert taxpack.cell(12.5) == "12.50" and taxpack.cell(None) == "" and taxpack.cell(True) == "yes"
    assert taxpack.write_csv([["a"]], ["h"]).startswith("﻿".encode())


def _client(tmp_path):
    return TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False))


def test_zip_has_files_in_category_folders_and_a_summary(tmp_path):
    with _client(tmp_path) as c:
        sign_in_client(c)
        c2 = db.connect(tmp_path / "paperjay.db")
        from paperjay.library import Library
        from paperjay.store import FileStore
        lib2 = Library(c2)
        store = FileStore(tmp_path / "media")
        staged = store.stage(io.BytesIO(b"%PDF-1.4 clinic"))
        store.commit(staged, ".pdf")
        doc_id = lib2.add(title="=Clinic", checksum=staged.checksum, original_name="c.pdf", original_ext=".pdf",
                          mime_type="application/pdf", size=staged.size, doc_date="2025-05-01", status="ready",
                          content="Clinic receipt 100.65", paper_no=7)
        lib2.update(doc_id, by="system", category="medical")
        with db.transaction(c2):
            analyze.save_card(c2, doc_id, "Clinic receipt 100.65", "test", {
                "kind": "receipt", "category": "medical", "vendor": "", "title": "Clinic", "summary": "",
                "total": 100.65, "tags": [], "dates": [], "ids": [], "dropped": []})
        c2.close()

        r = c.get("/api/taxpack", params={"year": 2025})
        assert r.status_code == 200
        body = r.json()
        assert body["label"] == "2025" and [d["id"] for d in body["documents"]] == [doc_id]
        assert "_file" not in body["documents"][0] and 2025 in body["years"]

        assert c.get("/api/taxpack.zip", params={"year": 2025, "categories": "food"}).status_code == 400
        r = c.get("/api/taxpack.zip", params={"year": 2025, "categories": "medical"})
        assert r.status_code == 200 and "tax%20pack%202025.zip" in r.headers["content-disposition"]
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        assert "PaperJay tax pack 2025/Medical/2025-05-01 =Clinic [%d].pdf" % doc_id in names
        assert z.read(f"PaperJay tax pack 2025/Medical/2025-05-01 =Clinic [{doc_id}].pdf") == b"%PDF-1.4 clinic"
        rows = list(csv.reader(io.StringIO(z.read("PaperJay tax pack 2025/summary.csv").decode("utf-8-sig"))))
        assert rows[0] == taxpack.CSV_COLUMNS
        assert rows[1][1] == "'=Clinic" and rows[1][5] == "100.65" and rows[1][6] == "yes" and rows[1][8] == "7"
        readme = z.read("PaperJay tax pack 2025/README.txt").decode()
        assert "Medical" in readme and "100.65" in readme and "paper original" in readme
        assert not list((tmp_path / "media" / "tmp").glob("taxpack-*"))   # cleaned up after sending


def test_documents_csv_follows_filters_and_selection(tmp_path):
    with _client(tmp_path) as c:
        sign_in_client(c)
        c2 = db.connect(tmp_path / "paperjay.db")
        from paperjay.library import Library
        lib2 = Library(c2)
        ids = [lib2.add(title=t, checksum=t * 8, original_name=f"{t}.pdf", original_ext=".pdf",
                        mime_type="application/pdf", size=1, doc_date=d, status="ready")
               for t, d in (("a", "2025-01-01"), ("b", "2025-02-01"), ("c", "2024-03-01"))]
        c2.close()
        r = c.get("/api/documents.csv", params={"date_from": "2025-01-01"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        assert [row[1] for row in rows[1:]] == ["b", "a"]
        rows = list(csv.reader(io.StringIO(c.get("/api/documents.csv", params={"ids": f"{ids[2]}"}).content.decode("utf-8-sig"))))
        assert [row[1] for row in rows[1:]] == ["c"]


def test_limited_keys_and_readonly(tmp_path):
    from paperjay import auth
    with _client(tmp_path) as c:
        user = sign_in_client(c, role="readonly")
        assert c.get("/api/taxpack").status_code == 200      # looking is reading
        c2 = db.connect(tmp_path / "paperjay.db")
        _, key = auth.create_token(c2, user.id, "assistant", permissions=["read"])
        c2.close()
        c.headers["Authorization"] = f"Token {key}"
        assert c.get("/api/taxpack").status_code == 403       # like the full export: no key may download
        assert c.get("/api/documents.csv").status_code == 403
