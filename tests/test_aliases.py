"""Corrections teach (spec B.2): a name a document is read as becomes an alias of the
company a person files it under; merges keep the merged name as an alias."""
import pytest
from fastapi.testclient import TestClient

from paperjay import ask, profile, rules
from paperjay.app import create_app
from paperjay.config import Config

from conftest import sign_in_client
from fake_ai import load_library

GM_TEXT = "NW Financial\nAccount No: 1234 9876\nAmount due $300.00\nDue date: May 1, 2026"


def test_person_correction_stores_alias(con, lib, add_doc):
    d = add_doc(content=GM_TEXT, status="ready")
    card = rules.extract(GM_TEXT, "")
    profile.save_card(con, d, "rules", GM_TEXT, "", rules.RULES_VERSION, card)
    gm = lib.get_or_create_label("correspondent", "Northwind Motors")[0]
    lib.update(d, correspondent_id=gm)  # the person files it under a different name
    assert lib.aliases() == {"NW Financial": "Northwind Motors"}
    assert lib.list_aliases(gm)[0]["source"] == "person"


def test_no_alias_when_names_agree(con, lib, add_doc):
    d = add_doc(content=GM_TEXT, status="ready")
    card = rules.extract(GM_TEXT, "")
    profile.save_card(con, d, "rules", GM_TEXT, "", rules.RULES_VERSION, card)
    gmf = lib.get_or_create_label("correspondent", "NW Financial")[0]
    lib.update(d, correspondent_id=gmf)
    assert lib.aliases() == {}


def test_alias_lets_rules_file_the_company(con, lib):
    gm = lib.get_or_create_label("correspondent", "Northwind Motors")[0]
    lib.add_alias(gm, "NW Financial")
    card = rules.extract("NW Financial\nAmount due $10.00\n", "", aliases=lib.aliases())
    assert card["vendor"] == "Northwind Motors" and card["vendor_may_file"]


def test_refile_passes_aliases_to_rules(con, lib, add_doc):
    gm = lib.get_or_create_label("correspondent", "Northwind Motors")[0]
    lib.add_alias(gm, "NW Financial")
    d = add_doc(content=GM_TEXT, status="ready")
    profile.refile(con, d)
    assert lib.get(d)["correspondent"] == "Northwind Motors"


def test_merge_keeps_merged_name_as_alias(con, lib, add_doc):
    a = lib.get_or_create_label("correspondent", "Comcast")[0]
    b = lib.get_or_create_label("correspondent", "Xfinity")[0]
    d = add_doc(content="Comcast bill text", status="ready")
    lib.update(d, correspondent_id=a)
    lib.merge_labels("correspondent", [a], b)
    assert lib.get(d)["correspondent"] == "Xfinity"
    assert lib.aliases() == {"Comcast": "Xfinity"}
    assert lib.list_aliases(b)[0]["source"] == "merge"


def test_alias_for_own_or_another_company_teaches_nothing(con, lib):
    a = lib.get_or_create_label("correspondent", "Xfinity")[0]
    lib.get_or_create_label("correspondent", "Comcast")
    assert lib.add_alias(a, "Xfinity") is None
    assert lib.add_alias(a, "Comcast") is None
    assert lib.aliases() == {}


def test_delete_alias(con, lib):
    gm = lib.get_or_create_label("correspondent", "Northwind Motors")[0]
    alias_id = lib.add_alias(gm, "NW Financial")
    lib.delete_alias(gm, alias_id)
    assert lib.aliases() == {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    load_library(tmp_path / "paperjay.db", ai_on=False)
    monkeypatch.setattr(ask, "INDEX", ask.Index())
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)),
                    follow_redirects=False) as c:
        sign_in_client(c)
        yield c


def test_aliases_api(client):
    corr = client.post("/api/labels/correspondent", json={"name": "Northwind Motors"}).json()
    r = client.post(f"/api/labels/correspondent/{corr['id']}/aliases",
                    json={"alias": "NW Financial"})
    assert r.status_code == 200
    aliases = r.json()["aliases"]
    assert [a["alias"] for a in aliases] == ["NW Financial"]
    got = client.get(f"/api/labels/correspondent/{corr['id']}/aliases").json()["aliases"]
    assert got == aliases
    r = client.delete(f"/api/labels/correspondent/{corr['id']}/aliases/{aliases[0]['id']}")
    assert r.json()["aliases"] == []
    assert client.post(f"/api/labels/correspondent/{corr['id']}/aliases",
                       json={"alias": "Northwind Motors"}).status_code == 400
    assert client.get("/api/labels/correspondent/9999/aliases").status_code == 404
