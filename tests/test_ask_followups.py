"""Follow-up detection and the combined question (no network)."""
from paperjay.followups import plan


def test_new_questions_stay_new():
    assert plan("When does my car insurance expire?", None, 3) == \
        {"search": "When does my car insurance expire?", "followup": False, "older": False}
    p = plan("How much was the flooring install?", "When does my car insurance expire?", 2)
    assert p["followup"] is False and p["search"] == "How much was the flooring install?"


def test_continuations_and_references_combine():
    prev = "When does my car insurance expire?"
    for q in ["and last year's?", "What about the other car?", "And the policy number?", "how about it?", "expired?"]:
        p = plan(q, prev, 1 if q != "And the policy number?" else 2)
        assert p["followup"] and p["search"] == f"{prev} {q}", q
    assert plan("and last year's?", prev, 0)["older"] is True


def test_a_new_period_replaces_the_old_one():
    p = plan("and in 2024?", "How much did I spend on medical in 2025?", 0)
    assert p["search"] == "How much did I spend on medical in ? and in 2024?"
    p = plan("what about last year?", "What did I spend in March 2026 at Apple?", 0)
    assert p["search"] == "What did I spend in at Apple? what about last year?" and p["older"]


def test_a_followup_with_a_new_subject_keeps_what_was_asked(tmp_path):
    """0.7.4: "How much was the electric bill?" then "and the water bill?" answers the water bill's total
    (the follow-up's own words name the subject, the question it continues says what's wanted)."""
    from fastapi.testclient import TestClient

    from paperjay import db
    from paperjay.app import create_app
    from paperjay.config import Config
    from paperjay.library import Library
    from conftest import sign_in_client
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        con = db.connect(tmp_path / "paperjay.db")
        lib = Library(con)
        from paperjay import profile
        for t, text in (("Electric bill", "Acme Energy electric bill. Amount due $144.37"),
                        ("Water bill", "City Water Department water bill. Amount due $61.20")):
            doc_id = lib.add(title=t, checksum=t * 4, original_name=f"{t}.pdf", original_ext=".pdf",
                    mime_type="application/pdf", size=1, doc_date="2026-11-01", status="ready", content=text)
            profile.refile(con, doc_id)   # the rules card (the worker does this after OCR)
        con.close()
        first = c.post("/api/ask", json={"q": "How much was the electric bill?", "no_llm": True}).json()
        assert "144.37" in first["answer"], first
        r = c.post("/api/ask", json={"q": "and the water bill?", "no_llm": True,
                                     "prev": {"q": "How much was the electric bill?", "a": first["answer"]}}).json()
        assert r["mode"] == "facts" and "61.20" in r["answer"] and "144.37" not in r["answer"], r
