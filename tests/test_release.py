"""Title/date release (spec A.3/A.3b): file-name titles and scan dates that an import marked as
person-set can be given back, audit-logged and reversible."""
from paperjay import importer
from paperjay.library import Library


def test_filename_title_released(lib, add_doc):
    doc_id = add_doc(name="IMG_2041.pdf", title="IMG_2041", given=["title"])
    assert "title" in lib.person_fields(doc_id)
    assert lib.release_import_filing() == 1
    assert "title" not in lib.person_fields(doc_id)
    releases = [h for h in lib.history(doc_id) if h["action"] == "release"]
    assert releases and releases[0]["detail"]["fields"] == ["title"]
    assert lib.release_import_filing() == 0  # idempotent


def test_person_renamed_title_not_released(lib, add_doc):
    doc_id = add_doc(name="IMG_2041.pdf", title="IMG_2041", given=["title"])
    lib.update(doc_id, title="Scan0009")  # a person renamed it, even to another file name
    assert lib.release_import_filing() == 0
    assert "title" in lib.person_fields(doc_id)


def test_real_title_not_released(lib, add_doc):
    doc_id = add_doc(name="scan.pdf", title="Water bill August", given=["title"])
    assert lib.release_import_filing() == 0
    assert "title" in lib.person_fields(doc_id)


def test_released_title_can_be_set_by_the_system_again(lib, add_doc):
    doc_id = add_doc(name="2104W2.pdf", title="2104W2", given=["title"])
    lib.release_import_filing()
    lib.update(doc_id, by="system", title="2023 W-2")
    assert lib.get(doc_id)["title"] == "2023 W-2"
    # and putting the release back (deleting nothing) - person_fields stays without title
    assert "title" not in lib.person_fields(doc_id)


def test_scan_date_released_when_text_has_a_labelled_date(lib, add_doc):
    text = "ACME ENERGY\nStatement date: August 1, 2025\nAmount due: $10.00\n"
    doc_id = add_doc(title="Bill", doc_date="2026-09-26", added_at="2026-09-26T09:00:00Z",
                     content=text, given=["doc_date"])
    assert "doc_date" in lib.person_fields(doc_id)
    assert lib.release_import_filing() >= 1
    assert "doc_date" not in lib.person_fields(doc_id)


def test_scan_date_kept_when_it_is_not_the_added_day(lib, add_doc):
    text = "ACME ENERGY\nStatement date: August 1, 2025\nAmount due: $10.00\n"
    doc_id = add_doc(title="Bill", doc_date="2025-08-01", added_at="2026-09-26T09:00:00Z",
                     content=text, given=["doc_date"])
    lib.release_import_filing()
    assert "doc_date" in lib.person_fields(doc_id)


def test_importer_does_not_given_a_filename_title(con, store, tmp_path):
    src = tmp_path / "Scan0042.pdf"
    src.write_bytes(b"%PDF-1.4 test")
    meta = {"id": 501, "title": "Scan0042", "date": "2025-01-01", "added": "2025-01-02T00:00:00Z",
            "correspondent": None, "document_type": None, "tags": [], "notes": [],
            "content": "some text", "original_name": "Scan0042.pdf", "mime_type": "application/pdf",
            "pages": 1}
    doc_id = importer.import_one(con, store, meta, src, None, None, "test")
    lib = Library(con)
    assert "title" not in lib.person_fields(doc_id)
    meta2 = dict(meta, id=502, title="Water bill August", original_name="Water bill.pdf")
    src2 = tmp_path / "Water bill.pdf"
    src2.write_bytes(b"%PDF-1.4 other")
    doc_id2 = importer.import_one(con, store, meta2, src2, None, None, "test")
    assert "title" in lib.person_fields(doc_id2)
