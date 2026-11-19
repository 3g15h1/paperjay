"""Reading hard pages with a vision model (0.8.1): which pages are weak, the request each provider gets,
the job replacing a page's text, the setting and the button."""
import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF

from paperjay import ai, db, jobs, settings, vision
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.library import Library
from paperjay.store import FileStore
from conftest import sign_in_client

GOOD = ("Example Mutual Insurance automobile identification card policy number effective date expiration "
        "date named insured vehicle compact sedan keep this card in your vehicle report claims")


def test_weak_pages():
    assert not vision.weak(GOOD)
    assert vision.weak("")
    assert vision.weak("Dr. Smth  pls  rx  12/03")
    assert vision.weak("lkjh sdfg wrtz mnbv xcvb qwrt plkm zxcv bnmq wrtp sdfk " * 2 + "the and")


def test_messages_per_provider():
    png = b"\x89PNG fake"
    m = vision.message(ai.Config("ollama", "http://x", "qwen2.5vl"), png)
    assert m["images"] == [base64.b64encode(png).decode()] and "exactly" in m["content"]
    m = vision.message(ai.Config("openai", "http://x", "gpt"), png)
    assert m["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def two_page_pdf() -> bytes:
    pdf = FPDF()
    for text in (GOOD, "scribbles"):
        pdf.add_page()
        pdf.set_font("helvetica", size=12)
        pdf.multi_cell(0, 6, text)
    return bytes(pdf.output())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db.migrate(tmp_path / "paperjay.db", tmp_path / "b")
    con = db.connect(tmp_path / "paperjay.db")
    store = FileStore(tmp_path / "media")
    staged = store.stage(io.BytesIO(two_page_pdf()))
    store.commit(staged, ".pdf")
    lib = Library(con)
    doc_id = lib.add(title="Card", checksum=staged.checksum, original_name="card.pdf", original_ext=".pdf",
                     mime_type="application/pdf", size=staged.size, status="ready",
                     content=f"{GOOD}\fscr1bbl3s")
    lib.update(doc_id, by="system", page_count=2)
    settings.save(con, {"ai_provider": "ollama", "ai_url": "http://ai.test", "vision_model": "qwen2.5vl:7b",
                        "vision_ocr": "weak"})
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["model"] == "qwen2.5vl:7b" and body["messages"][0]["images"][0]
        return httpx.Response(200, json={"message": {"content": "Here is the text on this page:\n"
                                                                "Dear Alex, your prescription is ready. Dr. Smith"}})
    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(handler))
    yield con, store, lib, doc_id, seen
    con.close()


def test_the_weak_page_is_read_again(setup):
    con, store, lib, doc_id, seen = setup
    vision.after_consume(con, doc_id)
    job = con.execute("SELECT * FROM jobs WHERE kind = 'vision'").fetchone()
    assert job and json.loads(job["payload"]) == {"all": False}
    vision.make_vision(store)(con, {"document_id": doc_id, "payload": {"all": False}})
    d = lib.get(doc_id)
    assert len(seen) == 1                                       # only page 2 went to the model
    assert d["content"] == f"{GOOD}\fDear Alex, your prescription is ready. Dr. Smith"
    assert "Page 2 was read by the AI (qwen2.5vl:7b)" in d["error"]
    assert con.execute("SELECT 1 FROM audit_log WHERE action = 'vision' AND document_id = ?", (doc_id,)).fetchone()
    assert con.execute("SELECT 1 FROM jobs WHERE kind = 'profile' AND document_id = ?", (doc_id,)).fetchone()


def test_every_page_when_asked(setup):
    con, store, lib, doc_id, seen = setup
    vision.make_vision(store)(con, {"document_id": doc_id, "payload": {"all": True}})
    assert len(seen) == 2


def test_off_or_without_a_model_nothing_happens(setup):
    con, store, lib, doc_id, seen = setup
    settings.save(con, {"vision_ocr": "off"})
    vision.after_consume(con, doc_id)
    settings.save(con, {"vision_ocr": "always", "vision_model": ""})
    vision.after_consume(con, doc_id)
    vision.make_vision(store)(con, {"document_id": doc_id, "payload": {"all": True}})
    assert not con.execute("SELECT 1 FROM jobs WHERE kind = 'vision'").fetchone() and not seen
    with pytest.raises(ValueError):
        settings.save(con, {"vision_ocr": "sometimes"})


def test_a_reply_with_less_text_is_not_used(setup, monkeypatch):
    con, store, lib, doc_id, seen = setup
    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json={"message": {"content": ""}})))
    vision.make_vision(store)(con, {"document_id": doc_id, "payload": {"all": True}})
    assert lib.get(doc_id)["content"] == f"{GOOD}\fscr1bbl3s" and not lib.get(doc_id)["error"]


def test_button(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        con = db.connect(tmp_path / "paperjay.db")
        doc_id = Library(con).add(title="x", checksum="x" * 8, original_name="x.pdf", original_ext=".pdf",
                                  mime_type="application/pdf", size=1, status="ready")
        con.close()
        assert c.post(f"/api/documents/{doc_id}/vision").status_code == 400      # no vision model yet
        c.put("/api/settings", json={"ai_provider": "ollama", "vision_model": "minicpm-v"})
        assert c.post(f"/api/documents/{doc_id}/vision").json() == {"queued": True}
        con = db.connect(tmp_path / "paperjay.db")
        assert json.loads(con.execute("SELECT payload FROM jobs WHERE kind = 'vision'").fetchone()[0]) == {"all": True}
