"""Browse (spec C.1): the filing cabinet — group the library by category, company,
year, type, tag, topic or month, with counts at every level and an Unfiled bucket."""
import pytest
from fastapi.testclient import TestClient

from paperjay import browse, db
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.library import Library

from conftest import sign_in_client


@pytest.fixture
def docs(con, lib, add_doc):
    acme = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    water = lib.get_or_create_label("correspondent", "City Water")[0]
    bill = lib.get_or_create_label("document_type", "Bill")[0]
    ids = {}
    ids["d1"] = add_doc(data=b"one", title="Acme March", doc_date="2025-03-01", status="ready")
    ids["d2"] = add_doc(data=b"two", title="Acme January", doc_date="2026-01-15", status="ready")
    ids["d3"] = add_doc(data=b"three", title="Water June", doc_date="2025-06-01", status="ready")
    ids["d4"] = add_doc(data=b"four", title="Dental February", doc_date="2026-02-01", status="ready")
    ids["d5"] = add_doc(data=b"five", title="Mystery page", status="ready")
    lib.update(ids["d1"], correspondent_id=acme, document_type_id=bill, category="utilities")
    lib.update(ids["d2"], correspondent_id=acme, document_type_id=bill, category="utilities")
    lib.update(ids["d3"], correspondent_id=water, category="utilities")
    lib.update(ids["d4"], category="medical")
    con.execute("INSERT INTO document_topics(document_id, topic) VALUES (?, 'dental')", (ids["d4"],))
    tag = lib.get_or_create_label("tag", "household")[0]
    lib.update(ids["d1"], tags=[tag])
    lib.update(ids["d3"], tags=[tag])
    gone = add_doc(data=b"six", title="Trashed Acme", doc_date="2025-05-01", status="ready")
    lib.update(gone, correspondent_id=acme, category="utilities")
    lib.trash(gone)
    ids["acme"], ids["water"], ids["bill"], ids["tag"] = acme, water, bill, tag
    return ids


def by_key(items):
    return {i["key"]: i for i in items}


def test_default_path_groups_by_category(docs, con):
    r = browse.browse(con, ["category", "correspondent", "year"], {})
    assert r["level"] == "category"
    items = by_key(r["items"])
    assert items["utilities"]["count"] == 3          # the trashed one is not counted
    assert items["medical"]["count"] == 1
    assert items["__unfiled__"]["count"] == 1
    assert items["__unfiled__"]["name"] == "Unfiled"
    assert r["items"][-1]["key"] == "__unfiled__"     # Unfiled sorts last
    assert r["total"] == 5 and r["documents"] == []


def test_browse_counts_ready_documents_only(docs, con, lib, add_doc):
    # Browse counts agree with the library and suggest: only ready documents are
    # counted. One still processing and one that failed are not filed yet, so
    # they show up nowhere here (review 0.4.0 #4).
    busy = add_doc(data=b"busy", title="Busy Acme", doc_date="2025-07-01", status="processing")
    lib.update(busy, correspondent_id=docs["acme"], category="utilities")
    bad = add_doc(data=b"bad", title="Failed Acme", doc_date="2025-08-01", status="failed")
    lib.update(bad, correspondent_id=docs["acme"], category="utilities")
    r = browse.browse(con, ["category", "correspondent", "year"], {})
    items = by_key(r["items"])
    assert items["utilities"]["count"] == 3 and r["total"] == 5


def test_drill_down_to_documents(docs, con):
    path = ["category", "correspondent", "year"]
    r = browse.browse(con, path, {"category": "utilities"})
    assert r["level"] == "correspondent"
    items = by_key(r["items"])
    assert items[str(docs["acme"])]["count"] == 2 and items[str(docs["acme"])]["name"] == "Acme Energy"
    r = browse.browse(con, path, {"category": "utilities", "correspondent_id": docs["acme"]})
    assert r["level"] == "year"
    assert [(i["key"], i["count"]) for i in r["items"]] == [("2026", 1), ("2025", 1)]  # newest first
    r = browse.browse(con, path, {"category": "utilities", "correspondent_id": docs["acme"], "year": "2025"})
    assert r["level"] == "documents" and r["total"] == 1
    assert [d["id"] for d in r["documents"]] == [docs["d1"]]


def test_unfiled_drill_down(docs, con):
    r = browse.browse(con, ["category", "correspondent", "year"], {"category": "__unfiled__"})
    assert r["level"] == "correspondent"
    assert by_key(r["items"])["__unfiled__"]["count"] == 1
    r = browse.browse(con, ["category", "correspondent", "year"],
                      {"category": "__unfiled__", "correspondent_id": "__unfiled__"})
    assert r["level"] == "year" and by_key(r["items"])["__unfiled__"]["count"] == 1


def test_year_month_path(docs, con):
    r = browse.browse(con, ["year", "month"], {})
    assert r["level"] == "year"
    items = by_key(r["items"])
    assert items["2025"]["count"] == 2 and items["2026"]["count"] == 2
    assert items["__unfiled__"]["count"] == 1
    r = browse.browse(con, ["year", "month"], {"year": "2025"})
    assert r["level"] == "month"
    assert [(i["key"], i["name"], i["count"]) for i in r["items"]] == [("03", "March", 1), ("06", "June", 1)]


def test_type_tag_topic_paths(docs, con):
    r = browse.browse(con, ["type", "year"], {})
    items = by_key(r["items"])
    assert items[str(docs["bill"])]["name"] == "Bill" and items[str(docs["bill"])]["count"] == 2
    assert items["__unfiled__"]["count"] == 3
    r = browse.browse(con, ["tag", "year"], {})
    items = by_key(r["items"])
    assert items[str(docs["tag"])]["count"] == 2 and items["__unfiled__"]["count"] == 3
    r = browse.browse(con, ["topic", "year"], {})
    items = by_key(r["items"])
    assert items["dental"]["count"] == 1 and items["__unfiled__"]["count"] == 4
    r = browse.browse(con, ["topic", "year"], {"topic": "dental", "year": "2026"})
    assert [d["id"] for d in r["documents"]] == [docs["d4"]]


def test_bad_dim_and_value(con):
    with pytest.raises(ValueError):
        browse.browse(con, ["nonsense"], {})
    with pytest.raises(ValueError):
        browse.browse(con, ["category"], {"correspondent_id": "abc"})


@pytest.fixture
def client(tmp_path):
    db.migrate(tmp_path / "paperjay.db", tmp_path / "backups")
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)), follow_redirects=False) as c:
        sign_in_client(c)
        yield c


def test_browse_api_shape(client, tmp_path):
    con = db.connect(tmp_path / "paperjay.db")
    try:
        lib = Library(con)
        doc_id = lib.add(title="Water bill", checksum="b" * 64, original_name="bill.pdf",
                         original_ext=".pdf", mime_type="application/pdf", size=3,
                         content="City Water", status="ready", doc_date="2025-04-01")
        lib.update(doc_id, category="utilities")
    finally:
        con.close()
    r = client.get("/api/browse").json()
    assert r["level"] == "category" and r["path"] == ["category", "correspondent", "year"]
    assert r["items"][0]["name"] == "utilities"
    r = client.get("/api/browse", params={"by": "year,month"}).json()
    assert r["level"] == "year" and r["items"][0]["key"] == "2025"
    assert client.get("/api/browse", params={"by": "nope"}).status_code == 400
