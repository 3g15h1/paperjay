"""AI providers (Ollama and OpenAI-compatible), settings, and the connection test."""
import asyncio
import json

import httpx
import pytest

from paperjay import ai, db, settings


def cfg(provider="openai", **kw):
    base = dict(provider=provider, url="http://llm.test", model="m", api_key="sk-1", embed_model="e")
    return ai.Config(**{**base, **kw})


class FakeOpenAI:
    """An OpenAI-compatible server that, like many local ones, may not support json_schema."""

    def __init__(self, json_schema=True):
        self.json_schema, self.requests = json_schema, []

    def __call__(self, request):
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer sk-1"
        path = request.url.path
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m"}, {"id": "e"}]})
        if path == "/v1/embeddings":
            body = json.loads(request.content)
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [3.0, 4.0]}
                                                      for i, _ in enumerate(body["input"])]})
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            rf = body.get("response_format", {}).get("type")
            if rf == "json_schema" and not self.json_schema:
                return httpx.Response(400, json={"error": "response_format json_schema not supported"})
            if body.get("stream"):
                chunks = [{"choices": [{"delta": {"content": w}, "finish_reason": None}]} for w in ("Hello ", "there")]
                lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
                return httpx.Response(200, content="\n\n".join(lines).encode())
            content = '```json\n{"kind": "bill"}\n```' if rf is None else '{"kind": "bill"}'
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(404)


@pytest.fixture
def server(monkeypatch):
    def install(fake):
        monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(fake))
        return fake
    return install


def test_openai_structured_output_with_fallbacks(server):
    f = server(FakeOpenAI())
    assert ai.chat(cfg(), [{"role": "user", "content": "x"}], schema={"type": "object"}) == {"kind": "bill"}
    f = server(FakeOpenAI(json_schema=False))
    assert ai.chat(cfg(), [{"role": "user", "content": "x"}], schema={"type": "object"}) == {"kind": "bill"}
    kinds = [json.loads(r.content).get("response_format", {}).get("type") for r in f.requests]
    assert kinds == ["json_schema", "json_object"]


def test_openai_stream_and_embeddings(server):
    server(FakeOpenAI())

    async def collect():
        return [t async for t in ai.chat_stream(cfg(), [{"role": "user", "content": "x"}])]
    assert asyncio.run(collect()) == ["Hello ", "there"]
    v = ai.embed(cfg(), ["a", "b"])
    assert v.shape == (2, 2) and abs(v[0][0] - 0.6) < 1e-6  # normalised


def test_url_with_or_without_v1(server):
    f = server(FakeOpenAI())
    ai.models(cfg(url="http://llm.test/v1"))
    ai.models(cfg(url="http://llm.test"))
    assert [r.url.path for r in f.requests] == ["/v1/models", "/v1/models"]


def test_off_means_off():
    with pytest.raises(ai.AIOff):
        ai.chat(cfg(provider="off"), [])
    assert ai.test(cfg(provider="off"))["ok"]


def test_connection_test_reports_each_step(server):
    server(FakeOpenAI())
    r = ai.test(cfg())
    assert r["ok"] and [s["step"] for s in r["steps"]] == ["server", "answer model", "embedding model"]
    r = ai.test(cfg(model="missing"))
    assert not r["ok"] and "isn't installed" in r["steps"][-1]["detail"]

    def down(request):
        raise httpx.ConnectError("refused", request=request)
    server(down)
    r = ai.test(cfg())
    assert not r["ok"] and r["steps"][0]["step"] == "server" and "can't reach" in r["steps"][0]["detail"]


def test_ollama_missing_model_hint(server):
    server(lambda r: httpx.Response(200, json={"models": [{"name": "llama3:latest"}]}))
    r = ai.test(cfg(provider="ollama", model="qwen2.5:3b-instruct"))
    assert "ollama pull qwen2.5:3b-instruct" in r["steps"][-1]["detail"]


def test_busy_lock_and_wait(server):
    server(FakeOpenAI())
    ai._lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            ai.chat(cfg(), [{"role": "user", "content": "x"}], wait=0.1)
        assert ai.busy() and ai.people_waiting()
    finally:
        ai._lock.release()


# ---- settings ----

def test_settings_defaults_env_and_validation(tmp_path, monkeypatch):
    db.migrate(tmp_path / "p.db", tmp_path / "b")
    con = db.connect(tmp_path / "p.db")
    assert settings.get(con, "ai_provider") == "off"
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("AI_THREADS", "4")
    assert settings.get(con, "ai_provider") == "ollama" and settings.get(con, "ai_threads") == 4
    settings.save(con, {"ai_provider": "openai", "ai_api_key": "sk-secret", "ai_url": "http://x/ "})
    s = settings.all_settings(con)
    assert s["ai_provider"] == "openai" and s["ai_api_key"] == "••••••" and s["ai_url"] == "http://x"
    settings.save(con, {"ai_api_key": "••••••"})  # sending the mask back keeps the key
    assert settings.get(con, "ai_api_key") == "sk-secret"
    for bad in ({"ai_provider": "gpt"}, {"ai_url": "ftp://x"}, {"ai_threads": -1}, {"ai_filing": "yes"},
                {"alert_days": ["x"]}, {"nope": 1}):
        with pytest.raises((ValueError, KeyError)):
            settings.save(con, bad)
