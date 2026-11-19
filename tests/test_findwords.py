"""Search words marked on the pages (0.7.6): word boxes from the searchable PDF, matching, turned pages."""
import io

import pikepdf
import pytest
from fastapi.testclient import TestClient

from paperjay import db, findwords
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.library import Library
from paperjay.store import FileStore
from conftest import sign_in_client


def text_pdf(lines_per_page, rotate=()) -> bytes:
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    for i, lines in enumerate(lines_per_page):
        ops = "BT /F1 12 Tf " + " ".join(f"72 {700 - 20 * k} Td ({t}) Tj 0 0 Td" if k == 0 else f"0 -20 Td ({t}) Tj"
                                         for k, t in enumerate(lines)) + " ET"
        page = pikepdf.Page(pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792],
                                               Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                                               Contents=pdf.make_stream(ops.encode())))
        pdf.pages.append(page)
        if i in rotate:
            pdf.pages[-1].rotate(90, relative=False)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_terms():
    assert findwords.terms_of("When does my car insurance expire?") == ["car", "insurance", "expire"]
    assert findwords.terms_of("EM-4471-2290, café") == ["em44712290", "cafe"]
    assert findwords.terms_of("of an") == []


def test_find_boxes(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(text_pdf([["Example Mutual Insurance", "Policy EM-4471-2290"], ["Nothing here"]]))
    r = findwords.find(p, findwords.terms_of("insur EM-4471-2290 mutual"))
    assert r["count"] == 3 and [pg["n"] for pg in r["pages"]] == [1]
    for x0, y0, x1, y1 in r["pages"][0]["boxes"]:
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert findwords.find(p, ["zebra"])["count"] == 0
    assert findwords.find(tmp_path / "missing.pdf", ["x"])["count"] == 0


def test_turned_pages_stay_on_the_page(tmp_path):
    p = tmp_path / "t.pdf"
    p.write_bytes(text_pdf([["Closing balance"], ["Closing balance"]], rotate={1}))
    r = findwords.find(p, ["closing"])
    assert [pg["n"] for pg in r["pages"]] == [1, 2]
    for pg in r["pages"]:
        for box in pg["boxes"]:
            assert all(0 <= v <= 1 for v in box), (pg["n"], box)


def test_api(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        con = db.connect(tmp_path / "paperjay.db")
        store = FileStore(tmp_path / "media")
        staged = store.stage(io.BytesIO(text_pdf([["Water bill", "Amount due 61.20"]])))
        store.commit(staged, ".pdf")
        doc_id = Library(con).add(title="w", checksum=staged.checksum, original_name="w.pdf", original_ext=".pdf",
                                  mime_type="application/pdf", size=staged.size, status="ready")
        con.close()
        r = c.get(f"/api/documents/{doc_id}/find", params={"q": "water amount"}).json()
        assert r["count"] == 2 and r["terms"] == ["water", "amount"]
        assert c.get(f"/api/documents/{doc_id}/find").json()["count"] == 0
        assert c.get("/api/documents/999/find", params={"q": "x"}).status_code == 404
