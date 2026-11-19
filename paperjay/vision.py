"""Reading hard pages with a vision model: handwriting, faint or crumpled photos, the pages Tesseract struggles
with.

With `vision_ocr` set to "weak" (only pages OCR read badly) or "always", and a `vision_model` that can see
images (for example qwen2.5vl or minicpm-v on Ollama, or any OpenAI-compatible server with image input), each
new document's pages are looked at after OCR. A page counts as weak when it has few real words, or mostly
garbage (letters without vowels, stray symbols). Those pages are sent to the model, one at a time, as an image
with a request to copy the text exactly; its text replaces OCR's for that page. The document then says which
pages the AI read, so names and numbers can be checked. A person can also ask for it on one document
("Read with the AI", every page). The searchable PDF keeps OCR's text layer: marks for search words only land
on words OCR found.

Runs on the AI worker, before AI filing, so filing and answers use the better text.
"""
import base64
import logging
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from . import ai, jobs, settings
from .db import transaction
from .library import Library, NotFound

log = logging.getLogger("paperjay")

VISION_JOB = "vision"
MAX_PAGES = 20          # per document, so one long scan can't hold the AI for hours
WIDTH = 1400            # pixels across the page image sent to the model
PROMPT = ("Copy all the text on this page exactly as it is written, including handwriting, numbers, amounts and "
          "dates. Keep the reading order and the line breaks. Do not describe the page, do not explain, do not "
          "translate and do not add anything that isn't written there. If there is no text, reply with nothing.")
_WORD = re.compile(r"[^\W\d_]{2,}")
_CHATTER = re.compile(r"^\s*(?:sure[,!.]?|here is|here's|the text (?:on|in) (?:this|the) (?:page|image)[^:\n]*:)[^\n]*\n",
                      re.I)


def weak(page_text: str) -> bool:
    """Few real words, or mostly garbage: what Tesseract makes of handwriting and bad photos."""
    words = _WORD.findall(page_text)
    if len(words) < 12:
        return True
    garbage = sum(1 for w in words if not re.search(r"[aeiouyäöüéèàáíóú]", w, re.I) and len(w) > 2)
    return garbage / len(words) > 0.35


def pages_of(content: str) -> list[str]:
    return (content or "").split("\f")


def _render(pdf: Path, n: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "p"
        r = subprocess.run(["pdftoppm", "-f", str(n), "-l", str(n), "-scale-to-x", str(WIDTH), "-scale-to-y", "-1",
                            "-png", "-singlefile", str(pdf), str(prefix)], capture_output=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace")[-200:])
        return prefix.with_suffix(".png").read_bytes()


def message(cfg: ai.Config, png: bytes) -> dict:
    """One user message with the page image, in the provider's own shape."""
    b64 = base64.b64encode(png).decode()
    if cfg.provider == "ollama":
        return {"role": "user", "content": PROMPT, "images": [b64]}
    return {"role": "user", "content": [{"type": "text", "text": PROMPT},
                                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}


def read_page(cfg: ai.Config, png: bytes) -> str:
    text = ai.chat(cfg, [message(cfg, png)], temperature=0.0, max_tokens=1500, timeout=900, ctx=8192)
    text = re.sub(r"^```[a-z]*\n|\n```\s*$", "", str(text).strip())
    return _CHATTER.sub("", text, count=1).strip()


def config(con: sqlite3.Connection) -> ai.Config | None:
    """The AI connection with the vision model in place of the filing model, or None when it's not set up."""
    cfg = ai.Config.load(con)
    model = str(settings.get(con, "vision_model") or "").strip()
    if not cfg.on or not model:
        return None
    return replace(cfg, model=model, no_think=False)


def after_consume(con: sqlite3.Connection, doc_id: int) -> None:
    """Queued when OCR finishes, if the setting asks for it and a page needs it."""
    mode = settings.get(con, "vision_ocr")
    if mode == "off" or config(con) is None:
        return
    row = con.execute("SELECT content FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return
    if mode == "always" or any(weak(p) for p in pages_of(row["content"])):
        with transaction(con):
            jobs.enqueue(con, VISION_JOB, document_id=doc_id, priority=95, payload={"all": mode == "always"})


def make_vision(store) -> "jobs.Handler":
    def vision_job(con: sqlite3.Connection, job: dict) -> None:
        cfg = config(con)
        if cfg is None:
            return
        lib = Library(con)
        try:
            doc = lib.get(job["document_id"])
        except NotFound:
            return
        if doc["deleted_at"] or doc["status"] != "ready":
            return
        if doc["archive_checksum"]:
            pdf = store.path("archive", doc["checksum"], ".pdf")
        elif doc["mime_type"] == "application/pdf":
            pdf = store.path("originals", doc["checksum"], doc["original_ext"])
        else:
            return
        texts = pages_of(doc["content"])
        count = doc["page_count"] or len(texts) or 1
        texts += [""] * (count - len(texts))
        wanted = [n for n in range(1, count + 1) if job["payload"].get("all") or weak(texts[n - 1])][:MAX_PAGES]
        read = []
        for n in wanted:
            text = read_page(cfg, _render(pdf, n))
            if text and len(_WORD.findall(text)) >= len(_WORD.findall(texts[n - 1])) * 0.5:
                texts[n - 1] = text
                read.append(n)
        if not read:
            return
        now = lib.get(doc["id"])
        if now["content"] != doc["content"]:
            return   # read again by OCR meanwhile: this text belongs to the old version
        pages = ", ".join(map(str, read))
        note = (f"Page{'s' if len(read) > 1 else ''} {pages} {'were' if len(read) > 1 else 'was'} read by the AI "
                f"({cfg.model}): check names and numbers.")
        lib.update(doc["id"], by="system", content="\f".join(texts),
                   error="; ".join(filter(None, [doc["error"], note])))
        with transaction(con):
            con.execute("INSERT INTO audit_log(document_id, action, detail) VALUES (?, 'vision', json_object("
                        "'pages', ?, 'model', ?))", (doc["id"], pages, cfg.model))
        from . import ask_api
        ask_api.after_consume(con, doc["id"])   # file it again and redo the AI's facts with the new text
        log.info("vision model read page(s) %s of document %d", pages, doc["id"])
    return vision_job

