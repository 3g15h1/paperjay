"""AI gets the context (spec B.4): hints in the prompt, topics/people in the schema,
old cards stay valid, and re-reads are selective and kept inside the batch window."""
import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from paperjay import ai, analyze, ask, ask_api, jobs, profile, settings
from paperjay.app import create_app
from paperjay.config import Config

from conftest import sign_in_client
from fake_ai import load_library

TEXT = ("Acme Energy\nAccount No: 4471229018\nStatement date: August 14, 2025\n"
        "Amount due $142.37\nDue date: September 5, 2025\nPatient: Jane Doe\nElectric service.")
BILL_ONLY = ("Acme Energy\nAccount No: 4471229018\nStatement date: August 14, 2025\n"
             "Amount due $142.37\nDue date: September 5, 2025\nElectric service.")


def _reply(**kw):
    base = {"kind": "bill", "category": "utilities", "vendor": "Acme Energy",
            "title": "Acme Energy bill", "summary": "Electric bill.", "total": "142.37",
            "dates": [{"label": "issued", "date": "2025-08-14"}],
            "ids": [{"label": "account", "value": "4471229018"}], "tags": [],
            "topics": ["electricity"], "people": [{"label": "patient", "name": "Jane Doe"}]}
    base.update(kw)
    return base


def test_ground_keeps_vocabulary_topics_and_labelled_people():
    card = analyze.ground(_reply(topics=["electricity", "not-a-topic", "internet", "water bill"]),
                          TEXT, "", [], topic_names=["electricity", "internet", "water bill"])
    assert card["topics"] == ["electricity", "internet", "water bill"]
    assert "topic 'not-a-topic'" in card["dropped"]
    assert card["people"] == [{"label": "patient", "name": "Jane Doe"}]


def test_ground_drops_ungrounded_people_and_caps_topics():
    card = analyze.ground(
        _reply(topics=["a", "b", "c", "d"], people=[{"label": "patient", "name": "Nobody Here"}]),
        TEXT, "", [], topic_names=["a", "b", "c", "d"])
    assert card["topics"] == ["a", "b", "c"]
    assert card["people"] == []
    assert "person 'Nobody Here'" in card["dropped"]


def test_schema_has_topics_enum_and_people():
    s = analyze.schema([], ["electricity", "internet"])
    assert s["properties"]["topics"]["maxItems"] == 3
    assert s["properties"]["topics"]["items"]["enum"] == ["electricity", "internet"]
    assert s["properties"]["people"]["items"]["required"] == ["label", "name"]


def test_messages_carry_hints():
    msgs = analyze.messages("T", TEXT, [], hints=["Text patterns read: company Acme Energy."])
    assert "Hints from the document's own text patterns" in msgs[1]["content"]
    assert "company Acme Energy" in msgs[1]["content"]
    msgs = analyze.messages("T", TEXT, [])
    assert "Hints" not in msgs[1]["content"]


def test_v3_card_stays_valid_after_the_version_bump(con, lib, add_doc):
    assert analyze.EXTRACTOR_VERSION == 4
    d = add_doc(content=TEXT, status="ready")
    card = analyze.ground(_reply(), TEXT, "", [])
    con.execute("INSERT INTO facts(document_id, source, content_hash, model, version, card) "
                "VALUES (?, 'ai', ?, 'm', 3, ?)",
                (d, analyze.content_hash(TEXT), json.dumps(card)))
    con.commit()
    assert analyze.get_card(con, d)["vendor"] == "Acme Energy"  # v3 accepted, no re-read forced
    assert d in analyze.current_cards(con)
    con.execute("UPDATE facts SET version = 2 WHERE document_id = ?", (d,))
    con.commit()
    assert analyze.get_card(con, d) is None  # older than v3 is too old


def test_current_cards_ignore_other_sources(con, lib, add_doc):
    d = add_doc(content=TEXT, status="ready")
    profile.save_card(con, d, "rules", TEXT, "", 1, {"kind": "bill", "vendor": "Rules Co"})
    profile.save_card(con, d, "library", TEXT, "", 1, {"kind": "bill", "vendor": "Library Co"})
    assert analyze.current_cards(con) == {}  # neither is an AI card


def test_needs_reread():
    full = {"vendor": "Acme", "kind": "bill", "topics": ["electricity"], "review": [],
            "low_evidence": []}
    assert not analyze.needs_reread(full)
    # single-source fields and missing topics are not gaps (0.3.2: 32 of 34 docs were re-read nightly)
    assert not analyze.needs_reread({**full, "topics": []})
    assert not analyze.needs_reread({**full, "low_evidence": ["total", "vendor"]})
    assert analyze.needs_reread({**full, "vendor": ""})
    assert analyze.needs_reread({**full, "kind": ""})
    assert analyze.needs_reread({**full, "review": ["vendor: AI says 'A', the text pattern says 'B'"]})
    # at most once per extractor version: a card already from this version is never re-read again
    assert not analyze.needs_reread({**full, "vendor": ""}, ai_version=analyze.EXTRACTOR_VERSION)
    assert analyze.needs_reread({**full, "vendor": ""}, ai_version=analyze.EXTRACTOR_VERSION - 1)
    assert not analyze.needs_reread({**full, "review": ["category: only a low-confidence value"]})


def test_batch_window(con):
    settings.save(con, {"ai_batch_hours": "01:00-06:00"})
    assert analyze.in_batch_window(con, now=datetime(2026, 1, 1, 3, 0))
    assert not analyze.in_batch_window(con, now=datetime(2026, 1, 1, 12, 0))
    settings.save(con, {"ai_batch_hours": "22:00-06:00"})  # wraps past midnight
    assert analyze.in_batch_window(con, now=datetime(2026, 1, 1, 23, 30))
    assert analyze.in_batch_window(con, now=datetime(2026, 1, 1, 5, 0))
    assert not analyze.in_batch_window(con, now=datetime(2026, 1, 1, 12, 0))


def _ai_on(con):
    settings.save(con, {"ai_provider": "ollama", "ai_url": "http://ollama.test",
                        "embed_model": ""})


def test_queue_missing_rereads_selectively_inside_the_window(con, lib, add_doc, monkeypatch):
    _ai_on(con)
    d = add_doc(content=BILL_ONLY, status="ready")
    n = ask_api.queue_missing(con)
    assert n == 1  # no card yet: queued as new work, window or not
    card = analyze.ground(_reply(people=[]), BILL_ONLY, "", [])
    analyze.save_card(con, d, BILL_ONLY, "m", card)
    profile.refile(con, d)  # a complete profile: vendor, kind, topics all present
    monkeypatch.setattr(analyze, "in_batch_window", lambda con: False)
    assert ask_api.queue_missing(con) == 0
    monkeypatch.setattr(analyze, "in_batch_window", lambda con: True)
    # the profile is complete, so even inside the window there is nothing to re-read
    assert ask_api.queue_missing(con) == 0


def test_queue_missing_rereads_a_gappy_profile_only_in_the_window(con, lib, add_doc, monkeypatch):
    _ai_on(con)
    d = add_doc(content=TEXT, status="ready")
    card = analyze.ground(_reply(), TEXT, "", [])
    analyze.save_card(con, d, TEXT, "m", card)
    # an older extractor's card, and a profile with a gap: the one case that earns a re-read
    con.execute("UPDATE facts SET version = ? WHERE document_id = ? AND source = 'ai'",
                (analyze.EXTRACTOR_VERSION - 1, d))
    seen = []
    monkeypatch.setattr(analyze, "needs_reread",
                        lambda prof, v=None: seen.append(v) or v < analyze.EXTRACTOR_VERSION)
    monkeypatch.setattr(analyze, "in_batch_window", lambda con: False)
    assert ask_api.queue_missing(con) == 0  # bulk re-reads wait for the window
    monkeypatch.setattr(analyze, "in_batch_window", lambda con: True)
    assert ask_api.queue_missing(con) == 1
    assert seen == [analyze.EXTRACTOR_VERSION - 1]  # the stored card's version is passed on
    rows = con.execute("SELECT kind, payload FROM jobs WHERE document_id = ?", (d,)).fetchall()
    assert rows and json.loads(rows[0]["payload"]) == {"reread": True}


def test_queue_missing_never_rereads_the_same_version_twice(con, lib, add_doc, monkeypatch):
    """0.3.2: the 15-minute upkeep re-queued every gappy document after each re-read, all night."""
    _ai_on(con)
    d = add_doc(content=TEXT, status="ready")
    card = analyze.ground(_reply(vendor="", topics=[]), TEXT, "", [])
    analyze.save_card(con, d, TEXT, "m", card)  # saved as the current EXTRACTOR_VERSION
    monkeypatch.setattr(analyze, "in_batch_window", lambda con: True)
    assert ask_api.queue_missing(con) == 0


def test_queue_missing_skips_a_recent_failed_read(con, lib, add_doc, monkeypatch):
    _ai_on(con)
    d = add_doc(content=TEXT, status="ready")
    card = analyze.ground(_reply(vendor=""), TEXT, "", [])
    analyze.save_card(con, d, TEXT, "m", card)
    con.execute("UPDATE facts SET version = ? WHERE document_id = ?", (analyze.EXTRACTOR_VERSION - 1, d))
    con.execute("INSERT INTO jobs(kind, document_id, status, run_after, created_at, finished_at) "
                "VALUES ('analyze', ?, 'failed', 0, 0, strftime('%s','now'))", (d,))
    monkeypatch.setattr(analyze, "in_batch_window", lambda con: True)
    assert ask_api.queue_missing(con) == 0


def test_job_sends_hints_and_stores_topics(con, lib, add_doc, monkeypatch):
    _ai_on(con)
    d = add_doc(content=TEXT, status="ready")
    profile.refile(con, d)  # the rules card exists first, as in production (profile@90 < analyze@100)
    seen = []

    def fake(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"message": {"content": json.dumps(_reply())},
                                         "done": True})

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(ai, "PEOPLE_FIRST_SECONDS", 0)
    analyze.make_analyze()(con, {"document_id": d, "payload": {}})
    user_msg = seen[0]["messages"][1]["content"]
    assert "Text patterns read:" in user_msg and "Acme Energy" in user_msg
    props = seen[0]["format"]["properties"]
    assert "electricity" in props["topics"]["items"]["enum"]
    card = analyze.get_card(con, d)
    assert card["topics"] == ["electricity"]
    assert card["people"] == [{"label": "patient", "name": "Jane Doe"}]
    # a selective re-read job runs even though a valid card exists
    analyze.make_analyze()(con, {"document_id": d, "payload": {"reread": True}})
    assert len(seen) == 2


@pytest.fixture
def client(tmp_path, monkeypatch):
    load_library(tmp_path / "paperjay.db", ai_on=False)
    monkeypatch.setattr(ask, "INDEX", ask.Index())
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)),
                    follow_redirects=False) as c:
        sign_in_client(c)
        yield c


def test_reread_all_reports_estimate_then_queues(client):
    r = client.post("/api/ai/reread-all", json={})
    assert r.status_code == 200
    out = r.json()
    assert out["documents"] == 10 and out["queued"] == 0  # 11 documents, one has no text yet
    assert out["estimate_seconds"] == 10 * 40
    r = client.post("/api/ai/reread-all", json={"confirm": True})
    assert r.status_code == 400  # the fake library's AI is off: nothing to re-read with
    con = client.app.state.config
    from paperjay import db as pdb
    c2 = pdb.connect(con.db_path)
    settings.save(c2, {"ai_provider": "ollama", "ai_url": "http://ollama.test", "embed_model": ""})
    c2.close()
    r = client.post("/api/ai/reread-all", json={"confirm": True})
    assert r.json()["queued"] == 10
