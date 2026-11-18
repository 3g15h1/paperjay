"""Phase E (0.4.3): filing health — the Tidy up lists and their one-tap fixes.
Made-up names, numbers and text only."""
import pytest
from fastapi.testclient import TestClient

from paperjay import db, duplicates, health, profile, settings
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.library import Library

from conftest import sign_in_client

BILL = ("Acme Energy\nStatement date: March 1, 2026\nAccount number: 1234 5678\n"
        "Electric service for February. Usage: 812 kWh.\nAmount due: $142.37\nDue date: March 21, 2026\n")


def _doc(lib, key, title="Electric bill", content="Some text about a thing.", status="ready",
         original_name="doc.pdf", **kw):
    return lib.add(title=title, checksum=key.encode().hex().ljust(64, "0")[:64],
                   original_name=original_name, original_ext=".pdf", mime_type="application/pdf",
                   size=10, content=content, status=status, **kw)


def _sug(lib, doc_id, **sug):
    lib.update(doc_id, by="system", suggestions={**lib.get(doc_id)["suggestions"], **sug})


def _ids(con, name):
    return [r["id"] for r in health.classify(con)[name]]


# ---- the lists ----

def test_gaps_failed_and_file_name_titles(lib, con):
    corr, _ = lib.get_or_create_label("correspondent", "Acme Energy")
    typ, _ = lib.get_or_create_label("document_type", "Bill")
    full = _doc(lib, "full", doc_date="2026-03-01")
    lib.update(full, correspondent_id=corr, document_type_id=typ)
    bare = _doc(lib, "bare", title="IMG_4021", original_name="IMG_4021.jpg")
    failed = _doc(lib, "failed", status="failed")
    lib.update(failed, by="system", error="the PDF is password protected")
    _doc(lib, "pending", status="pending")             # still being read: not listed
    trashed = _doc(lib, "trashed")
    lib.trash(trashed)
    lists = health.classify(con)
    assert [r["id"] for r in lists["failed"]] == [failed]
    for name in ("no_company", "no_date", "no_type"):
        assert [r["id"] for r in lists[name]] == [bare], name
    assert [r["id"] for r in lists["file_name"]] == [bare]
    assert health.items(con, "failed")[0]["error"] == "the PDF is password protected"
    c = health.counts(con)
    assert c["counts"]["no_company"] == 1 and c["documents"] == 2


def test_copies_disagreements_and_low_evidence(lib, con):
    original = _doc(lib, "orig", doc_date="2026-03-01")
    copy = _doc(lib, "copy", doc_date="2026-03-01")
    _sug(lib, copy, duplicate_of=original)
    gone = _doc(lib, "gone")
    orphan = _doc(lib, "orphan")
    _sug(lib, orphan, duplicate_of=gone)
    lib.trash(gone)                                    # a copy of a trashed document: not listed
    assert _ids(con, "copies") == [copy]
    assert health.items(con, "copies")[0]["copy_of"] == {"id": original, "title": "Electric bill"}

    reason = "vendor: AI says 'Acme Power', the text pattern says 'Acme Energy'"
    argued = _doc(lib, "argued")
    _sug(lib, argued, review=[reason, "no company name found"])
    assert _ids(con, "disagree") == [argued]
    assert health.items(con, "disagree")[0]["reasons"] == [reason]
    _sug(lib, argued, reviewed=[reason])               # looked at: off the list
    assert _ids(con, "disagree") == []

    weak = _doc(lib, "weak", doc_date="2026-03-01")
    _sug(lib, weak, low_evidence=["doc_date", "vendor", "title"])   # no company filed, title isn't listed
    assert _ids(con, "low_evidence") == [weak]
    assert health.items(con, "low_evidence")[0]["fields"] == ["doc_date"]


def test_confirm_imported_filing(lib, con):
    corr, _ = lib.get_or_create_label("correspondent", "Acme Energy")
    tag, _ = lib.get_or_create_label("tag", "AI-filed")
    plain = _doc(lib, "plain")
    tagged = _doc(lib, "tagged")
    unfiled = _doc(lib, "unfiled")                     # nothing filed: nothing to confirm
    for d in (plain, tagged, unfiled):
        lib._audit(None, "import", d, {"source": "paperless"})
    lib.update(plain, by="system", correspondent_id=corr)
    lib.update(tagged, by="system", correspondent_id=corr, tags=[tag])
    assert sorted(_ids(con, "confirm_import")) == sorted([plain, tagged])
    lib.confirm_filing(plain)
    assert _ids(con, "confirm_import") == [tagged]
    settings.save(con, {"trust_imported_filing": True})   # trusted imports, but the AI tag still asks
    assert _ids(con, "confirm_import") == [tagged]


def test_date_suggestions_list_every_labelled_date(lib, con):
    d = _doc(lib, "bill", content=BILL)
    profile.refile(con, d)
    lib.update(d, doc_date=None)                       # a person cleared it: no date, kept that way
    profile.refile(con, d)
    assert lib.get(d)["doc_date"] is None
    item = next(i for i in health.items(con, "no_date") if i["id"] == d)
    dates = {x["date"]: x for x in item["dates"]}
    assert {"2026-03-01", "2026-03-21"} <= set(dates)
    assert [x["date"] for x in item["dates"] if x["picked"]] == [item["suggest"]] == ["2026-03-01"]


# ---- the fixes ----

def test_confirm_fields_makes_them_a_persons_and_clears_low_evidence(lib, con):
    d = _doc(lib, "weak", doc_date="2026-03-01")
    lib.update(d, by="ai", doc_date="2026-03-02")
    _sug(lib, d, low_evidence=["doc_date"])
    from paperjay.health_api import confirm_low_evidence
    confirm_low_evidence(con, d, None, None)
    doc = lib.get(d)
    assert "doc_date" in lib.person_fields(d) and "doc_date" not in doc["ai_fields"]
    assert doc["suggestions"]["low_evidence"] == [] and doc["doc_date"] == "2026-03-02"
    assert _ids(con, "low_evidence") == []
    with pytest.raises(ValueError):
        confirm_low_evidence(con, d, ["vendor"], None)  # no company filed: nothing to confirm


def test_not_a_copy_is_remembered(lib, con):
    settings.save(con, {"duplicate_detection": "review"})
    corr, _ = lib.get_or_create_label("correspondent", "Acme Energy")
    a = _doc(lib, "a", content=BILL, doc_date="2026-03-01")
    b = _doc(lib, "b", content=BILL + " ", doc_date="2026-03-02")
    for d in (a, b):
        lib.update(d, correspondent_id=corr)
        profile.refile(con, d)
    assert lib.get(b)["suggestions"].get("duplicate_of") == a
    doc = lib.not_a_copy(b)
    assert "duplicate_of" not in doc["suggestions"] and doc["suggestions"]["not_copy_of"] == [a]
    assert not any("copy" in r for r in doc["suggestions"]["review"])
    profile.refile(con, b)                             # the next refile doesn't pair them again
    profile.refile(con, a)
    assert lib.get(b)["suggestions"].get("duplicate_of") is None
    assert all(p.get("duplicate_of") is None for p in profile.current_profiles(con).values())
    with pytest.raises(ValueError):
        lib.not_a_copy(b)


def test_find_duplicates_skips_pairs_a_person_separated():
    prof = {"total": 5.0, "dates": []}
    meta = lambda i, apart=(): {"simhash": 0, "length": 10, "oldest_key": ("2026", i),  # noqa: E731
                                "series": "acct:1", "vendor": "x", "not_copy_of": set(apart)}
    profiles = {1: prof, 2: prof, 3: prof}
    assert duplicates.find_duplicates(profiles, {1: meta(1), 2: meta(2), 3: meta(3)}) == {2: 1, 3: 1}
    found = duplicates.find_duplicates(profiles, {1: meta(1), 2: meta(2, [1]), 3: meta(3)})
    assert found == {3: 1}                             # 2 stands on its own now


# ---- the API ----

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        c.user = sign_in_client(c)
        yield c


def _con(client):
    return db.connect(client.app.state.config.db_path)


def test_health_api_lists_and_one_tap_accepts(client):
    con = _con(client)
    lib = Library(con)
    d = _doc(lib, "x", title="IMG_4021", original_name="IMG_4021.jpg")
    _sug(lib, d, correspondent="Acme Energy", document_type="Bill")
    r = client.get("/api/health/filing").json()
    assert r["counts"]["no_company"] == 1 and "items" not in r
    r = client.get("/api/health/filing", params={"list": "no_company"}).json()
    assert r["items"][0]["suggest"] == "Acme Energy"
    assert client.get("/api/health/filing", params={"list": "nope"}).status_code == 400
    for body in ({"correspondent": "Acme Energy"}, {"document_type": "Bill"},
                 {"doc_date": "2026-03-01"}, {"title": "Electric bill March"}):
        assert client.post(f"/api/documents/{d}/suggestions/accept", json=body).status_code == 200
    assert client.post(f"/api/documents/{d}/suggestions/accept", json={"doc_date": "soon"}).status_code == 400
    doc = lib.get(d)
    assert doc["correspondent"] == "Acme Energy" and doc["document_type"] == "Bill"
    assert {"correspondent_id", "document_type_id", "doc_date", "title"} <= lib.person_fields(d)
    assert "correspondent" not in doc["suggestions"] and "document_type" not in doc["suggestions"]
    assert client.get("/api/health/filing").json()["documents"] == 0
    con.close()


def test_bulk_confirm_and_bulk_reviewed_remembers(client):
    con = _con(client)
    lib = Library(con)
    corr, _ = lib.get_or_create_label("correspondent", "Acme Energy")
    a, b = _doc(lib, "a"), _doc(lib, "b", doc_date="2026-03-01")
    lib._audit(None, "import", a, {"source": "paperless"})
    lib.update(a, by="system", correspondent_id=corr)
    _sug(lib, b, low_evidence=["doc_date"], review=["doc_date: only a low-confidence rules value"])
    lib.update(b, by="system", needs_review=True)
    assert client.post("/api/documents/bulk", json={"ids": [a], "action": "confirm_filing"}).json()["done"] == 1
    assert _ids(con, "confirm_import") == []
    assert client.post("/api/documents/bulk", json={"ids": [a, b], "action": "confirm_fields"}).json()["done"] == 1
    assert "doc_date" in lib.person_fields(b)
    client.post("/api/documents/bulk", json={"ids": [b], "action": "mark_reviewed"})
    sug = lib.get(b)["suggestions"]
    assert sug["review"] == [] and sug["reviewed"] == ["doc_date: only a low-confidence rules value"]
    con.close()


def test_refile_everything_is_admin_and_estimates_first(client):
    con = _con(client)
    lib = Library(con)
    for k in ("a", "b", "c"):
        _doc(lib, k)
    r = client.post("/api/refile", json={}).json()
    assert r == {"documents": 3, "ai_on": False, "ai_estimate_seconds": 0, "ai_estimate": None,
                 "queued": 0, "queued_ai": 0}
    assert not con.execute("SELECT 1 FROM jobs WHERE kind = 'profile'").fetchone()  # nothing queued yet
    r = client.post("/api/refile", json={"confirm": True, "ai": True}).json()
    assert r["queued"] == 3 and r["queued_ai"] == 0     # the AI is off: rules + library only
    assert con.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'profile' AND status = 'queued'"
                       ).fetchone()[0] == 3
    d = lib.get(1)["id"]
    assert client.post(f"/api/documents/{d}/refile").status_code == 200
    con.close()


def test_roles(client, tmp_path):
    con = _con(client)
    lib = Library(con)
    d = _doc(lib, "x")
    con.close()
    with TestClient(client.app) as member:
        sign_in_client(member, role="member")
        assert member.post("/api/refile", json={"confirm": True}).status_code == 403
        assert member.post(f"/api/documents/{d}/confirm-filing").status_code == 200
    with TestClient(client.app) as reader:
        sign_in_client(reader, role="readonly")
        assert reader.get("/api/health/filing").status_code == 200
        assert reader.post(f"/api/documents/{d}/confirm-filing").status_code == 403
        assert reader.post(f"/api/documents/{d}/not-a-copy").status_code == 403


def test_file_name_titles_get_grounded_options(lib, con):
    corr, _ = lib.get_or_create_label("correspondent", "Acme Energy")
    typ, _ = lib.get_or_create_label("document_type", "Bill")
    ocr_junk = _doc(lib, "junk", title="IMG_4021", original_name="Reward certificate.pdf")
    camera = _doc(lib, "cam", title="IMG_4022", original_name="IMG_4022.jpg", doc_date="2026-03-01")
    lib.update(camera, by="system", correspondent_id=corr, document_type_id=typ)
    opts = {i["id"]: i["options"] for i in health.items(con, "file_name")}
    assert opts[ocr_junk] == ["Reward certificate"]
    assert opts[camera] == ["Acme Energy bill, Mar 2026"]           # never the file name itself
