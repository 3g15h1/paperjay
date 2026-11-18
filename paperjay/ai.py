"""Talking to the AI: an Ollama server or any OpenAI-compatible one. Optional: with the provider set to
"off" every caller gets AIOff and PaperJay works without it.

One request at a time (a home server's CPU or GPU runs one model call well, two badly), and people come
first: background jobs (filing, fact cards, embeddings) wait while someone has asked a question recently.
"""
import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass

import httpx
import numpy as np

from . import embedder, settings

log = logging.getLogger("paperjay")

TRANSPORT = None  # tests swap in an httpx.MockTransport
PEOPLE_FIRST_SECONDS = 120

_lock = threading.Lock()
_last_question = 0.0


class AIOff(RuntimeError):
    pass


class ModelMissing(RuntimeError):
    pass


def note_question() -> None:
    global _last_question
    _last_question = time.time()


def people_waiting() -> bool:
    """True if someone asked a question recently: background AI work should wait."""
    return time.time() - _last_question < PEOPLE_FIRST_SECONDS or _lock.locked()


def busy() -> bool:
    return _lock.locked()


@dataclass(frozen=True)
class Config:
    provider: str
    url: str
    model: str
    api_key: str = ""
    threads: int = 0
    keep_alive: str = "24h"
    embed_model: str = ""
    embed_source: str = "ai"      # builtin | ai | off (see settings.DEFAULTS)
    no_think: bool = False

    KEYS = ("ai_provider", "ai_url", "ai_model", "ai_api_key", "ai_threads", "ai_keep_alive", "embed_model",
            "embed_source", "ai_no_think")

    @property
    def on(self) -> bool:
        return self.provider != "off"

    @property
    def meaning_on(self) -> bool:
        """Search by meaning works: the built-in model (with or without the AI), or the AI's embedding model."""
        if self.embed_source == "builtin":
            return True
        return self.embed_source == "ai" and self.on and bool(self.embed_model)

    @property
    def embed_key(self) -> str:
        """The name stored vectors are filed under ("" with search by meaning off)."""
        if not self.meaning_on:
            return ""
        return embedder.KEY if self.embed_source == "builtin" else self.embed_model

    @classmethod
    def from_settings(cls, s: dict) -> "Config":
        return cls(s["ai_provider"], s["ai_url"].rstrip("/"), s["ai_model"], s["ai_api_key"], s["ai_threads"],
                   s["ai_keep_alive"], s["embed_model"].strip(), s["embed_source"], bool(s["ai_no_think"]))

    @classmethod
    def load(cls, con) -> "Config":
        s = {k: settings.get(con, k) for k in cls.KEYS}
        if not settings.is_stored(con, "embed_source") and settings.is_stored(con, "embed_model") \
                and not s["embed_model"].strip():
            s["embed_source"] = "off"   # turned off before 0.5 by emptying the model name: keep it off
        return cls.from_settings(s)


def _headers(cfg: Config) -> dict:
    return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}


def _ollama_options(cfg: Config, **opts) -> dict:
    if cfg.threads:
        opts["num_thread"] = cfg.threads
    return opts


def _chat_body(cfg: Config, messages: list[dict], schema: dict | None, temperature: float, max_tokens: int,
               stream: bool, ctx: int) -> tuple[str, dict]:
    if cfg.provider == "ollama":
        body = {"model": cfg.model, "messages": messages, "stream": stream, "keep_alive": cfg.keep_alive,
                "options": _ollama_options(cfg, temperature=temperature, num_ctx=ctx, num_predict=max_tokens)}
        if schema:
            body["format"] = schema
        if cfg.no_think:
            body["think"] = False
        return f"{cfg.url}/api/chat", body
    body = {"model": cfg.model, "messages": messages, "stream": stream, "temperature": temperature,
            "max_tokens": max_tokens}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
    if cfg.no_think:  # the two spellings servers read: OpenAI-style effort, and llama.cpp/vLLM's template switch
        body["reasoning_effort"] = "none"
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return f"{_openai_base(cfg)}/chat/completions", body


def _openai_base(cfg: Config) -> str:
    return cfg.url if re.search(r"/v\d+$", cfg.url) else f"{cfg.url}/v1"


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)  # models without structured output wrap JSON in prose or fences
        if not m:
            raise ValueError(f"the model didn't reply with JSON: {text[:200]!r}")
        return json.loads(m.group(0))


def _require(cfg: Config) -> None:
    if not cfg.on:
        raise AIOff("AI is turned off")


def chat(cfg: Config, messages: list[dict], schema: dict | None = None, temperature: float = 0.0,
         max_tokens: int = 500, timeout: float = 600, ctx: int = 4096, wait: float | None = None):
    """One reply (blocking). With a JSON schema, the parsed object. `wait`: give up (TimeoutError) if
    another AI call doesn't finish within this many seconds."""
    _require(cfg)
    if not _lock.acquire(timeout=-1 if wait is None else wait):
        raise TimeoutError("the AI is busy")
    try:
        with httpx.Client(transport=TRANSPORT, timeout=httpx.Timeout(timeout, connect=5)) as c:
            url, body = _chat_body(cfg, messages, schema, temperature, max_tokens, False, ctx)
            r = c.post(url, json=body, headers=_headers(cfg))
            if r.status_code == 400 and schema and cfg.provider == "openai":
                # servers without json_schema support: ask for plain JSON instead
                body["response_format"] = {"type": "json_object"}
                r = c.post(url, json=body, headers=_headers(cfg))
                if r.status_code == 400:
                    body.pop("response_format")
                    r = c.post(url, json=body, headers=_headers(cfg))
            _raise_for(r, cfg.model)
            data = r.json()
    finally:
        _lock.release()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    text = data["message"]["content"] if cfg.provider == "ollama" else data["choices"][0]["message"]["content"]
    return _parse_json(text) if schema else text


async def chat_stream(cfg: Config, messages: list[dict], temperature: float = 0.1, max_tokens: int = 220,
                      ctx: int = 4096):
    """Stream a reply's text as it is written (async generator)."""
    _require(cfg)
    while not _lock.acquire(blocking=False):  # polling, so a client that gives up while waiting can't leak the lock
        await asyncio.sleep(0.2)
    try:
        async with httpx.AsyncClient(transport=TRANSPORT, timeout=httpx.Timeout(240, connect=5)) as c:
            url, body = _chat_body(cfg, messages, None, temperature, max_tokens, True, ctx)
            async with c.stream("POST", url, json=body, headers=_headers(cfg)) as r:
                if r.status_code >= 400:
                    await r.aread()
                    _raise_for(r, cfg.model)
                async for line in r.aiter_lines():
                    tok, done = _stream_piece(cfg, line)
                    if tok:
                        yield tok
                    if done:
                        return
    finally:
        _lock.release()


def _stream_piece(cfg: Config, line: str) -> tuple[str, bool]:
    line = line.strip()
    if not line:
        return "", False
    if cfg.provider == "ollama":
        msg = json.loads(line)
        if msg.get("error"):
            raise RuntimeError(msg["error"])
        return msg.get("message", {}).get("content", ""), bool(msg.get("done"))
    if not line.startswith("data:"):
        return "", False
    data = line[5:].strip()
    if data == "[DONE]":
        return "", True
    msg = json.loads(data)
    choice = (msg.get("choices") or [{}])[0]
    return (choice.get("delta") or {}).get("content") or "", choice.get("finish_reason") is not None


def embed(cfg: Config, texts: list[str], timeout: float = 120, wait: float | None = None,
          kind: str = "document") -> np.ndarray:
    """Unit-length float32 vectors, one row per text. kind: "document" (passages) or "query" (a question);
    only the built-in model uses it."""
    if not cfg.meaning_on:
        raise AIOff("search by meaning is turned off")
    if cfg.embed_source == "builtin":
        return embedder.embed(texts, kind)
    if not _lock.acquire(timeout=-1 if wait is None else wait):
        raise TimeoutError("the AI is busy")
    try:
        with httpx.Client(transport=TRANSPORT, timeout=httpx.Timeout(timeout, connect=5)) as c:
            if cfg.provider == "ollama":
                r = c.post(f"{cfg.url}/api/embed", json={"model": cfg.embed_model, "input": texts,
                                                         "keep_alive": cfg.keep_alive})
                _raise_for(r, cfg.embed_model)
                vecs = r.json()["embeddings"]
            else:
                r = c.post(f"{_openai_base(cfg)}/embeddings", json={"model": cfg.embed_model, "input": texts},
                           headers=_headers(cfg))
                _raise_for(r, cfg.embed_model)
                vecs = [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d.get("index", 0))]
    finally:
        _lock.release()
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim != 2 or len(arr) != len(texts):
        raise RuntimeError(f"unexpected embedding response shape {arr.shape}")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.where(norms == 0, 1, norms)


def _raise_for(r: httpx.Response, model: str) -> None:
    if r.status_code < 400:
        return
    text = r.text[:300]
    if r.status_code == 404 or "not found" in text.lower():
        raise ModelMissing(f"the model {model!r} isn't available on the AI server ({text})")
    if r.status_code in (401, 403):
        raise PermissionError(f"the AI server refused the API key ({r.status_code})")
    raise RuntimeError(f"the AI server answered {r.status_code}: {text}")


def models(cfg: Config) -> list[str]:
    with httpx.Client(transport=TRANSPORT, timeout=5) as c:
        if cfg.provider == "ollama":
            r = c.get(f"{cfg.url}/api/tags")
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        r = c.get(f"{_openai_base(cfg)}/models", headers=_headers(cfg))
        _raise_for(r, cfg.model)
        return sorted(m["id"] for m in r.json().get("data", []))


def _has(names: list[str], model: str) -> bool:
    # servers that list one model under a variant name (e.g. "qwen3.5-9b-q4_k_m" for "qwen3.5-9b") answer to the base name
    return model in names or f"{model}:latest" in names or any(
        n.split(":")[0] == model or n.startswith(f"{model}-") for n in names)


def test(cfg: Config) -> dict:
    """The 'Test connection' button: can we reach the server, are the models there, how fast is it?"""
    out = {"provider": cfg.provider, "ok": False, "steps": []}
    if cfg.embed_source == "builtin":
        t0 = time.monotonic()
        try:
            embed(cfg, ["test"], kind="query")
            out["steps"].append({"step": "embedding model", "ok": True,
                                 "detail": f"built in, works ({time.monotonic() - t0:.1f} seconds)"})
        except Exception as e:
            out["steps"].append({"step": "embedding model", "ok": False, "detail": str(e)})
            return out
    if not cfg.on:
        out["steps"].append({"step": "AI", "ok": True, "detail": "turned off. PaperJay works without it"})
        out["ok"] = True
        return out
    try:
        names = models(cfg)
        out["steps"].append({"step": "server", "ok": True, "detail": f"found it ({len(names)} model{'s' if len(names) != 1 else ''} installed)"})
    except Exception as e:
        out["steps"].append({"step": "server", "ok": False, "detail": f"can't reach it at {cfg.url}. Is it running, and is the address right? ({e})"})
        return out
    out["models"] = names
    ai_embed = cfg.embed_model if cfg.embed_source == "ai" else ""
    for label, model in (("answer model", cfg.model), ("embedding model", ai_embed)):
        if model and not _has(names, model):
            hint = f". To install it, run this on the computer with Ollama: ollama pull {model}" if cfg.provider == "ollama" else ""
            out["steps"].append({"step": label, "ok": False, "detail": f"{model} isn't installed there yet{hint}"})
            return out
    t0 = time.monotonic()
    try:
        reply = chat(cfg, [{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=5,
                     timeout=300, wait=60)
        out["steps"].append({"step": "answer model", "ok": True,
                             "detail": f"{cfg.model} answered in {time.monotonic() - t0:.1f} seconds"})
    except Exception as e:
        out["steps"].append({"step": "answer model", "ok": False, "detail": str(e)})
        return out
    if ai_embed:
        try:
            v = embed(cfg, ["test"], wait=60)
            out["steps"].append({"step": "embedding model", "ok": True,
                                 "detail": f"{cfg.embed_model} works"})
        except Exception as e:
            out["steps"].append({"step": "embedding model", "ok": False, "detail": str(e)})
            return out
    out["ok"] = True
    return out
