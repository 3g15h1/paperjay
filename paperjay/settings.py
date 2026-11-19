"""Settings people change in the app, kept in the settings table as JSON.

Each has a default; for headless installs the environment variable named in ENV overrides the default
(but not a value saved in the app). Values are validated on save.
"""
import json
import os
import re
import sqlite3

from .db import get_setting, set_setting, transaction
from .keep_rules import DEFAULT_RULES as DEFAULT_KEEP_RULES

DEFAULTS = {
    # Reading documents
    "ocr_language": "eng",         # Tesseract languages, e.g. "deu+eng"; only installed ones can be chosen
    "date_order": "MDY",           # how to read 03/04/2025: MDY or DMY
    # AI. provider: off | ollama | openai (any OpenAI-compatible server: LM Studio, llama.cpp, vLLM, a cloud API)
    "ai_provider": "off",
    "ai_url": "http://localhost:11434",
    "ai_model": "qwen2.5:3b-instruct",
    "ai_api_key": "",
    "index_secret": "",            # keys the A–Z index term keys (generated once, never shown)
    "ai_threads": 0,               # Ollama num_thread; 0 = let Ollama decide
    "ai_keep_alive": "24h",        # how long Ollama keeps the model loaded
    "embed_model": "nomic-embed-text",  # search by meaning with embed_source "ai": the AI program's embedding model
    "embed_source": "builtin",     # search by meaning: builtin (a small model inside PaperJay) | ai (embed_model) | off
    "ai_no_think": False,
    "vision_ocr": "off",           # 0.8.1: read hard pages with a vision model: off | weak (pages OCR read badly) | always
    "vision_model": "",            # a model that sees images, on the same AI server (qwen2.5vl:7b, minicpm-v…)          # ask reasoning models (Qwen3, ...) to answer without a thinking step: much faster
    # What the AI may do on its own
    "ai_filing": True,             # set title, date, company, type and existing tags on new documents
    "ai_new_correspondents": True,  # add companies it finds in a document's text
    # Answers
    "ask_suggestions": [],         # example questions shown on the start page
    "ask_synonyms": "",            # "electric=acme,acmeenergy; internet=fastnet"
    # Coming up
    "reminder_days": 60,
    "alert_days": [14, 3, 1, 0],
    "done_tags": ["paid"],         # bills with one of these tags are left out of "coming up"
    # Filing without the AI (spec section 8): the rules layer and the merged profile
    "rules_filing": True,          # file from text patterns even when the AI is off
    "rules_new_correspondents": False,  # may a header-only company name create a company?
    "trust_imported_filing": False,     # imported filing counts as a person's own (library layer)
    "import_ai_tags": ["AI-filed", "ai-processed", "paperless-gpt"],  # tags an import's AI added
    "custom_topics": [],           # the household's own topics: [{name, strong, weak, words}]
    "household_people": [],        # the household (spec C.6): [{names, words, user_ids}] — names are
    # the names their papers use (a person can have several); words are what others call them;
    # user_ids links a person to a PaperJay account ("my" = them)
    "duplicate_detection": "review",
    "barcodes": True,        # 0.7.9: split scanned stacks at separator sheets, read paper numbers from labels
    "paper_numbers": False,  # Phase D: number the paper originals — offered at upload/scan, found by #n, printed as labels  # near-duplicates flag for review; the B.3 gate passed on the
    # 0.3.1 acceptance evidence (zero false flags on the 36-document snapshot and on the
    # synthetic library, whose two planted copies were both caught). "log" only records.
    "ai_batch_hours": "01:00-06:00",  # when bulk AI re-reads may run (B.4), in the server's time zone (TZ)
    # Email
    "mail_time": "02:30",          # when mailboxes are checked each night (server time, HH:MM); "" = never
    # How long to keep the paper (keeping.py): first matching rule wins; nothing matched = keep for good
    "keep_rules": DEFAULT_KEEP_RULES,
    # Tax pack
    "tax_year_start": "01-01",     # month-day the tax year starts: 01-01, 04-06 (UK), 07-01 (Australia)…
    # Updates
    "update_check": True,          # ask GitHub every few hours whether a newer PaperJay is out, and tell admins
}
ENV = {"ocr_language": "OCR_LANGUAGE", "date_order": "DATE_ORDER", "ai_provider": "AI_PROVIDER", "ai_url": "AI_URL", "ai_model": "AI_MODEL", "ai_api_key": "AI_API_KEY",
       "ai_threads": "AI_THREADS", "embed_model": "EMBED_MODEL",
       "embed_source": "EMBED_SOURCE", "update_check": "UPDATE_CHECK",
       "tax_year_start": "TAX_YEAR_START", "vision_ocr": "VISION_OCR", "vision_model": "VISION_MODEL"}
SECRET = {"ai_api_key", "index_secret"}


def _validate(key: str, value):
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}")
    default = DEFAULTS[key]
    if key == "ai_provider" and value not in ("off", "ollama", "openai"):
        raise ValueError("ai_provider must be off, ollama or openai")
    if key == "embed_source" and value not in ("builtin", "ai", "off"):
        raise ValueError("embed_source must be builtin, ai or off")
    if key == "date_order" and value not in ("MDY", "DMY"):
        raise ValueError("date_order must be MDY or DMY")
    if key == "ocr_language":
        from .ocr import tesseract_languages
        wanted, have = [x for x in str(value).split("+") if x], tesseract_languages()
        if not wanted or (have and set(wanted) - have):
            raise ValueError(f"OCR languages must be installed ones: {', '.join(sorted(have - {'osd'})) or 'none found'}")
    if key == "mail_time" and value and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value).strip()):
        raise ValueError("mail_time must be a time like 02:30, or empty")
    if key == "vision_ocr" and value not in ("off", "weak", "always"):
        raise ValueError("vision_ocr must be off, weak or always")
    if key == "tax_year_start":
        from .taxpack import parse_start
        parse_start(value)
    if key == "duplicate_detection" and value not in ("log", "review"):
        raise ValueError("duplicate_detection must be 'log' or 'review'")
    if key == "ai_batch_hours" and not re.fullmatch(
            r"([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d", str(value).strip()):
        raise ValueError("ai_batch_hours must look like 01:00-06:00")
    if key == "custom_topics":
        if not isinstance(value, list) or not all(
                isinstance(v, dict) and isinstance(v.get("name"), str) for v in value):
            raise ValueError("custom_topics must be a list of topics with a name")
        return value
    if key == "keep_rules":
        from .keeping import validate_rules
        return validate_rules(DEFAULT_KEEP_RULES if value is None else value)   # None = back to the defaults
    if key == "household_people":
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise ValueError("household_people must be a list of people")
        # A row with no names — the settings page's Add button creates one — is
        # dropped, not an error: one blank row mustn't fail the whole save
        # (review 0.4.0 #4). A names value of the wrong type still fails below.
        def _blank(v):
            names = v.get("names")
            return names is None or (isinstance(names, list)
                                     and not any(isinstance(n, str) and n.strip()
                                                 for n in names))

        value = [v for v in value if not _blank(v)]
        out = []
        for v in value:
            names = v.get("names") or []
            if not isinstance(names, list) or not names or not all(
                    isinstance(n, str) and n.strip() for n in names):
                raise ValueError("each household person needs at least one name")
            words, user_ids = v.get("words") or [], v.get("user_ids") or []
            if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
                raise ValueError("each household person's words must be a list of names")
            if not isinstance(user_ids, list) or not all(
                    isinstance(u, int) and not isinstance(u, bool) for u in user_ids):
                raise ValueError("user_ids must be a list of account ids")
            out.append({"names": [n.strip() for n in names],
                        "words": [w.strip() for w in words if w.strip()],
                        "user_ids": user_ids})
        return out
    if key == "ai_url" and not str(value).startswith(("http://", "https://")):
        raise ValueError("ai_url must start with http:// or https://")
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
    elif isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a whole number, 0 or more")
    elif isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(v, type(default[0]) if default else str) for v in value):
            raise ValueError(f"{key} must be a list")
    elif not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value.strip().rstrip("/") if key == "ai_url" else value.strip() if isinstance(value, str) else value


def _from_env(key: str):
    raw = os.environ.get(ENV.get(key, ""), "").strip()
    if not raw:
        return None
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    return int(raw) if isinstance(default, int) and not isinstance(default, bool) else raw


def is_stored(con: sqlite3.Connection, key: str) -> bool:
    """True if the value was saved in the app (not a default or an environment variable)."""
    return get_setting(con, f"setting:{key}") is not None


def get(con: sqlite3.Connection, key: str):
    stored = get_setting(con, f"setting:{key}")
    if stored is not None:
        return json.loads(stored)
    env = _from_env(key)
    return env if env is not None else DEFAULTS[key]


def all_settings(con: sqlite3.Connection, hide_secrets: bool = True) -> dict:
    out = {k: get(con, k) for k in DEFAULTS}
    if hide_secrets:
        for k in SECRET:
            out[k] = "••••••" if out[k] else ""
    return out


def save(con: sqlite3.Connection, changes: dict) -> dict:
    """Validate and save several settings at once. A secret sent back as '••••••' is left unchanged."""
    clean = {}
    for k, v in changes.items():
        if k in SECRET and v == "••••••":
            continue
        clean[k] = _validate(k, v)
    with transaction(con):
        for k, v in clean.items():
            set_setting(con, f"setting:{k}", json.dumps(v))
    return all_settings(con)
