"""Library filters (spec C.5): category, topic, series, people, paper-shelf and the
A–Z term filter — all composing with the existing search."""
import pytest
from fastapi.testclient import TestClient

from paperjay import indexing, profile
from paperjay.app import create_app
from paperjay.config import Config

from conftest import sign_in_client

BILL = """Acme Energy
Account No: 1234 5678 9012
Amount due $54.00
Account holder: Jo Miller
Your electricity usage this month was typical for the season."""

LETTER = """City Water
Your water service continues as normal this spring."""


@pytest.fixture
def seeded(lib, con):
    bill = lib.add(title="Electric bill", checksum="a" * 64, original_name="bill.pdf",
                   original_ext=".pdf", mime_type="application/pdf", size=10, content=BILL,
                   status="ready", doc_date="2025-03-01")
    letter = lib.add(title="Water letter", checksum="b" * 64, original_name="letter.pdf",
                     original_ext=".pdf", mime_type="application/pdf", size=10, content=LETTER,
                     status="ready", doc_date="2025-04-01")
    acme = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    lib.update(bill, correspondent_id=acme)
    lib.update(letter, category="home")
    profile.refile(con, bill)   # builds the bill's cards, topics and index terms
    # the person's own filing, set after the refile so the profile doesn't recompute it away
    lib.update(bill, category="utilities", paper_no=7, series_key="acme|bill")
    con.execute("DELETE FROM document_topics")
    con.execute("INSERT INTO document_topics(document_id, topic) "
                "VALUES (?, 'utilities & energy')", (bill,))
    con.execute("INSERT INTO document_topics(document_id, topic) "
                "VALUES (?, 'utilities & energy')", (letter,))
    return {"bill": bill, "letter": letter, "lib": lib}


def _ids(result):
    return [r["id"] for r in result["results"]]


def test_category_topic_series_filters(seeded):
    lib = seeded["lib"]
    assert _ids(lib.search(category="utilities")) == [seeded["bill"]]
    assert _ids(lib.search(category="home")) == [seeded["letter"]]
    assert _ids(lib.search(topic="utilities & energy")) == [seeded["letter"], seeded["bill"]]
    assert _ids(lib.search(series="acme|bill")) == [seeded["bill"]]
    assert _ids(lib.search(category="tax")) == []


def test_people_and_paper_filters(seeded):
    lib = seeded["lib"]
    assert _ids(lib.search(people=("Jo Miller",))) == [seeded["bill"]]
    assert _ids(lib.search(people=("jo miller",))) == [seeded["bill"]]  # case doesn't matter
    assert _ids(lib.search(people=("Nobody Here",))) == []
    assert _ids(lib.search(has_paper=True)) == [seeded["bill"]]
    assert _ids(lib.search(has_paper=False)) == [seeded["letter"]]
    assert _ids(lib.search(paper_no=7)) == [seeded["bill"]]
    assert _ids(lib.search(paper_no=8)) == []
    with pytest.raises(ValueError):
        lib.search(paper_no=0)


def test_term_filter_uses_the_index_key(seeded, con):
    lib = seeded["lib"]
    key = indexing.term_key(con, "company", "acmeenergy")
    assert _ids(lib.search(term=f"company:{key}")) == [seeded["bill"]]
    key = indexing.term_key(con, "number", "123456789012")
    assert _ids(lib.search(term=f"number:{key}")) == [seeded["bill"]]
    assert _ids(lib.search(term="company:deadbeefdeadbeef")) == []   # unknown key: nothing
    with pytest.raises(ValueError):
        lib.search(term="bogus:abc")


def test_filters_compose_with_words(seeded):
    lib = seeded["lib"]
    r = lib.search("electricity", category="utilities")
    assert _ids(r) == [seeded["bill"]]
    r = lib.search("electricity", category="home")
    assert r["total"] == 0


def test_filters_via_the_api(tmp_path, seeded, con):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)),
                    follow_redirects=False) as client:
        sign_in_client(client)
        r = client.get("/api/documents", params={"category": "utilities", "has_paper": "true"})
        assert [d["id"] for d in r.json()["results"]] == [seeded["bill"]]
        key = indexing.term_key(con, "person", "jomiller")
        r = client.get("/api/documents", params={"term": f"person:{key}"})
        assert [d["id"] for d in r.json()["results"]] == [seeded["bill"]]
        r = client.get("/api/documents", params={"term": "bogus:abc"})
        assert r.status_code == 400
