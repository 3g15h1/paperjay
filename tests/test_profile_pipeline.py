"""The profile pipeline (spec A.4): rules file documents with the AI off, the profile job is
queued for every consumed document, and Ask answers fact questions from rules cards alone —
including the dentist case (E1): the word 'dentist' is not in the document's text."""
import pytest

from paperjay import ask, ask_api, jobs, profile
from paperjay.ask import Asker

DENTAL = ("Bright Smiles Dental Care\n123 Main Street\n"
          "Statement date: March 3, 2026\nPatient: Alex\n"
          "Cleaning and exam\nAmount due: $85.00\nPlease pay within 30 days.\n")
BILL = ("Acme Energy\nStatement date: March 1, 2026\nAccount number: 1234 5678\n"
        "Electric service for February. Usage: 812 kWh. Meter reading 45210.\n"
        "Amount due: $142.37\nDue date: March 21, 2026\n")


def test_refile_suggests_a_new_known_company_with_ai_off(lib, add_doc):
    """Owner decision Q2: with the AI off, PaperJay suggests a company it doesn't have yet."""
    doc_id = add_doc(name="scan.pdf", title="scan", content=BILL, status="ready")
    profile.refile(lib.con, doc_id)
    doc = lib.get(doc_id)
    assert doc["correspondent"] is None
    assert doc["suggestions"]["correspondent"] == "Acme Energy"
    assert not lib.con.execute("SELECT 1 FROM correspondents WHERE name = 'Acme Energy'").fetchone()


def test_refile_files_a_document_with_ai_off(lib, add_doc):
    lib.create_label("correspondent", "Acme Energy")  # the household already has the company
    doc_id = add_doc(name="scan.pdf", title="scan", content=BILL, status="ready")
    changes = profile.refile(lib.con, doc_id)
    doc = lib.get(doc_id)
    assert doc["correspondent"] == "Acme Energy"      # the household's company, filed by the rules
    assert doc["document_type"] == "Bill"
    assert doc["doc_date"] == "2026-03-01"
    assert doc["category"] == "utilities"
    assert doc["series_key"] == "acct:12345678"
    topics = {r[0] for r in lib.con.execute("SELECT topic FROM document_topics WHERE document_id = ?", (doc_id,))}
    assert topics  # at least one topic recorded


def test_refile_respects_person_fields(lib, add_doc):
    doc_id = add_doc(name="scan.pdf", title="My own title", content=BILL, given=["title"], status="ready")
    profile.refile(lib.con, doc_id)
    assert lib.get(doc_id)["title"] == "My own title"


def test_after_consume_queues_profile_even_with_ai_off(con, lib, add_doc):
    doc_id = add_doc(content=BILL)
    ask_api.after_consume(con, doc_id)
    kinds = [r[0] for r in con.execute("SELECT kind FROM jobs WHERE document_id = ?", (doc_id,))]
    assert kinds == ["profile"]  # no analyze/embed jobs: the AI is off in the test config


def _ask(con, question):
    index = ask.Index()
    index.refresh(con)
    asker = Asker(con, index=index)
    return asker.prepare(question, None, None, None, False)


def test_ask_finds_the_dentist_by_topic_and_total(con, lib, add_doc):
    dental = add_doc(name="scan.pdf", title="scan", content=DENTAL, status="ready")
    add_doc(name="bill.pdf", title="Energy bill", content=BILL, status="ready")
    profile.refile(con, dental)
    r, mode, _ = _ask(con, "What did the dentist charge?")
    assert r["sources"], "no sources at all"
    # E1 fixed: the dental document — whose text never says 'dentist' — is the top source,
    # found through its topics, and its passage carries the amount
    assert r["sources"][0]["id"] == dental, r["sources"]
    assert "85" in r["sources"][0]["snippet"]
    r2, mode2, _ = _ask(con, "How much did the dentist charge?")
    assert mode2 == "facts" and "85" in r2["extractive"]["text"], (mode2, r2.get("extractive"))


def test_ask_totals_work_from_rules_cards(con, lib, add_doc):
    add_doc(name="bill.pdf", title="Energy bill", content=BILL, status="ready")
    for r in con.execute("SELECT id FROM documents").fetchall():
        profile.refile(con, r[0])
    r, mode, _ = _ask(con, "How much did I spend on utilities in March 2026?")
    assert mode == "total" and "142.37" in str(r.get("total_answer") or r), (mode, str(r)[:300])


def test_profile_job_handler(lib, add_doc):
    lib.create_label("correspondent", "Acme Energy")
    doc_id = add_doc(name="scan.pdf", title="scan", content=BILL, status="ready")
    profile.make_profile()(lib.con, {"document_id": doc_id})
    assert lib.get(doc_id)["correspondent"] == "Acme Energy"
