"""End to end: upload through the API, real OCR in the worker, then check the stored document."""
import io
import time

import pikepdf
import pytest
from fastapi.testclient import TestClient

from paperjay import db, intake, ocr
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.consume import ConsumeFolder
from paperjay.store import FileStore

import docgen
from conftest import sign_in_client

needs_ocr = pytest.mark.skipif(bool(ocr.missing_tools()), reason=f"OCR tools missing: {ocr.missing_tools()}")


def jpeg(**kw) -> bytes:
    buf = io.BytesIO()
    docgen.page_image(**kw).save(buf, "JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("pj")
    app = create_app(Config(data_dir=d, consume_dir=None, max_upload_mb=5))
    with TestClient(app) as c:
        sign_in_client(c)
        yield c


def wait(client, doc_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = client.get(f"/api/documents/{doc_id}").json()
        if doc["status"] in ("ready", "failed") and (doc["processing"] or {}).get("status") in ("done", "failed"):
            return doc
        time.sleep(0.3)
    raise AssertionError(f"document {doc_id} still {doc['status']} after {timeout}s")


def upload(client, name, data, **form):
    return client.post("/api/documents", files={"file": (name, data)}, data=form)


@needs_ocr
def test_phone_photo_end_to_end(client):
    r = upload(client, "IMG_0001.jpg", jpeg(rotate=90))
    assert r.status_code == 201, r.text
    doc = wait(client, r.json()["uploads"][0]["document_id"])
    assert doc["status"] == "ready" and doc["error"] is None
    assert "Account Number: 4471-2290-18" in doc["content"] and "$142.37" in doc["content"]
    assert doc["title"] == "ACME POWER COMPANY"      # generic file name replaced from the text
    assert doc["doc_date"] == "2025-08-14"
    assert doc["page_count"] == 1 and doc["mime_type"] == "image/jpeg"
    pdf = client.get(f"/api/documents/{doc['id']}/archive")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-security-policy"] == "sandbox"
    thumb = client.get(f"/api/documents/{doc['id']}/thumb")
    assert thumb.status_code == 200 and thumb.content[8:12] == b"WEBP"
    orig = client.get(f"/api/documents/{doc['id']}/original")
    assert orig.content[:3] == b"\xff\xd8\xff"

    dup = upload(client, "copy.jpg", orig.content)
    assert dup.status_code == 409 and dup.json()["uploads"][0]["duplicate_of"] == doc["id"]


@needs_ocr
def test_given_title_and_labels_are_kept(client):
    con = db.connect(client.app.state.config.db_path)
    from paperjay.library import Library
    bills = Library(con).create_label("tag", "Bills")
    r = upload(client, "IMG_0002.png", _png(), title="My water bill", doc_date="2024-01-02", tags=str(bills))
    doc = wait(client, r.json()["uploads"][0]["document_id"])
    assert doc["title"] == "My water bill" and doc["doc_date"] == "2024-01-02"
    assert [t["name"] for t in doc["tags"]] == ["Bills"]


def _png() -> bytes:
    buf = io.BytesIO()
    docgen.page_image(lines=["Water utility", "Total 55.10"]).convert("RGBA").save(buf, "PNG")
    return buf.getvalue()


@needs_ocr
def test_born_digital_pdf_and_multipage_tiff(client, tmp_path):
    pdf = docgen.text_pdf(tmp_path / "statement.pdf").read_bytes()
    a = upload(client, "Bank statement.pdf", pdf).json()["uploads"][0]["document_id"]
    frames = [docgen.page_image(lines=["Page one heading", "alpha"]), docgen.page_image(lines=["Page two", "omega"])]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:], dpi=(300, 300), compression="tiff_lzw")
    b = upload(client, "scan.tif", buf.getvalue()).json()["uploads"][0]["document_id"]
    da, db_ = wait(client, a), wait(client, b)
    assert da["status"] == "ready" and "Account Number" in da["content"] and da["title"] == "Bank statement"
    assert db_["page_count"] == 2 and "omega" in db_["content"] and "\f" in db_["content"]


@needs_ocr
def test_unreadable_pdfs_fail_clearly_and_keep_the_original(client, tmp_path):
    src = docgen.text_pdf(tmp_path / "x.pdf")
    with pikepdf.open(src) as p:
        p.save(tmp_path / "locked.pdf", encryption=pikepdf.Encryption(owner="o", user="secret"))
    r = upload(client, "locked.pdf", (tmp_path / "locked.pdf").read_bytes())
    doc = wait(client, r.json()["uploads"][0]["document_id"])
    assert doc["status"] == "failed" and "password" in doc["error"]
    assert doc["processing"]["status"] == "failed" and doc["processing"]["attempts"] == 1  # not retried
    assert client.get(f"/api/documents/{doc['id']}/original").status_code == 200

    r = upload(client, "broken.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 200)
    doc = wait(client, r.json()["uploads"][0]["document_id"])
    assert doc["status"] == "failed" and doc["error"]


def test_rejects_non_documents_and_oversized(client):
    r = upload(client, "invoice.pdf", b"PK\x03\x04 a zip pretending")
    assert r.status_code == 415 and "PDF, JPEG" in r.json()["uploads"][0]["message"]
    assert upload(client, "empty.pdf", b"").status_code == 415
    r = upload(client, "huge.pdf", b"%PDF-1.4\n" + b"0" * (7 * 1024 * 1024))
    assert r.status_code == 413
    assert upload(client, "x.pdf", b"%PDF-1.4", doc_date="14/08/2025").status_code == 400
    recent = client.get("/api/uploads").json()
    assert {"rejected"} <= {u["outcome"] for u in recent}


@needs_ocr
def test_paperless_compatible_upload_and_task(client):
    data = jpeg(lines=["Paperless compatible", "Shortcut upload 2025-02-03"])
    r = client.post("/api/documents/post_document/", files={"document": ("shortcut.jpg", data)},
                    data={"title": "From my phone"})
    assert r.status_code == 200 and isinstance(r.json(), str)
    task_id = r.json()
    deadline = time.time() + 180
    while time.time() < deadline:
        task = client.get("/api/tasks/", params={"task_id": task_id}).json()[0]
        if task["status"] in ("SUCCESS", "FAILURE"):
            break
        time.sleep(0.3)
    assert task["status"] == "SUCCESS" and task["related_document"]
    assert client.get(f"/api/documents/{task['related_document']}").json()["title"] == "From my phone"

    again = client.post("/api/documents/post_document/", files={"document": ("again.jpg", data)}).json()
    task = client.get("/api/tasks/", params={"task_id": again}).json()[0]
    assert task["status"] == "FAILURE" and "already stored" in task["result"]
    assert client.get("/api/tasks/", params={"task_id": "nope"}).json() == []
    assert client.post("/api/documents/post_document/",
                       files={"document": ("a.exe", b"MZ\x90\x00")}).status_code == 400


def test_consume_folder(tmp_path):
    path = tmp_path / "p.db"
    db.migrate(path, tmp_path / "b")
    store = FileStore(tmp_path / "media")
    folder = tmp_path / "consume"
    folder.mkdir()
    cf = ConsumeFolder(folder, path, store, max_bytes=10_000_000, settle=0)
    (folder / "bill.pdf").write_bytes(b"%PDF-1.4 bill")
    (folder / "notes.txt").write_bytes(b"just text")
    (folder / ".hidden.pdf").write_bytes(b"%PDF-1.4 hidden")
    (folder / "big.pdf.part").write_bytes(b"%PDF-1.4 copying")
    assert cf.scan() == []                       # first sight: wait until the file stops changing
    results = {u.filename: u.outcome for u in cf.scan()}
    assert results == {"bill.pdf": "accepted", "notes.txt": "rejected"}
    assert not (folder / "bill.pdf").exists() and (folder / "rejected" / "notes.txt").exists()
    assert (folder / ".hidden.pdf").exists() and (folder / "big.pdf.part").exists()
    (folder / "bill-copy.pdf").write_bytes(b"%PDF-1.4 bill")
    cf.scan()
    assert [u.outcome for u in cf.scan()] == ["duplicate"] and not (folder / "bill-copy.pdf").exists()
    con = db.connect(path)
    assert con.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'consume'").fetchone()[0] == 1


def test_requeue_unfinished(tmp_path):
    path = tmp_path / "p.db"
    db.migrate(path, tmp_path / "b")
    con = db.connect(path)
    store = FileStore(tmp_path / "media")
    up = intake.ingest(con, store, io.BytesIO(b"%PDF-1.4 x"), "x.pdf", source="api", max_bytes=10**6)
    con.execute("DELETE FROM jobs")                # as if we crashed before queueing
    assert intake.requeue_unfinished(con) == 1
    assert intake.requeue_unfinished(con) == 0
    assert con.execute("SELECT document_id FROM jobs").fetchone()[0] == up.document_id


@needs_ocr
def test_pages_render_for_photos_and_pdfs(client, tmp_path):
    photo = upload(client, "IMG_0100.jpg", jpeg(lines=["Page image test", "Total 9.99"])).json()["uploads"][0]["document_id"]
    pdf = upload(client, "Statement 2.pdf", docgen.text_pdf(tmp_path / "s.pdf", lines=["Rendered statement"]).read_bytes()
                 ).json()["uploads"][0]["document_id"]
    for doc_id in (photo, pdf):
        wait(client, doc_id)
        r = client.get(f"/api/documents/{doc_id}/pages/1", params={"w": 700})
        assert r.status_code == 200 and r.content[8:12] == b"WEBP", r.text[:200]
        assert client.get(f"/api/documents/{doc_id}/pages/2").status_code == 404


@needs_ocr
def test_spot_colour_pdf_still_gets_an_archive(client, tmp_path):
    """Print-shop PDFs with Separation colours can't become PDF/A as they are (2 of 33 real documents)."""
    import subprocess
    ps = tmp_path / "spot.ps"
    ps.write_text("%!PS\n[/Separation (PANTONE 300 C) /DeviceCMYK {dup 0.99 mul exch dup 0.43 mul exch pop 0 0}] "
                  "setcolorspace 1 setcolor newpath 60 600 moveto 540 600 lineto 540 760 lineto 60 760 lineto closepath "
                  "fill 0 setgray /Helvetica findfont 18 scalefont setfont 72 560 moveto (Northwind Bank statement) show "
                  "72 530 moveto (Balance due 412.08) show showpage\n")
    pdf = tmp_path / "spot.pdf"
    subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite", "-sColorConversionStrategy=LeaveColorUnchanged",
                    f"-sOutputFile={pdf}", str(ps)], check=True)
    doc = wait(client, upload(client, "Statement.pdf", pdf.read_bytes()).json()["uploads"][0]["document_id"])
    assert doc["status"] == "ready" and doc["archive_checksum"], doc["error"]
    assert "Northwind Bank statement" in doc["content"] and "RGB" in (doc["error"] or "")
