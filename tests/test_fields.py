"""The household's own fields (0.7.8): definitions, filling from the fact card, typed values, search,
spreadsheet columns, export and import, permissions."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from paperjay import analyze, auth, db, fields
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.library import Library
from conftest import sign_in_client

CARD = {"kind": "insurance", "category": "insurance", "vendor": "Example Mutual", "title": "Card", "summary": "",
        "total": 412.5, "tags": [], "dropped": [],
        "dates": [{"label": "expires", "date": "2026-12-30"}, {"label": "effective", "date": "2026-07-01"}],
        "ids": [{"label": "Policy number", "value": "EM-4471-2290"}, {"label": "member id", "value": "M-77"}]}


def test_definitions_are_checked(con):
    f = fields.create(con, " Policy  number ", "text", "id: Policy ")
    assert f["name"] == "Policy number" and f["fill"] == "id:policy"
    for bad in (("", "text", ""), ("X", "colour", ""), ("Y", "text", "guess"), ("Z", "text", "id:")):
        with pytest.raises(ValueError):
            fields.create(con, *bad)
    with pytest.raises(ValueError, match="already"):
        fields.create(con, "policy NUMBER")
    assert fields.update(con, f["id"], {"name": "Policy no."})["name"] == "Policy no."


@pytest.mark.parametrize("kind, value, stored", [("number", "12.5", "12.5"), ("money", "$1,099.08", "1099.08"),
                                                 ("date", "2026-01-31", "2026-01-31"), ("text", "  ABC  123 ", "ABC 123")])
def test_values_are_checked(kind, value, stored):
    assert fields.check_value(kind, value) == stored


@pytest.mark.parametrize("kind, value", [("number", "twelve"), ("money", "lots"), ("date", "31/01/2026")])
def test_bad_values(kind, value):
    with pytest.raises(ValueError):
        fields.check_value(kind, value)


def test_filling_typing_and_clearing(con, add_doc, lib):
    doc_id = add_doc(name="card.pdf", title="Insurance card", status="ready", content="card")
    pol = fields.create(con, "Policy number", "text", "id:policy")
    exp = fields.create(con, "Renewal", "date", "date:expires")
    tot = fields.create(con, "Premium", "money", "total")
    plate = fields.create(con, "Licence plate")
    got = {f["name"]: (f["value"], f["source"]) for f in fields.for_document(con, doc_id, CARD)}
    assert got == {"Policy number": ("EM-4471-2290", "filled"), "Renewal": ("2026-12-30", "filled"),
                   "Premium": ("412.50", "filled"), "Licence plate": (None, "none")}
    fields.set_value(con, doc_id, plate["id"], "ABC 123")
    fields.set_value(con, doc_id, pol["id"], "")              # "none", even though one would be filled
    got = {f["name"]: (f["value"], f["source"]) for f in fields.for_document(con, doc_id, CARD)}
    assert got["Licence plate"] == ("ABC 123", "person") and got["Policy number"] == (None, "cleared")
    fields.set_value(con, doc_id, pol["id"], None)            # back to filling itself
    assert fields.for_document(con, doc_id, CARD)[0]["source"] == "filled"
    assert lib.search("ABC")["total"] == 1                    # typed values are searchable
    assert [h["action"] for h in lib.history(doc_id)].count("field") == 3
    fields.delete(con, plate["id"])
    assert lib.search("ABC")["total"] == 0
    assert {f["id"] for f in fields.all_fields(con)} == {pol["id"], exp["id"], tot["id"]}


def test_api_csv_and_permissions(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        admin = sign_in_client(c)
        con = db.connect(tmp_path / "paperjay.db")
        lib = Library(con)
        doc_id = lib.add(title="Car card", checksum="c" * 8, original_name="c.pdf", original_ext=".pdf",
                         mime_type="application/pdf", size=1, status="ready", content="card", doc_date="2026-07-01")
        with db.transaction(con):
            analyze.save_card(con, doc_id, "card", "test", CARD)
        member = auth.create_user(con, "m", "correct horse battery", "member")
        _, member_key = auth.create_token(con, member.id, "k")
        _, read_key = auth.create_token(con, admin.id, "r", permissions=["read"])
        con.close()

        pol = c.post("/api/fields", json={"name": "Policy number", "fill": "id:policy"}).json()
        plate = c.post("/api/fields", json={"name": "Licence plate"}).json()
        assert c.post("/api/fields", json={"name": ""}).status_code == 400
        doc = c.get(f"/api/documents/{doc_id}").json()
        assert [(f["name"], f["value"]) for f in doc["fields"]] == [("Policy number", "EM-4471-2290"), ("Licence plate", None)]
        r = c.put(f"/api/documents/{doc_id}/fields/{plate['id']}", json={"value": "ABC 123"})
        assert r.status_code == 200 and r.json()[1]["value"] == "ABC 123"
        assert c.put(f"/api/documents/{doc_id}/fields/999", json={"value": "x"}).status_code == 404
        assert c.put(f"/api/documents/{doc_id}/fields/{plate['id']}", json={"value": ["x"]}).status_code == 400

        rows = list(csv.reader(io.StringIO(c.get("/api/documents.csv").content.decode("utf-8-sig"))))
        assert rows[0][-2:] == ["Policy number", "Licence plate"] and rows[1][-2:] == ["EM-4471-2290", "ABC 123"]

        c.headers["Authorization"] = f"Token {member_key}"
        assert c.post("/api/fields", json={"name": "Nope"}).status_code == 403          # definitions: admins
        assert c.put(f"/api/documents/{doc_id}/fields/{pol['id']}", json={"value": "X-1"}).status_code == 200
        c.headers["Authorization"] = f"Token {read_key}"
        assert c.get("/api/fields").status_code == 200
        assert c.put(f"/api/documents/{doc_id}/fields/{pol['id']}", json={"value": "X"}).status_code == 403


def test_export_and_import(tmp_path, con, store, add_doc, lib):
    from paperjay import export, importer
    doc_id = add_doc(name="c.pdf", title="Car", status="ready", content="car")
    plate = fields.create(con, "Licence plate")
    fields.create(con, "Policy number", "text", "id:policy")
    fields.set_value(con, doc_id, plate["id"], "ABC 123")
    m = export.manifest(con)
    assert m["custom_fields"][1] == {"name": "Policy number", "kind": "text", "fill": "id:policy"}
    assert m["documents"][0]["fields"] == {"Licence plate": "ABC 123"}
    export.export(con, store, tmp_path / "out")
    db.migrate(tmp_path / "other.db", tmp_path / "b2")
    from paperjay.store import FileStore
    importer.import_export(tmp_path / "other.db", FileStore(tmp_path / "m2"), tmp_path / "out")
    con2 = db.connect(tmp_path / "other.db")
    assert [f["name"] for f in fields.all_fields(con2)] == ["Licence plate", "Policy number"]
    new_id = con2.execute("SELECT id FROM documents").fetchone()[0]
    assert fields.for_document(con2, new_id, None)[0]["value"] == "ABC 123"
