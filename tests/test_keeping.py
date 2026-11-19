"""Keeping paper and shredding it (0.7.1): keep rules, a person's own choice, the shredding list, shelf
numbers that are freed but never reused, and the setting's validation."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from paperjay import analyze, db, keeping, papers, settings
from paperjay.app import create_app
from paperjay.config import Config
from conftest import sign_in_client

TODAY = date(2026, 11, 19)


def _doc(con, add_doc, lib, title, day, category=None, kind="other", topics=(), expires=None, paper_no=None):
    doc_id = add_doc(name=f"{title}.pdf", title=title, doc_date=day, status="ready", content=title,
                     paper_no=paper_no)
    if category:
        lib.update(doc_id, by="system", category=category)
    dates = [{"label": "expires", "date": expires}] if expires else []
    card = {"kind": kind, "category": category or "other", "vendor": "", "title": title, "summary": "",
            "total": None, "tags": [], "dates": dates, "ids": [], "dropped": []}
    with db.transaction(con):
        analyze.save_card(con, doc_id, title, "test", card)
        for t in topics:
            con.execute("INSERT INTO document_topics(document_id, topic) VALUES (?, ?)", (doc_id, t))
    return doc_id


def test_default_rules(con, add_doc, lib):
    rules = settings.get(con, "keep_rules")
    cases = {
        _doc(con, add_doc, lib, "Electric", "2024-03-07", "utilities"): ("2025-12-31", "Utility"),
        _doc(con, add_doc, lib, "Return", "2024-04-01", "tax", kind="tax"): ("2031-12-31", "Tax"),
        _doc(con, add_doc, lib, "Gift", "2024-06-14", "other", topics=["donation"]): ("2031-12-31", "Tax"),
        _doc(con, add_doc, lib, "Lease", "2024-01-01", "home", kind="contract"): (keeping.FOREVER, "Contracts"),
        _doc(con, add_doc, lib, "Policy", "2024-01-01", "insurance", kind="insurance",
             expires="2025-06-30"): ("2026-06-30", "Insurance"),
        _doc(con, add_doc, lib, "Card statement", "2024-05-01", "finance", topics=["credit card"]): ("2025-12-31", "Bank"),
        _doc(con, add_doc, lib, "Mystery", "2010-01-01"): (keeping.FOREVER, "no keep rule"),
    }
    for doc_id, (until, why) in cases.items():
        k = keeping.for_document(con, doc_id)
        assert k["until"] == until and k["why"].startswith(why), (lib.get(doc_id)["title"], k)


def test_type_name_gives_the_kind(con, add_doc, lib):
    doc_id = _doc(con, add_doc, lib, "Agreement", "2020-01-01", "home")
    lib.update(doc_id, document_type_id=lib.create_label("document_type", "Contract"))
    assert keeping.for_document(con, doc_id)["until"] == keeping.FOREVER


def test_own_choice_wins(con, add_doc, lib):
    doc_id = _doc(con, add_doc, lib, "Electric", "2020-03-07", "utilities", paper_no=3)
    assert [d["id"] for d in keeping.ready(con, TODAY)] == [doc_id]
    lib.update(doc_id, keep_until=keeping.FOREVER)
    k = keeping.for_document(con, doc_id)
    assert k == {**k, "until": keeping.FOREVER, "why": "your choice", "own": True}
    assert keeping.ready(con, TODAY) == []
    with pytest.raises(ValueError):
        lib.update(doc_id, keep_until="soon")


def test_ready_list_shelf_only_and_order(con, add_doc, lib):
    a = _doc(con, add_doc, lib, "Electric A", "2020-03-07", "utilities", paper_no=9)
    b = _doc(con, add_doc, lib, "Electric B", "2021-03-07", "utilities", paper_no=2)
    c = _doc(con, add_doc, lib, "Electric C", "2021-04-07", "utilities")   # no number on the shelf
    _doc(con, add_doc, lib, "Electric new", "2026-03-07", "utilities", paper_no=4)  # kept until 2027
    assert [d["id"] for d in keeping.ready(con, TODAY)] == [b, a]
    assert [d["id"] for d in keeping.ready(con, TODAY, shelf_only=False)] == [b, a, c]
    assert keeping.count_ready(con, TODAY) == 2


def test_rules_are_validated_and_reset(con):
    for bad in ([{"name": ""}], [{"name": "x", "years": -1}], [{"name": "x", "years": True}],
                [{"name": "x", "from": "never"}], [{"name": "x", "topics": "w-2"}], "rules"):
        with pytest.raises(ValueError):
            settings.save(con, {"keep_rules": bad})
    settings.save(con, {"keep_rules": [{"name": " Bills ", "years": 2, "categories": ["Utilities "]}]})
    assert settings.get(con, "keep_rules") == [{"name": "Bills", "years": 2, "from": "year_end",
                                                "categories": ["utilities"], "kinds": [], "topics": []}]
    settings.save(con, {"keep_rules": None})
    assert settings.get(con, "keep_rules") == keeping.DEFAULT_RULES


def test_no_rules_keeps_everything(con, add_doc, lib):
    settings.save(con, {"keep_rules": []})
    _doc(con, add_doc, lib, "Electric", "2001-03-07", "utilities", paper_no=1)
    assert keeping.ready(con, TODAY) == []


def test_shredded_number_is_never_reused(con, add_doc, lib):
    doc_id = _doc(con, add_doc, lib, "Electric", "2020-03-07", "utilities", paper_no=12)
    lib.update(doc_id, shredded_at="2026-11-19T10:00:00Z", shredded_paper_no=12, paper_no=None)
    assert papers.next_free(con) == 13


def _client(tmp_path):
    return TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False))


def test_shred_api_and_undo(tmp_path):
    with _client(tmp_path) as c:
        sign_in_client(c)
        con = db.connect(tmp_path / "paperjay.db")
        from paperjay.library import Library
        lib = Library(con)
        ids = [lib.add(title=t, checksum=t * 8, original_name=f"{t}.pdf", original_ext=".pdf",
                       mime_type="application/pdf", size=1, doc_date="2020-03-01", status="ready",
                       content="electric bill", paper_no=n) for t, n in (("a", 5), ("b", 6))]
        for i in ids:
            lib.update(i, by="system", category="utilities")
        con.close()

        r = c.get("/api/shred").json()
        assert [d["paper_no"] for d in r["documents"]] == [5, 6] and r["count"] == 2
        assert c.get("/api/summary").json()["shred"] == 2

        d = c.post(f"/api/documents/{ids[0]}/shredded").json()
        assert d["paper_no"] is None and d["shredded_paper_no"] == 5 and d["shredded_at"]
        doc = c.get(f"/api/documents/{ids[0]}").json()
        assert doc["keeping"]["shredded_paper_no"] == 5
        assert [x["action"] for x in c.get(f"/api/documents/{ids[0]}/history").json()][-1] == "update"
        assert c.get("/api/shred").json()["count"] == 1

        d = c.post(f"/api/documents/{ids[0]}/unshred").json()
        assert d["paper_no"] == 5 and d["shredded_at"] is None

        assert c.post("/api/shred/bulk", json={"ids": ids}).json() == {"shredded": 2}
        assert c.get("/api/shred").json()["count"] == 0
        assert c.post("/api/shred/bulk", json={"ids": ["x"]}).status_code == 400

        # keep_until through the normal edit
        r = c.patch(f"/api/documents/{ids[1]}", json={"keep_until": keeping.FOREVER})
        assert r.status_code == 200 and r.json()["keep_until"] == keeping.FOREVER


def test_unshred_leaves_a_taken_number_alone(tmp_path):
    with _client(tmp_path) as c:
        sign_in_client(c)
        con = db.connect(tmp_path / "paperjay.db")
        from paperjay.library import Library
        lib = Library(con)
        a = lib.add(title="a", checksum="a" * 8, original_name="a.pdf", original_ext=".pdf",
                    mime_type="application/pdf", size=1, status="ready", paper_no=5)
        b = lib.add(title="b", checksum="b" * 8, original_name="b.pdf", original_ext=".pdf",
                    mime_type="application/pdf", size=1, status="ready")
        con.close()
        c.post(f"/api/documents/{a}/shredded")
        c.patch(f"/api/documents/{b}", json={"paper_no": 5})       # someone typed the old number by hand
        assert c.post(f"/api/documents/{a}/unshred").json()["paper_no"] is None


def test_permissions(tmp_path):
    from paperjay import auth
    with _client(tmp_path) as c:
        user = sign_in_client(c, role="readonly")
        assert c.get("/api/shred").status_code == 200
        assert c.post("/api/documents/1/shredded").status_code == 403
        con = db.connect(tmp_path / "paperjay.db")
        member = auth.create_user(con, "m", "correct horse battery", "member")
        _, read_key = auth.create_token(con, member.id, "k", permissions=["read"])
        _, upd_key = auth.create_token(con, member.id, "k2", permissions=["update"])
        con.close()
        c.headers["Authorization"] = f"Token {read_key}"
        assert c.get("/api/shred").status_code == 200
        assert c.post("/api/shred/bulk", json={"ids": []}).status_code == 403
        c.headers["Authorization"] = f"Token {upd_key}"
        assert c.post("/api/shred/bulk", json={"ids": []}).status_code == 200


def test_export_keeps_keeping(tmp_path, con, store, add_doc, lib):
    from paperjay import export, importer
    doc_id = add_doc(name="old.pdf", title="Old bill", status="ready", content="bill")
    lib.update(doc_id, keep_until="2030-01-01", shredded_at="2026-01-01T00:00:00Z", shredded_paper_no=4)
    m = export.manifest(con)["documents"][0]
    assert (m["keep_until"], m["shredded_paper_no"]) == ("2030-01-01", 4)
    export.export(con, store, tmp_path / "out")
    db.migrate(tmp_path / "other.db", tmp_path / "b2")
    from paperjay.store import FileStore
    importer.import_export(tmp_path / "other.db", FileStore(tmp_path / "media2"), tmp_path / "out")
    con2 = db.connect(tmp_path / "other.db")
    row = con2.execute("SELECT keep_until, shredded_at, shredded_paper_no FROM documents").fetchone()
    assert tuple(row) == ("2030-01-01", "2026-01-01T00:00:00Z", 4)
    assert papers.next_free(con2) == 5
