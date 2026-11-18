"""The built-in model for search by meaning (0.5): on by default, works with the AI off, uses nomic's task
prefixes, and keeps a deliberate 'off' from before 0.5. A fake backend stands in for the real model."""
import json

import httpx
import numpy as np
import pytest

from paperjay import ai, ask_api, embedder, meaning, settings
from paperjay.db import set_setting
from fake_ai import fake_embedding

BILL = "Acme Energy\nElectric service for February.\nAmount due: $142.37\n"
LAWN = "GreenThumb LLC\nMowing and edging, monthly service.\nTotal: $120.00\n"


@pytest.fixture
def builtin(monkeypatch):
    """Search by meaning on the built-in model; records what the model was given."""
    monkeypatch.delenv("EMBED_SOURCE", raising=False)
    monkeypatch.setattr(ai, "_last_question", 0.0)           # no question asked lately: background jobs may run
    seen = []

    def backend(texts):
        seen.extend(texts)
        return [fake_embedding(t.split(": ", 1)[1]) for t in texts]

    monkeypatch.setattr(embedder, "BACKEND", backend)
    return seen


def test_builtin_is_the_default_and_needs_no_ai(con, builtin):
    cfg = ai.Config.load(con)
    assert (cfg.provider, cfg.embed_source) == ("off", "builtin")
    assert cfg.meaning_on and cfg.embed_key == embedder.KEY


def test_off_before_0_5_stays_off(con, builtin):
    set_setting(con, "setting:embed_model", json.dumps(""))   # how search by meaning was turned off before 0.5
    assert ai.Config.load(con).embed_source == "off"
    assert not ai.Config.load(con).meaning_on
    settings.save(con, {"embed_source": "builtin"})           # choosing it again wins
    assert ai.Config.load(con).meaning_on


def test_ai_source_needs_the_ai_and_a_model(con, builtin):
    settings.save(con, {"embed_source": "ai"})
    assert not ai.Config.load(con).meaning_on                 # AI off
    settings.save(con, {"ai_provider": "ollama"})
    cfg = ai.Config.load(con)
    assert cfg.meaning_on and cfg.embed_key == "nomic-embed-text"
    with pytest.raises(ValueError):
        settings.save(con, {"embed_source": "somewhere"})


def test_prefixes_and_unit_vectors(builtin):
    v = embedder.embed(["electric bill"], "document")
    q = embedder.embed(["power"], "query")
    assert builtin == ["search_document: electric bill", "search_query: power"]
    assert np.allclose(np.linalg.norm(v, axis=1), 1) and np.allclose(np.linalg.norm(q, axis=1), 1)


def test_ai_embed_routes_to_the_builtin_model_without_the_ai(con, builtin, monkeypatch):
    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(lambda r: pytest.fail("no AI call expected")))
    cfg = ai.Config.load(con)
    out = ai.embed(cfg, ["lawn mowing"], kind="query")
    assert out.shape[0] == 1 and builtin == ["search_query: lawn mowing"]


def test_after_consume_and_embed_job_with_ai_off(con, add_doc, builtin):
    bill = add_doc(name="bill.pdf", content=BILL, status="ready")
    lawn = add_doc(name="lawn.pdf", content=LAWN, status="ready")
    ask_api.after_consume(con, bill)
    kinds = sorted(r[0] for r in con.execute("SELECT kind FROM jobs WHERE document_id = ?", (bill,)))
    assert kinds == ["embed", "profile"]                      # no analyze job: the AI is off
    assert ask_api.queue_missing(con) >= 1                    # the lawn document is missing its vectors
    job = meaning.make_embed()
    for doc_id in (bill, lawn):
        job(con, {"document_id": doc_id})
    assert meaning.indexed(con, embedder.KEY) == {bill, lawn}
    assert all(t.startswith("search_document: ") for t in builtin)
    builtin.clear()
    scores = meaning.scores(con, "grass cutting")
    assert builtin == ["search_query: grass cutting"]
    assert max(scores, key=scores.get) == lawn


def test_connection_test_checks_the_builtin_model(con, builtin):
    r = ai.test(ai.Config.load(con))
    assert r["ok"] and r["steps"][0]["step"] == "embedding model" and "built in" in r["steps"][0]["detail"]


def test_unavailable_model_defers_the_job(con, add_doc, builtin, monkeypatch):
    from paperjay import jobs

    def broken(texts):
        raise embedder.Unavailable("no internet")

    monkeypatch.setattr(embedder, "BACKEND", broken)
    doc = add_doc(content=BILL, status="ready")
    with pytest.raises(jobs.Defer):
        meaning.make_embed()(con, {"document_id": doc})


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_no_think_asks_for_answers_without_thinking(provider):
    on = ai.Config(provider, "http://llm.test", "m", no_think=True)
    off = ai.Config(provider, "http://llm.test", "m")
    _, body_on = ai._chat_body(on, [{"role": "user", "content": "hi"}], None, 0.0, 10, False, 4096)
    _, body_off = ai._chat_body(off, [{"role": "user", "content": "hi"}], None, 0.0, 10, False, 4096)
    if provider == "openai":
        assert body_on["reasoning_effort"] == "none"
        assert body_on["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning_effort" not in body_off and "chat_template_kwargs" not in body_off
    else:
        assert body_on["think"] is False and "think" not in body_off


def test_model_listed_under_a_variant_name_counts_as_installed():
    assert ai._has(["qwen3.5-9b-q4_k_m"], "qwen3.5-9b")
    assert ai._has(["qwen2.5:3b-instruct"], "qwen2.5")
    assert not ai._has(["qwen3.5-9b-q4_k_m"], "qwen3")
