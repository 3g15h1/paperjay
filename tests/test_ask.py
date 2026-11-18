"""Answers end to end through the HTTP API, against the fake library and a fake Ollama.
Ported from Paperless Ask's tests, so the same questions give the same answers."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from paperjay import ai, ask, db, settings
from paperjay.app import create_app
from paperjay.config import Config

from conftest import sign_in_client
from fake_ai import DOCS, SOON, FakeOllama, load_library

WITH_TEXT = len([d for d in DOCS if d["content"]])


@pytest.fixture
def fake():
    return FakeOllama()


@pytest.fixture
def make(tmp_path, monkeypatch):
    """make(fake, **settings) -> a started TestClient over the fake library."""
    clients = []
    monkeypatch.setattr(ai, "PEOPLE_FIRST_SECONDS", 0)
    monkeypatch.setattr(ask, "INDEX", ask.Index())

    def _make(fake, ai_on=True, **conf):
        path = tmp_path / "paperjay.db"
        if not path.exists():
            load_library(path, ai_on=ai_on, ai_filing=False, **conf)
        elif conf:
            con = db.connect(path)
            settings.save(con, conf)
            con.close()
        monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(fake))
        c = TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)))
        c.__enter__()
        sign_in_client(c, username=f"t{len(clients)}")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def start_ai(c):
    from paperjay.ask_api import queue_missing
    con = db.connect(c.app.state.config.db_path)
    queue_missing(con)
    con.close()
    c.app.state.paperjay["ai_worker"].wake()


def wait_for_cards(c, timeout=15):
    start_ai(c)
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/api/ai/status").json()
        if st["analyzed"] == WITH_TEXT and not st["queued"].get("analyze"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"analysis didn't finish: {st}")


def wait_for_index(c, timeout=15):
    start_ai(c)
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/api/ai/status").json()
        if st["embedded"] == WITH_TEXT and not st["queued"].get("embed"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"embedding didn't finish: {st}")


def sse_events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def post(c, q, **kw):
    return c.post("/api/ask", json={"q": q, **kw}).json()


# ---- from Paperless Ask 1.0 (no fact cards) ----

def test_llm_answer_is_verified_and_cited(make, fake):
    c = make(fake, embed_model="")
    r = post(c, "When does my car insurance expire?", full=True)
    assert r["mode"] == "llm", r
    assert r["answer"] == "Your car insurance expires on October 22, 2026 [1]."
    assert r["unverified"] == []
    # newest card first; last year's near-copy is kept out of the model's prompt
    assert r["sources"][0]["id"] == 2
    prompt = fake.chats[0]["messages"][1]["content"]
    assert "10/22/2026" in prompt and "10/22/2025" not in prompt
    assert "num_thread" not in fake.chats[0]["options"]
    s = r["sources"][0]
    assert s["url"] == "/documents/2" and s["thumb"] == "/api/documents/2/thumb" and s["correspondent"] == "Example Mutual"


def test_unverified_number_is_flagged(make):
    c = make(FakeOllama(answer="It expires on 11/30/2027 [1]."), embed_model="")
    assert post(c, "When does my car insurance expire?", full=True)["unverified"] == ["11/30/2027"]


def test_missing_term_refuses_without_llm(make, fake):
    c = make(fake, embed_model="")
    r = post(c, "What was my Zorblax bill?")
    assert r["refused"] and r["missing"] == ["Zorblax"] and not fake.chats
    assert "Zorblax" in r["answer"]


def test_synonyms_from_settings(make, fake):
    c = make(fake, embed_model="")
    r = post(c, "How much was my hydro bill?", no_llm=True)
    assert not r["refused"] and r["sources"][0]["id"] == 4
    assert {"kind": "amount", "value": "$ 142.37", "n": 1} in r["extractive"]["facts"]
    c.put("/api/settings", json={"ask_synonyms": ""})
    r = post(c, "How much was my hydro bill?", no_llm=True)
    assert r["refused"] and r["missing"] == ["hydro"]


def test_ai_down_falls_back_to_extractive(make):
    c = make(FakeOllama(up=False))
    events = sse_events(c.get("/api/ask/stream", params={"q": "What did my phone cost?"}).text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "sources" and "llm_error" in kinds and events[-1][1]["mode"] == "extractive"
    assert events[0][1]["sources"][0]["id"] == 1


def test_ai_off_answers_from_passages(make, fake):
    c = make(fake, ai_on=False)
    events = sse_events(c.get("/api/ask/stream", params={"q": "What did my phone cost?"}).text)
    assert [e for e, _ in events] == ["sources", "done"] and events[-1][1] == {**events[-1][1], "mode": "extractive", "ai": "off"}
    r = post(c, "What did my phone cost?")
    assert r["mode"] == "extractive" and r["answer"].startswith("Best match: “Electronics receipt”")
    assert not fake.chats and not fake.extractions


def test_stream_tokens(make, fake):
    c = make(fake, embed_model="")
    events = sse_events(c.get("/api/ask/stream", params={"q": "When does my car insurance expire?", "full": 1}).text)
    assert [e for e, _ in events][:2] == ["sources", "stage"]
    assert "".join(d["t"] for e, d in events if e == "token").startswith("Your car insurance")
    assert events[-1][1]["mode"] == "llm"


def test_list_question_needs_no_llm(make, fake):
    c = make(fake, embed_model="")
    r = post(c, "Show me my insurance cards")
    assert r["mode"] == "list" and not fake.chats


def test_threads_setting(make, fake):
    c = make(fake, embed_model="", ai_threads=2)
    post(c, "When does my car insurance expire?", full=True)
    assert fake.chats[0]["options"]["num_thread"] == 2


# ---- fact cards, instant answers, totals, coming up ----

def test_analysis_grounds_and_keeps_cards(make, fake):
    c = make(fake, embed_model="")
    wait_for_cards(c)
    cards = {x["id"]: x for x in c.get("/api/facts").json()["cards"]}
    assert len(cards) == WITH_TEXT  # doc 10 has no text
    assert cards[2]["ids"] == [{"label": "Policy Number", "value": "123456789"}]
    assert "id member='JOHN DOE'" in cards[2]["dropped"] and cards[2]["total"] is None
    assert [d["date"] for d in cards[4]["dates"] if d["label"] == "due"] == ["2025-09-05"]
    assert cards[9]["vendor"] == ""
    n = len(fake.extractions)
    c.__exit__(None, None, None)
    c = make(fake)  # restart: cards come from the database, nothing is redone
    start_ai(c)
    time.sleep(0.5)
    assert len(fake.extractions) == n


def test_changed_text_is_analyzed_again(make, fake):
    c = make(fake, embed_model="")
    wait_for_cards(c)
    n = len(fake.extractions)
    con = db.connect(c.app.state.config.db_path)
    from paperjay.library import Library
    Library(con).update(1, content=DOCS[0]["content"] + "\nThank you")
    con.close()
    end = time.time() + 10
    while len(fake.extractions) == n and time.time() < end:
        start_ai(c)
        time.sleep(0.1)
    assert len(fake.extractions) == n + 1


def test_instant_answers_from_cards(make, fake):
    c = make(fake, embed_model="")
    wait_for_cards(c)
    r = post(c, "When does my car insurance expire?")
    assert (r["mode"], r["answer"]) == ("facts", "Example Mutual insurance expires on October 22, 2026 [1].")
    r = post(c, "What's my policy number?")
    assert (r["mode"], r["answer"]) == ("facts", "Policy Number: 123456789 [1] (Example Mutual).")
    r = post(c, "When is my Northwind payment due?")
    assert r["mode"] == "facts"
    assert r["answer"] == f"$250.00 is due to Northwind Card Services on {SOON:%B} {SOON.day}, {SOON.year} [1]."
    assert not fake.chats
    assert post(c, "When does my car insurance expire?", full=True)["mode"] == "llm" and fake.chats
    assert post(c, "What did I buy at the gadget store?")["mode"] == "llm"  # not a single-fact question


def test_totals(make, fake):
    c = make(fake, embed_model="")
    wait_for_cards(c)
    r = post(c, "How much did I spend on medical in 2025?")
    assert r["mode"] == "total" and not fake.chats and r["total"]["pending"] == 0
    assert r["answer"] == "Total on medical in 2025: $100.65 across 1 document [1]."
    assert r["total"]["total"] == 100.65 and [s["id"] for s in r["sources"]] == [5]
    assert [x["id"] for x in r["total"]["excluded"]] == [6]
    events = [b.split("\n")[0] for b in c.get("/api/ask/stream", params={"q": "Total spent on medical"}).text.split("\n\n") if b]
    assert events == ["event: sources", "event: done"]


def test_upcoming_and_calendar(make, fake):
    c = make(fake, embed_model="", alert_days=[10])
    wait_for_cards(c)
    up = c.get("/api/upcoming").json()
    ids = {(i["id"], i["label"]) for i in up["items"]}
    assert (8, "due") in ids and not any(i == 9 for i, _ in ids)  # doc 9 is tagged Paid
    item = next(i for i in up["items"] if i["id"] == 8)
    assert item["amount"] == 250.0 and item["days_left"] == 10 and item["url"] == "http://testserver/documents/8"
    assert [i["id"] for i in up["alerts"]] == [8]
    cal = c.get("/calendar.ics")
    assert cal.headers["content-type"].startswith("text/calendar")
    assert f"UID:8-due-{SOON:%Y%m%d}@testserver" in cal.text and "http://testserver/documents/8" in cal.text


def test_ai_off_keeps_pattern_reminders(make, fake):
    c = make(fake, ai_on=False)
    up = c.get("/api/upcoming").json()
    assert any(i["id"] == 8 and i["label"] == "due" for i in up["items"])
    assert post(c, "How much did I spend on medical in 2025?", no_llm=True)["total"]["items"] == []
    assert not fake.extractions


# ---- follow-ups, search by meaning, one document, feedback ----

def test_search_by_meaning_finds_a_document_without_the_word(make, fake):
    c = make(fake)
    wait_for_index(c)
    # since Phase A the lease carries a 'rent' topic, so "rent" itself now finds it directly (spec A.2);
    # the honest-refusal + meaning fallback below uses "housing", which no topic or text contains
    r = post(c, "Where is my rent agreement?")
    assert not r["refused"] and r["sources"][0]["id"] == 11
    chats_before = len(fake.chats)
    r = post(c, "Where is my housing agreement?")
    # still an honest "couldn't find", but the closest document in meaning comes first, without the model
    assert r["refused"] and r["mode"] == "none" and len(fake.chats) == chats_before
    assert r["sources"][0]["id"] == 11 and r["semantic"]["11"] >= 0.9 and "closest in meaning" in r["answer"]
    r = post(c, "Where is my housing agreement?", full=True, debug=True)  # "Ask the AI anyway"
    assert r["mode"] == "llm" and "no document mentions “housing”" in r["prompt"] and "Riverside Apartments" in r["prompt"]
    c.put("/api/settings", json={"embed_model": ""})
    r = post(c, "Where is my housing agreement?", no_llm=True)
    assert r["refused"] and r["semantic"] == {} and "closest in meaning" not in r["answer"]


def test_missing_embedding_model_doesnt_break_answers(make):
    fake = FakeOllama()
    fake.embed_model_installed = False
    c = make(fake)
    start_ai(c)
    time.sleep(0.5)
    assert c.get("/api/ai/status").json()["embedded"] == 0
    assert post(c, "When does my car insurance expire?", no_llm=True)["sources"][0]["id"] == 2


def test_follow_ups(make, fake):
    c = make(fake, embed_model="")
    wait_for_cards(c)
    first = post(c, "When does my car insurance expire?")
    assert first["mode"] == "facts" and first["followup"] is False
    prev = {"q": first["context_q"], "a": first["answer"]}
    r = post(c, "And the policy number?", prev=prev)
    assert r["followup"] and r["mode"] == "facts" and r["answer"].startswith("Policy Number: 123456789")
    assert r["context_q"] == "When does my car insurance expire? And the policy number?"
    assert post(c, "and last year's?", prev=prev)["mode"] == "llm"
    assert post(c, "What about the other car?", prev=prev)["mode"] == "llm"
    r = post(c, "and last year's?", prev=prev, full=True, debug=True)
    assert r["mode"] == "llm" and 3 in [s["id"] for s in r["sources"][:2]]
    assert "Earlier in this conversation the user asked: When does my car insurance expire?" in r["prompt"]
    assert "10/22/2025" in r["prompt"]
    assert post(c, "How much was the flooring install?", prev=prev)["followup"] is False


def test_follow_up_stream_params(make, fake):
    c = make(fake, embed_model="")
    text = c.get("/api/ask/stream", params={"q": "and the other one?", "prev_q": "What's my auto policy number?",
                                            "prev_a": "Policy Number: 123456789 [1]."}).text
    sources = json.loads(text.split("\n\n")[0].split("data: ", 1)[1])
    assert sources["followup"] and sources["context_q"] == "What's my auto policy number? and the other one?"
    assert "Earlier in this conversation" in fake.chats[-1]["messages"][1]["content"]


def test_ask_about_one_document(make, fake):
    c = make(fake, embed_model="")
    r = post(c, "Are pets allowed?", doc=11, debug=True)
    assert r["scoped"] == 11 and [s["id"] for s in r["sources"]] == [11] and r["mode"] == "llm"
    assert "Landlord allows one cat" in r["prompt"] and "[2]" not in r["prompt"]
    assert post(c, "Are pets allowed?", doc=999, no_llm=True)["scoped"] is None


def test_feedback_round_trip(make, fake):
    c = make(fake, embed_model="")
    assert c.post("/api/feedback", json={"id": "x1", "q": "What was my DTE bill?", "vote": "sideways"}).status_code == 400
    assert c.post("/api/feedback", json={"id": "x1", "q": "What was my DTE bill?", "vote": "down", "mode": "none",
                                         "missing": ["DTE"], "sources": [{"id": 4, "title": "Utility statement"}]}).json() == {"ok": True}
    c.post("/api/feedback", json={"id": "x1", "q": "What was my DTE bill?", "vote": "down", "comment": "electric bill"})
    f = c.get("/api/feedback").json()
    assert f["down"] == 1 and f["total"] == 1
    assert f["items"][0]["comment"] == "electric bill" and f["items"][0]["missing"] == ["DTE"]


def test_trashed_documents_drop_out_of_answers(make, fake):
    c = make(fake, embed_model="")
    assert post(c, "What did my phone cost?", no_llm=True)["sources"][0]["id"] == 1
    con = db.connect(c.app.state.config.db_path)
    from paperjay.library import Library
    Library(con).trash(1)
    con.close()
    assert 1 not in [s["id"] for s in post(c, "What did my phone cost?", no_llm=True)["sources"]]
