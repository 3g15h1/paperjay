"""The merged document profile (spec section 3) and the 0007 schema."""
import json

import pytest

from paperjay import db, profile


def rules_card(**kw):
    base = {"kind": "bill", "category": "utilities", "vendor": "Acme Energy", "title": "",
            "summary": "", "total": 100.0, "dates": [{"label": "issued", "date": "2025-08-01"},
                                                     {"label": "due", "date": "2025-08-21"}],
            "doc_date": "2025-08-01", "ids": [{"label": "account", "value": "1234 5678"}],
            "people": [], "topics": ["internet"], "tags": [], "dropped": [],
            "conf": {"vendor": 0.9, "kind": 0.8, "category": 0.7, "total": 0.9, "dates": 0.9},
            "vendor_may_file": True}
    base.update(kw)
    return base


def ai_card(**kw):
    base = {"kind": "bill", "category": "utilities", "vendor": "Acme Energy", "title": "Acme Energy Bill",
            "summary": "Monthly energy bill.", "total": 100.0,
            "dates": [{"label": "issued", "date": "2025-08-01"}, {"label": "due", "date": "2025-08-21"}],
            "ids": [{"label": "account", "value": "1234 5678"}], "tags": [], "dropped": ["total '5'"]}
    base.update(kw)
    return base


def test_migration_0007_schema(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(facts)")}
    assert "source" in cols
    assert db.schema_version(con) >= 7  # 0007's objects survive every later migration
    doc_cols = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
    assert {"category", "series_key", "simhash"} <= doc_cols
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"document_topics", "correspondent_aliases"} <= tables
    up_cols = {r[1] for r in con.execute("PRAGMA table_info(uploads)")}
    assert "sender" in up_cols


def test_old_cards_become_ai_cards(tmp_path):
    # a v6 database upgrades: its facts rows are the AI's cards afterwards
    path = tmp_path / "old.db"
    migs = [m for m in db.available_migrations() if m[0] <= 6]
    db.migrate(path, tmp_path / "b", migrations=migs)
    con = db.connect(path)
    con.execute("INSERT INTO documents(id, title, checksum, original_name, original_ext, mime_type, size, content) "
                "VALUES (1, 't', 'x', 't.pdf', '.pdf', 'application/pdf', 1, 'hello')")
    con.execute("INSERT INTO facts(document_id, content_hash, model, version, card) VALUES (1, 'h', 'm', 3, '{}')")
    con.close()
    db.migrate(path, tmp_path / "b")
    con = db.connect(path)
    row = con.execute("SELECT source, card FROM facts WHERE document_id = 1").fetchone()
    assert row["source"] == "ai" and row["card"] == "{}"
    con.close()


def test_merge_agreeing_sources():
    p = profile.merge({"rules": rules_card(), "ai": ai_card()})
    assert p["vendor"] == "Acme Energy" and p["src"]["vendor"] == "rules+ai"
    assert p["total"] == 100.0
    assert p["title"] == "Acme Energy Bill"  # only the AI has a title
    assert p["series"] == "acct:12345678"
    assert "total '5'" in p["dropped"]


def test_merge_disagreement_tie_goes_to_rules_for_amounts_ai_for_title():
    p = profile.merge({"rules": rules_card(total=100.0, title=""),
                       "ai": ai_card(total=120.0, title="AI Title")})
    # both claim 0.9/0.8 for total: rules card conf total 0.9, AI total conf defaults 0.8
    assert p["total"] == 100.0
    assert not p["review"], "a total disagreement is not a filing question (0.3.2 review noise)"
    p = profile.merge({"rules": rules_card(vendor="Acme Energy"), "ai": ai_card(vendor="Other Co")})
    assert p["review"], "a company disagreement must be flagged for review"


def test_merge_single_low_confidence_becomes_suggestion():
    p = profile.merge({"rules": rules_card(vendor="Weak Co", conf={"vendor": 0.5})})
    assert p["vendor"] == ""
    assert p["suggestions"]["vendor"] == "Weak Co"


def test_merge_library_strong_wins():
    lib = {"vendor": "Filed Name", "conf": {"vendor": 0.95}}
    p = profile.merge({"rules": rules_card(), "ai": ai_card(), "library": lib})
    assert p["vendor"] == "Filed Name" and p["src"]["vendor"] == "library"


def test_merge_person_value_wins():
    p = profile.merge({"rules": rules_card(), "ai": ai_card()},
                      person={"vendor": "My Name For It"})
    assert p["vendor"] == "My Name For It" and p["src"]["vendor"] == "person"


def test_series_fallback_and_prefixes():
    p = profile.merge({"rules": rules_card(ids=[{"label": "policy", "value": "AB-123"}])})
    assert p["series"] == "policy:ab123"
    p2 = profile.merge({"rules": rules_card(ids=[])}, correspondent_id=7)
    assert p2["series"] == "vendor:7:bill"


def test_save_and_load_cards_by_source(con, add_doc):
    doc_id = add_doc(content="hello world this is the document text")
    profile.save_card(con, doc_id, "rules", "hello world this is the document text", "", 1, rules_card())
    profile.save_card(con, doc_id, "ai", "hello world this is the document text", "m", 3, ai_card())
    cards = profile.get_cards(con, doc_id, "hello world this is the document text")
    assert set(cards) == {"rules", "ai"}
    profiles = profile.current_profiles(con)
    assert profiles[doc_id]["vendor"] == "Acme Energy"


def test_single_source_below_bar_is_low_evidence_when_applied():
    # vendor rules conf 0.9 clears the 0.85 single-source bar but stays single-source: low evidence
    p = profile.merge({"rules": rules_card()})
    assert p["vendor"] == "Acme Energy"
    assert "vendor" in p["low_evidence"]


# ---- the library layer in the merge (spec B.1/B.2, Phase B) ----

def test_merge_library_similarity_vote_wins():
    # a passed similarity vote: conf = 0.75 share x 0.8 strength = 0.6, the floor for a library win
    lib = {"vendor": "Filed Name", "kind": "bill", "conf": {"vendor": 0.6, "kind": 0.64}}
    p = profile.merge({"rules": rules_card(), "ai": ai_card(), "library": lib})
    assert p["vendor"] == "Filed Name" and p["src"]["vendor"] == "library"
    assert p["kind"] == "bill" and p["src"]["kind"] == "library"


def test_merge_library_weak_value_does_not_win():
    lib = {"vendor": "Weak Name", "conf": {"vendor": 0.4}}
    p = profile.merge({"rules": rules_card(), "library": lib})
    assert p["vendor"] == "Acme Energy" and p["src"]["vendor"] == "rules"


def test_merge_library_ai_disagreement_flagged():
    lib = {"vendor": "Filed Name", "conf": {"vendor": 0.95}}
    p = profile.merge({"ai": ai_card(), "library": lib})
    assert p["vendor"] == "Filed Name"
    assert any("library filing says" in r for r in p["review"])


def test_refile_files_from_the_library_layer(con, lib, add_doc):
    from paperjay import rules as rules_mod
    text = ("City Water\nAccount No: 4455 6677\nStatement date: August 1, 2025\n"
            "Amount due $40.00\nDue date: August 21, 2025\nWater and sewer service.")
    cid = lib.get_or_create_label("correspondent", "City Water")[0]
    for i, month in enumerate(("June", "July")):
        t = add_doc(data=f"trusted-{i}".encode(), name=f"t{i}.pdf",
                    content=text.replace("August", month), status="ready")
        lib.update(t, correspondent_id=cid)  # filed by a person: trusted
        card = rules_mod.extract(lib.get(t)["content"], "")
        profile.save_card(con, t, "rules", lib.get(t)["content"], "", rules_mod.RULES_VERSION, card)
    d = add_doc(data=b"target", name="target.pdf", content=text, status="ready")
    profile.refile(con, d)
    doc = lib.get(d)
    # 'City Water' is no known vendor and the header alone is only a suggestion (conf 0.5):
    # only the library layer's shared-account vote can have filed it
    assert doc["correspondent"] == "City Water"
    cards = profile.get_cards(con, d)
    assert cards["library"]["vendor"] == "City Water"
    assert cards["library"]["conf"]["vendor"] == 0.95


def test_series_recognises_verbose_id_labels():
    # the AI writes labels like "Policy Number"; the series key must still use the policy
    p = profile.merge({"ai": ai_card(ids=[{"label": "Policy Number", "value": "123456789"},
                                          {"label": "member", "value": "JOHN DOE"}])})
    assert p["series"] == "policy:123456789"
    p2 = profile.merge({"ai": ai_card(ids=[{"label": "Account No.", "value": "4455 6677"}])})
    assert p2["series"] == "acct:44556677"
