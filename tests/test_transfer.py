"""Import from Paperless-ngx (a fake 3.2 server shaped like the real one) and export / re-import."""
import io
import json
import zipfile

import httpx
import pytest

from paperjay import db, export, importer
from paperjay.library import Library
from paperjay.store import FileStore

import docgen

TOKEN = "0123456789abcdef"


def _jpeg(text):
    buf = io.BytesIO()
    docgen.page_image(lines=[text]).save(buf, "JPEG", quality=80)
    return buf.getvalue()


@pytest.fixture
def paperless(tmp_path):
    pdf = docgen.text_pdf(tmp_path / "bill.pdf", lines=["Acme Energy", "Amount due $142.37"]).read_bytes()
    archive = docgen.text_pdf(tmp_path / "bill-archive.pdf", lines=["Acme Energy (archive)"]).read_bytes()
    photo = _jpeg("Receipt photo")
    import hashlib
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    docs = [
        {"id": 45, "correspondent": 1, "document_type": 1, "title": "Acme bill", "content": "Acme Energy Amount due $142.37",
         "tags": [9, 7], "created": "2025-08-14", "created_date": "2025-08-14", "added": "2025-08-15T09:00:00.5-04:00",
         "archive_serial_number": 12, "original_file_name": "bill.pdf", "archived_file_name": "bill.pdf",
         "notes": [{"id": 1, "note": "paid by card", "created": "2025-08-20T10:00:00Z", "user": {"id": 2}}],
         "custom_fields": [{"field": 3, "value": "Kitchen"}], "page_count": 1, "mime_type": "application/pdf",
         "root_document": None, "versions": [{"id": 45, "checksum": sha(pdf), "is_root": True}]},
        {"id": 44, "correspondent": None, "document_type": None, "title": "Photo", "content": "", "tags": [],
         "created": "2025-09-01", "created_date": "2025-09-01", "added": "2025-09-01T12:00:00Z",
         "archive_serial_number": None, "original_file_name": "IMG_1.jpeg", "archived_file_name": None,
         "notes": [], "custom_fields": [], "page_count": None, "mime_type": "image/jpeg", "root_document": None,
         "versions": [{"id": 44, "checksum": sha(photo), "is_root": True}]},
        {"id": 50, "title": "a later version", "root_document": 45, "versions": []},
    ]
    files = {(45, True): pdf, (45, False): archive, (44, True): photo}
    lists = {"/api/tags/": [{"id": 9, "name": "Bills", "color": "#9b59b6", "parent": None},
                            {"id": 7, "name": "Utilities", "color": "#a6cee3", "parent": None}],
             "/api/correspondents/": [{"id": 1, "name": "Acme Energy"}],
             "/api/document_types/": [{"id": 1, "name": "Bill"}],
             "/api/custom_fields/": [{"id": 3, "name": "Room"}]}

    def handler(request: httpx.Request):
        if request.headers.get("authorization") != f"Token {TOKEN}":
            return httpx.Response(401)
        path = request.url.path
        if path == "/api/documents/":
            page = int(request.url.params.get("page", 1))
            rows = docs[:2] if page == 1 else docs[2:]
            return httpx.Response(200, json={"count": len(docs), "results": rows,
                                             "next": "http://pl.test/api/documents/?page=2" if page == 1 else None})
        if path in lists:
            return httpx.Response(200, json={"count": len(lists[path]), "next": None, "results": lists[path]})
        if path.endswith("/download/"):
            doc_id = int(path.split("/")[3])
            body = files.get((doc_id, request.url.params.get("original") == "true"))
            return httpx.Response(200, content=body) if body else httpx.Response(404)
        return httpx.Response(404)

    importer.TRANSPORT = httpx.MockTransport(handler)
    yield
    importer.TRANSPORT = None
    importer.PROGRESS.clear()
    importer.PROGRESS["state"] = "idle"


@pytest.fixture
def setup(tmp_path):
    db.migrate(tmp_path / "p.db", tmp_path / "b")
    return tmp_path / "p.db", FileStore(tmp_path / "media")


def test_check_and_import_from_paperless(paperless, setup):
    path, store = setup
    con = db.connect(path)
    plan = importer.check_paperless(con, "http://pl.test", TOKEN)
    assert plan["documents"] == 2 and plan["already_here"] == 0 and plan["with_searchable_pdf"] == 1
    assert plan["without_text"] == 1 and plan["tags"] == 2

    result = importer.import_paperless(path, store, "http://pl.test", TOKEN)
    assert result["state"] == "done" and result["imported"] == 2 and result["failed"] == 0, result
    lib = Library(con)
    bill = lib.get(45)                                     # id kept
    assert bill["title"] == "Acme bill" and bill["doc_date"] == "2025-08-14" and bill["added_at"] == "2025-08-15T13:00:00Z"
    assert bill["correspondent"] == "Acme Energy" and bill["document_type"] == "Bill" and bill["status"] == "ready"
    assert {t["name"]: t["color"] for t in bill["tags"]} == {"Bills": "#9b59b6", "Utilities": "#a6cee3"}
    assert bill["content"].startswith("Acme Energy") and bill["archive_checksum"]
    assert store.path("archive", bill["checksum"], ".pdf").read_bytes().startswith(b"%PDF")
    assert store.path("thumbs", bill["checksum"], ".webp").exists()
    notes = [n["body"] for n in lib.notes(45)]
    assert notes[0] == "paid by card" and "Room: Kitchen" in notes[1]
    # Phase D: the archive serial number is the paper number now, not a note line
    assert bill["paper_no"] == 12 and "Archive serial number" not in notes[1]
    assert lib.person_fields(45) >= {"title", "doc_date", "correspondent_id", "document_type_id", "tags", "paper_no"}
    photo = lib.get(44)                                    # no text: read with OCR
    assert photo["status"] == "pending" and lib.person_fields(44) == {"doc_date"}  # title "Photo" is a
    # file name (IMG_1.jpeg), so since spec A.3 the importer does not mark it as a person's title
    assert con.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'consume' AND document_id = 44").fetchone()[0] == 1
    assert lib.history(45)[-1]["action"] == "import"

    again = importer.import_paperless(path, store, "http://pl.test", TOKEN)   # safe to run twice
    assert again["imported"] == 0 and again["duplicates"] == 2
    assert importer.check_paperless(con, "http://pl.test", TOKEN)["already_here"] == 2

def test_wrong_token(paperless, setup):
    path, store = setup
    with pytest.raises(PermissionError):
        importer.check_paperless(db.connect(path), "http://pl.test", "wrong")
    assert importer.import_paperless(path, store, "http://pl.test", "wrong")["state"] == "failed"


def test_export_is_incremental_and_reimports(paperless, setup, tmp_path):
    path, store = setup
    importer.import_paperless(path, store, "http://pl.test", TOKEN)
    con = db.connect(path)
    out = tmp_path / "export"
    r = export.export(con, store, out)
    assert r == {**r, "documents": 2, "copied": 3}
    m = json.loads((out / "manifest.json").read_text())
    bill = next(d for d in m["documents"] if d["id"] == 45)
    assert bill["original"] == "documents/2025/2025-08-14 Acme bill [45].pdf"
    assert (out / bill["original"]).exists() and (out / bill["searchable_pdf"]).exists() and (out / "README.txt").exists()
    assert export.export(con, store, out)["copied"] == 0                 # nothing changed
    Library(con).update(45, title="Acme Energy bill: August")
    r = export.export(con, store, out)
    assert r["moved"] == 2 and r["copied"] == 0
    assert (out / "documents/2025/2025-08-14 Acme Energy bill August [45].pdf").exists()
    Library(con).trash(44)
    assert export.export(con, store, out, prune=True)["removed"] == 1

    # restore into an empty PaperJay
    db.migrate(tmp_path / "q.db", tmp_path / "qb")
    res = importer.import_export(tmp_path / "q.db", FileStore(tmp_path / "media2"), out)
    assert res["imported"] == 1 and res["failed"] == 0
    restored = Library(db.connect(tmp_path / "q.db")).get(45)
    assert restored["title"] == "Acme Energy bill: August" and [t["name"] for t in restored["tags"]] == ["Bills", "Utilities"]
    assert restored["archive_checksum"] and restored["content"].startswith("Acme")


def test_export_zip_and_admin_only(tmp_path):
    from fastapi.testclient import TestClient

    from conftest import sign_in_client
    from paperjay.app import create_app
    from paperjay.config import Config
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        c.post("/api/documents", files={"file": ("a.pdf", docgen.text_pdf(tmp_path / "a.pdf").read_bytes())})
        r = c.get("/api/export.zip")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert "manifest.json" in names and any(n.startswith("documents/") for n in names)
        sign_in_client(c, role="member")
        assert c.get("/api/export.zip").status_code == 403
        assert c.post("/api/import/paperless/check", json={"url": "http://x", "token": "t"}).status_code == 403
