"""0.3.2: what the review of 0.3.0/0.3.1 on a real library found. Made-up names and numbers only."""
import json

from paperjay import analyze, ask_api, profile, rules, settings

# A law firm's fax that mentions a tax agency: the firm is the sender, the agency is only addressed
LAW_FAX = ("BRIGHTWATER, BRIGHTWATER, LOWE\n& PARTNERS, P.C.\n100 Example Road\nSpringfield, ST 00000\n"
           "(555) 010-0000\nTRANSMISSION\nTO: IRS- Springfield Field Office\nRE: Request for assistance\n"
           "DATE: 04/20/2022\nPlease find the attached forms for our client.\n")
# An insurance ID card that mentions a state office in its fine print
ID_CARD = ("Keep these cards handy in your glove compartment.\nThank you for choosing Example Mutual.\n"
           "CERTIFICATE OF NO-FAULT INSURANCE\nPolicy Number: 900000001\nEffective Date: 04/22/2026\n"
           "Expiration Date: 10/22/2026\nInsurer: Example Mutual Insurance Co\n"
           "Carry this card. Show it to the Secretary of State when you register the vehicle.\n")
BILL = ("Acme Energy\nStatement date: March 1, 2026\nAccount number: 1234 5678\n"
        "Electric service for February. Usage: 812 kWh.\nAmount due: $142.37\nDue date: March 21, 2026\n")


def test_mentioned_agency_is_not_the_sender():
    card = rules.extract(LAW_FAX, "fax", "fax.pdf",
                         correspondents=["Brightwater, Brightwater, Lowe & Partners, P.C."])
    assert card["vendor"] == "Brightwater, Brightwater, Lowe & Partners, P.C."
    # without the firm as a company, the agency still never files by itself
    card = rules.extract(LAW_FAX, "fax", "fax.pdf")
    assert norm(card["vendor"]) != "irs"


def test_agency_company_counts_only_in_the_letterhead():
    card = rules.extract(ID_CARD, "card", "card.pdf", correspondents=["Secretary of State"])
    assert card["vendor"] != "Secretary of State"
    letter = "Secretary of State\nVehicle registration renewal\nPlate: ABC123\n"
    assert rules.extract(letter, "x", "x.pdf", correspondents=["Secretary of State"])["vendor"] == "Secretary of State"


def test_current_company_is_kept_when_the_text_names_it():
    card = rules.extract(ID_CARD, "card", "card.pdf", correspondents=["Example Mutual", "Acme Energy"],
                         current="Example Mutual")
    assert card["vendor"] == "Example Mutual" and card["conf"]["vendor"] == 0.9


def test_known_vendor_alone_only_suggests():
    card = rules.extract(BILL, "scan", "scan.pdf")
    assert card["vendor"] == "Acme Energy" and card["vendor_may_file"] is False
    assert card["conf"]["vendor"] < profile.BARS["vendor"]


def norm(s):
    return rules.norm_name(s or "")


def test_rules_alone_never_replace_an_existing_company(lib, add_doc):
    other = lib.create_label("correspondent", "Old Co")
    lib.create_label("correspondent", "Acme Energy")
    d = add_doc(content=BILL, status="ready")
    lib.update(d, by="ai", correspondent_id=other)  # filed earlier by the AI, not a person
    profile.refile(lib.con, d)
    doc = lib.get(d)
    assert doc["correspondent"] == "Old Co"
    assert doc["suggestions"]["correspondent"] == "Acme Energy"
    assert any("text pattern suggests" in r for r in doc["suggestions"]["review"])


def test_ai_values_are_recorded_as_the_ais(lib, add_doc):
    lib.create_label("correspondent", "Acme Energy")
    d = add_doc(name="scan.pdf", title="scan", content=BILL, status="ready")
    card = analyze.ground({"kind": "bill", "category": "utilities", "vendor": "Acme Energy",
                           "title": "Acme Energy bill March 2026", "summary": "", "total": "142.37",
                           "dates": [], "ids": [], "tags": [], "topics": [], "people": []}, BILL, "scan", [])
    analyze.save_card(lib.con, d, BILL, "m", card)
    profile.refile(lib.con, d)
    doc = lib.get(d)
    assert doc["correspondent"] == "Acme Energy"
    assert "correspondent_id" in doc["ai_fields"]  # rules + AI agreed: the AI's value is part of it
    assert doc["title"] == "Acme Energy bill March 2026" and "title" in doc["ai_fields"]


def test_ai_filing_off_keeps_ai_only_values_out(lib, add_doc):
    settings.save(lib.con, {"ai_filing": False})
    d = add_doc(name="scan.pdf", title="scan", content="Some letter without much in it.\n", status="ready")
    card = analyze.ground({"kind": "other", "category": "other", "vendor": "", "summary": "",
                           "title": "A letter about nothing much", "total": None, "dates": [], "ids": [],
                           "tags": [], "topics": [], "people": []}, "Some letter without much in it.", "scan", [])
    analyze.save_card(lib.con, d, "Some letter without much in it.\n", "m", card)
    profile.refile(lib.con, d)
    assert lib.get(d)["title"] == "scan"  # the AI's title alone may not file with ai_filing off


def test_reviewed_holds_until_a_new_reason(con, lib, add_doc):
    other = lib.create_label("correspondent", "Old Co")
    lib.create_label("correspondent", "Acme Energy")
    d = add_doc(content=BILL, status="ready")
    lib.update(d, by="ai", correspondent_id=other)
    profile.refile(con, d)
    assert lib.get(d)["needs_review"]
    ask_api.mark_reviewed(con, None, d)
    profile.refile(con, d)
    doc = lib.get(d)
    assert not doc["needs_review"], doc["suggestions"]
    # a reason nobody has seen flags it again
    lib.update(d, by="ai", correspondent_id=lib.create_label("correspondent", "Third Co"))
    profile.refile(con, d)
    assert lib.get(d)["needs_review"]


def test_review_cleared_before_032_is_read_back_from_the_audit_log(con, lib, add_doc):
    other = lib.create_label("correspondent", "Old Co")
    lib.create_label("correspondent", "Acme Energy")
    d = add_doc(content=BILL, status="ready")
    lib.update(d, by="ai", correspondent_id=other)
    profile.refile(con, d)
    doc = lib.get(d)
    # the 0.3.1 way of clearing: review emptied, no "reviewed" list kept
    lib.update(d, user_id=None, needs_review=False, suggestions={**doc["suggestions"], "review": []})
    sug = lib.get(d)["suggestions"]
    sug.pop("reviewed", None)
    lib.update(d, by="system", suggestions=sug)
    profile.refile(con, d)
    assert not lib.get(d)["needs_review"]


def test_mentioned_names_are_never_taught_as_aliases(lib, add_doc):
    lib.create_label("correspondent", "Example Mutual")
    d = add_doc(content=ID_CARD, status="ready")
    profile.save_card(lib.con, d, "rules", ID_CARD, "", 1, {"vendor": "Secretary of State"})
    lib.update(d, correspondent_id=lib.find_label("correspondent", "Example Mutual"))
    assert lib.aliases() == {}


def test_series_key_keeps_the_document_kind():
    assert profile.series_key([{"label": "invoice number", "value": "40917"}], "Acme", "bill", 5) == "vendor:5:bill"
    assert profile.series_key([], "Acme", "", 5) == "vendor:5:other"


def test_file_name_titles_never_win_the_merge():
    p = profile.merge({"ai": {"title": "Receipt48213377", "vendor": "Hotel"},
                       "rules": {"title": "Hotel Receipt April 2024", "conf": {"title": 0.6}}},
                      original_name="receipt48213377.pdf")
    assert p["title"] == "Hotel Receipt April 2024"
    assert rules.looks_like_filename("ABCInsuranceIDCardAuto")
    assert not rules.looks_like_filename("Treadmill Invoice")


def test_reread_jobs_record_the_version(con, lib, add_doc):
    d = add_doc(content=BILL, status="ready")
    analyze.save_card(con, d, BILL, "m", {"vendor": "", "kind": ""})
    v = con.execute("SELECT version FROM facts WHERE document_id = ? AND source = 'ai'", (d,)).fetchone()[0]
    assert v == analyze.EXTRACTOR_VERSION
    assert not analyze.needs_reread({"vendor": "", "kind": ""}, v)
    assert json.loads(con.execute("SELECT card FROM facts WHERE document_id = ?", (d,)).fetchone()[0])["vendor"] == ""
