"""The A–Z index (spec C.2): index_terms built from merged profiles at refile, company
terms kept in step with label edits, numbers masked with an audit-logged reveal."""
import json

import pytest
from fastapi.testclient import TestClient

from paperjay import db, indexing, profile
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.library import Library

from conftest import sign_in_client

BILL = """Acme Energy
Account No: 1234 5678 9012
Statement date: March 1, 2025
Amount due $54.00
Due date: March 21, 2025
Account holder: Jo Miller
Your electricity usage this month was typical for the season."""


def _doc(lib, text, title="A bill", name="scan1.pdf"):
    return lib.add(title=title, checksum=f"{abs(hash(text)) % 10**16:064x}", original_name=name,
                   original_ext=".pdf", mime_type="application/pdf", size=10,
                   content=text, status="ready", doc_date="2025-03-01")


def _terms(con, doc_id):
    return {(r["kind"], r["norm"]): (r["term"], r["label"]) for r in con.execute(
        "SELECT kind, norm, term, label FROM index_terms WHERE document_id = ?", (doc_id,))}


def test_refile_writes_terms(con, lib):
    doc_id = _doc(lib, BILL)
    acme = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    lib.update(doc_id, correspondent_id=acme)
    profile.refile(con, doc_id)
    terms = _terms(con, doc_id)
    assert terms[("company", "acmeenergy")] == ("Acme Energy", "")
    assert terms[("number", "123456789012")] == ("1234 5678 9012", "account number")
    assert terms[("person", "jomiller")] == ("Jo Miller", "account holder")


def test_company_term_follows_label_edits(con, lib):
    doc_id = _doc(lib, BILL)
    acme = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    water = lib.get_or_create_label("correspondent", "City Water")[0]
    lib.update(doc_id, correspondent_id=acme)
    profile.refile(con, doc_id)
    lib.update(doc_id, correspondent_id=water)     # a person corrects the company
    terms = _terms(con, doc_id)
    assert ("company", "citywater") in terms and ("company", "acmeenergy") not in terms
    lib.rename_label("correspondent", water, "City Water Co")
    terms = _terms(con, doc_id)
    assert ("company", "citywaterco") in terms
    lib.delete_label("correspondent", water)
    assert not any(k == "company" for k in _terms(con, doc_id))


def test_backfill_restores_terms(con, lib):
    doc_id = _doc(lib, BILL)
    acme = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    lib.update(doc_id, correspondent_id=acme)
    profile.refile(con, doc_id)
    con.execute("DELETE FROM index_terms")
    assert indexing.backfill(con) == 1
    assert ("number", "123456789012") in _terms(con, doc_id)


def test_trashed_documents_leave_the_index(con, lib):
    doc_id = _doc(lib, BILL)
    acme = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    lib.update(doc_id, correspondent_id=acme)
    profile.refile(con, doc_id)
    lib.trash(doc_id)
    assert indexing.az_entries(con, "company")["entries"] == []
    assert indexing.documents_for_term(con, "company",
                                       indexing.term_key(con, "company", "acmeenergy")) == []
    lib.restore(doc_id)
    assert indexing.documents_for_term(con, "company",
                                       indexing.term_key(con, "company", "acmeenergy")) == [doc_id]


def test_term_key_is_keyed_by_a_per_install_secret(con, tmp_path):
    # Review 0.4.0 #3: keys were an unsalted sha1 of the value itself, brute-forceable
    # offline in seconds (account numbers are short and the last 4 are shown). They
    # are now an HMAC keyed by a secret generated once per install and kept in the
    # settings table, masked in every settings response.
    import hashlib

    from paperjay import settings
    key = indexing.term_key(con, "number", "123456789012")
    assert key != hashlib.sha1(b"number:123456789012").hexdigest()[:16]
    assert key == indexing.term_key(con, "number", "123456789012")   # stable here
    assert settings.get(con, "index_secret")                          # generated + stored
    assert settings.all_settings(con)["index_secret"] == "••••••"     # never handed out
    other_path = tmp_path / "other.db"
    db.migrate(other_path, tmp_path / "backups2")
    other = db.connect(other_path)
    settings.save(other, {"index_secret": "a-different-installs-secret"})
    try:
        assert indexing.term_key(other, "number", "123456789012") != key
    finally:
        other.close()


def test_startup_rebuilds_missing_terms_without_a_refile(tmp_path):
    # Review 0.4.0 #4: startup queued a full profile (refile) job for every ready
    # document with no index terms, on every restart — a document with nothing to
    # index (no company, number, person or topic) qualified forever. Startup now
    # rebuilds the terms directly and queues nothing.
    path = tmp_path / "paperjay.db"
    db.migrate(path, tmp_path / "backups")
    con = db.connect(path)
    lib = Library(con)
    doc_id = lib.add(title="Plain letter", checksum="c" * 64, original_name="l.pdf",
                     original_ext=".pdf", mime_type="application/pdf", size=10,
                     content="Dear all, a short note about nothing in particular.",
                     status="ready", doc_date="2025-01-01")
    profile.refile(con, doc_id)   # current cards + simhash: only the terms are absent
    assert con.execute("SELECT COUNT(*) FROM index_terms WHERE document_id = ?",
                       (doc_id,)).fetchone()[0] == 0
    con.close()
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)),
                    follow_redirects=False):
        pass
    con = db.connect(path)
    try:
        assert con.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'profile' "
                           "AND document_id = ?", (doc_id,)).fetchone()[0] == 0
    finally:
        con.close()


# ---- the API ----

POLICY = """FastNet Insurance
Policy number: PL-8842-1199
Insured: Sam Ortiz
Your policy renews on June 1, 2026."""


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "paperjay.db"
    db.migrate(path, tmp_path / "backups")
    con = db.connect(path)
    lib = Library(con)
    doc_id = _doc(lib, POLICY, title="Insurance policy", name="policy.pdf")
    fastnet = lib.get_or_create_label("correspondent", "FastNet")[0]
    lib.update(doc_id, correspondent_id=fastnet)
    lib.add_alias(fastnet, "Fast Net Co")
    profile.refile(con, doc_id)
    con.close()
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)), follow_redirects=False) as c:
        sign_in_client(c)
        c.doc_id = doc_id
        yield c


def test_company_section_includes_aliases(client):
    entries = client.get("/api/index", params={"kind": "company"}).json()["entries"]
    by_term = {e["term"]: e for e in entries}
    assert by_term["FastNet"]["count"] == 1 and by_term["FastNet"]["see"] is None
    assert by_term["Fast Net Co"]["see"] == "FastNet"
    assert by_term["Fast Net Co"]["correspondent_id"] == by_term["FastNet"]["correspondent_id"]
    r = client.get("/api/index", params={"kind": "company", "letter": "f"}).json()
    assert len(r["entries"]) == 2
    r = client.get("/api/index", params={"kind": "company", "letter": "z"}).json()
    assert r["entries"] == []
    assert client.get("/api/index", params={"kind": "bogus"}).status_code == 400


def test_numbers_masked_reveal_audited(client, tmp_path):
    r = client.get("/api/index", params={"kind": "number"}).json()
    (entry,) = r["entries"]
    assert entry["term"] == "••••1199" and entry["masked"] is True
    assert entry["label"] == "policy number" and entry["count"] == 1
    assert "8842" not in json.dumps(r)          # the full value never leaks into a list
    # typing part of the number finds it (the match runs on the server), still masked
    r = client.get("/api/index", params={"kind": "number", "q": "8842"}).json()
    assert len(r["entries"]) == 1 and r["entries"][0]["term"] == "••••1199"
    # reveal: one entry, in full, and the audit log notes it (without the value)
    r = client.get("/api/index", params={"kind": "number", "reveal": entry["key"]}).json()
    (entry,) = r["entries"]
    assert entry["term"] == "PL-8842-1199" and entry["masked"] is False
    con = db.connect(tmp_path / "paperjay.db")
    try:
        rows = con.execute("SELECT action, detail FROM audit_log WHERE action = 'reveal_number'").fetchall()
        assert len(rows) == 1 and "1199" in rows[0]["detail"] and "8842" not in rows[0]["detail"]
    finally:
        con.close()


def test_people_and_topics_sections(client):
    people = client.get("/api/index", params={"kind": "person"}).json()["entries"]
    assert [(e["term"], e["label"]) for e in people] == [("Sam Ortiz", "insured")]
    topics = client.get("/api/index", params={"kind": "topic"}).json()["entries"]
    assert topics == [] or all(e["count"] >= 1 for e in topics)  # shape only; topics vary by rules
