"""Suggest + the shared entity matcher (spec C.3): prefix matching over the library's
vocabulary, numbers masked, and the matcher Ask's narrowing will reuse."""
import pytest
from fastapi.testclient import TestClient

from paperjay import entities, indexing, profile
from paperjay.app import create_app
from paperjay.config import Config

from conftest import sign_in_client

BILL = """Acme Energy
Account No: 1234 5678 9012
Statement date: March 1, 2025
Amount due $54.00
Due date: March 21, 2025
Account holder: Jo Miller
Your electricity usage this month was typical for the season."""

POLICY = """FastNet Insurance
Policy number: PL-8842-1199
Insured: Sam Ortiz
Your policy renews on June 1, 2026."""


@pytest.fixture
def seeded(lib, con):
    bill = lib.add(title="Electric bill", checksum="a" * 64, original_name="bill.pdf",
                   original_ext=".pdf", mime_type="application/pdf", size=10, content=BILL,
                   status="ready", doc_date="2025-03-01")
    pol = lib.add(title="Insurance policy", checksum="b" * 64, original_name="policy.pdf",
                  original_ext=".pdf", mime_type="application/pdf", size=10, content=POLICY,
                  status="ready", doc_date="2026-05-01")
    acme = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    fastnet = lib.get_or_create_label("correspondent", "FastNet")[0]
    billtype = lib.get_or_create_label("document_type", "Bill")[0]
    utilities = lib.get_or_create_label("tag", "Utilities")[0]
    lib.update(bill, correspondent_id=acme, document_type_id=billtype, tags=[utilities])
    lib.update(pol, correspondent_id=fastnet, paper_no=124)
    lib.add_alias(fastnet, "Fast Net Co")
    profile.refile(con, bill)
    profile.refile(con, pol)
    return con


def _groups(result):
    return {g["kind"]: g["items"] for g in result["groups"]}


def test_suggest_matches_each_vocabulary(seeded):
    con = seeded
    g = _groups(entities.suggest(con, "acm"))
    assert [(i["name"], i["count"]) for i in g["company"]] == [("Acme Energy", 1)]
    g = _groups(entities.suggest(con, "fast net"))   # an alias finds its company
    assert [i["name"] for i in g["company"]] == ["FastNet"]
    g = _groups(entities.suggest(con, "pol"))
    assert [(i["name"], i["count"]) for i in g["number"]] == [("Policy number ••••1199", 1)]
    g = _groups(entities.suggest(con, "8842"))        # part of the number itself
    assert [i["name"] for i in g["number"]] == ["Policy number ••••1199"]
    g = _groups(entities.suggest(con, "jo"))
    assert [i["name"] for i in g["person"]] == ["Jo Miller"]
    g = _groups(entities.suggest(con, "bil"))
    assert [(i["name"], i["count"]) for i in g["type"]] == [("Bill", 1)]
    assert entities.suggest(con, "x") == {"groups": []}       # too short
    assert entities.suggest(con, "zzzz") == {"groups": []}    # nothing matches


def test_suggest_tags_paper_years_documents(seeded):
    con = seeded
    g = _groups(entities.suggest(con, "uti"))
    assert [(i["name"], i["count"]) for i in g["tag"]] == [("Utilities", 1)]
    g = _groups(entities.suggest(con, "12"))
    assert [i["name"] for i in g["paper"]] == ["Paper #124"]
    g = _groups(entities.suggest(con, "202"))
    assert {i["name"] for i in g["year"]} == {"2025", "2026"}
    g = _groups(entities.suggest(con, "electr"))
    assert [i["name"] for i in g["document"]] == ["Electric bill"]


def test_suggest_all_zero_digits_returns_no_paper(seeded):
    # Regression (review 0.4.0 #1): an all-zero digit query looped forever in the
    # paper group and OOM-killed the app; "012" also wrongly suggested papers
    # 12, 120, … — paper numbers start at 1, so a leading zero matches nothing.
    con = seeded
    for q in ("00", "0.", "000", "0-0", "012"):
        assert "paper" not in _groups(entities.suggest(con, q))
    g = _groups(entities.suggest(con, "12"))   # an ordinary paper prefix still works
    assert [i["name"] for i in g["paper"]] == ["Paper #124"]


def test_find_entities(seeded):
    con = seeded
    found = entities.find_entities(con, "show my acme energy bills")
    assert found["companies"] == ["Acme Energy"] and found["types"] == []
    found = entities.find_entities(con, "the fast net co policy for Sam")
    assert found["companies"] == ["FastNet"] and found["people"] == ["Sam Ortiz"]
    assert entities.number_suffix_matches(con, "papers for the policy ending 1199") == ["pl88421199"]
    assert entities.number_suffix_matches(con, "no digits here") == []


def test_find_entities_first_name_needs_a_real_mention(seeded):
    # Review 0.4.0 #2: a lone first name used to count as naming the person even
    # when the word was an ordinary one, so "Will my car insurance renew in May?"
    # narrowed Ask to Will Smith's papers. A first name alone now needs a
    # possessive or a capitalised mid-sentence mention, and words that are
    # capitalised for other reasons (months, common nouns) never count.
    con = seeded
    doc = con.execute("SELECT id FROM documents LIMIT 1").fetchone()[0]
    for term in ("Will Smith", "May Chen"):
        con.execute("INSERT INTO index_terms(kind, norm, term, label, document_id) "
                    "VALUES ('person', ?, ?, '', ?)", (indexing.squash(term), term, doc))
    assert entities.find_entities(con, "Will my car insurance renew in May?")["people"] == []
    assert entities.find_entities(con, "papers for will")["people"] == []
    assert entities.find_entities(con, "papers for Will")["people"] == ["Will Smith"]
    assert entities.find_entities(con, "Show me Will's insurance")["people"] == ["Will Smith"]
    assert entities.find_entities(con, "Show me Will Smith's insurance")["people"] == ["Will Smith"]


def test_suggest_api(tmp_path, seeded):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)),
                    follow_redirects=False) as client:
        sign_in_client(client)
        r = client.get("/api/suggest", params={"q": "acm"})
        assert r.status_code == 200
        assert r.json()["groups"][0]["kind"] == "company"
        assert client.get("/api/suggest", params={"q": ""}).json() == {"groups": []}
