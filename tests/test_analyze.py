"""AI filing: grounding (from Paperless Ask's fact cards) and the filing rules."""
import json

import httpx
import pytest

from paperjay import ai, analyze, db, settings
from paperjay.analyze import ground, match_correspondent
from paperjay.library import Library

TEXT = ("Example Mutual Insurance Automobile Insurance Identification Card Policy Number: 1234-5678-9 "
        "Effective Date: 04/22/2026 Expiration Date: Oct 22, 2026 Premium $ 1,234.50 Insured: JOHN DOE")


def raw(**kw):
    base = {"kind": "insurance", "category": "insurance", "vendor": "Example Mutual", "summary": "Card.",
            "total": None, "dates": [], "ids": [], "title": "", "tags": []}
    return {**base, **kw}


# ---- grounding ----

def test_keeps_grounded_values_in_any_written_form():
    c = ground(raw(total="1234.50", dates=[{"label": "expires", "date": "2026-10-22"},
                                           {"label": "effective", "date": "2026-04-22"}],
                   ids=[{"label": "Policy Number", "value": "123456789"}]), TEXT)
    assert c["total"] == 1234.5
    assert c["dates"] == [{"label": "expires", "date": "2026-10-22"}, {"label": "effective", "date": "2026-04-22"}]
    assert c["ids"] == [{"label": "Policy Number", "value": "123456789"}]
    assert c["dropped"] == []


def test_drops_what_the_text_does_not_support():
    c = ground(raw(total="999.99", vendor="Acme Insurance Group",
                   dates=[{"label": "due", "date": "2026-11-01"}, {"label": "due", "date": "not a date"}],
                   ids=[{"label": "member", "value": "JOHN DOE"}, {"label": "x", "value": "12"},
                        {"label": "claim", "value": "777777"}]), TEXT, correspondent="Example Mutual")
    assert c["total"] is None and c["dates"] == [] and c["ids"] == []
    assert c["vendor"] == "Example Mutual"  # fell back to the correspondent
    assert len(c["dropped"]) == 7


def test_null_string_total_unknown_enums_and_duplicates():
    c = ground(raw(total="null", kind="spaceship", category="??"), TEXT)
    assert c["total"] is None and c["kind"] == "other" and c["category"] == "other" and c["dropped"] == []
    c = ground(raw(dates=[{"label": "expires", "date": "2026-10-22"}] * 2,
                   ids=[{"label": "Policy", "value": "123456789"}, {"label": "Policy no", "value": "1234-5678-9"}]), TEXT)
    assert len(c["dates"]) == 1 and len(c["ids"]) == 1


def test_ids_must_be_in_the_text_not_just_the_title():
    c = ground(raw(ids=[{"label": "Policy", "value": "SC-X210N"}, {"label": "request", "value": "(B4410)"}]),
               "Request B4410) received", title="SC-X210N_20190311_093011")
    assert c["ids"] == [{"label": "request", "value": "B4410"}]


def test_only_allowed_tags_and_a_sensible_title():
    c = ground(raw(tags=["car", "Spaceships", "CAR"], title='"Auto insurance card 2026."'), TEXT, tag_names=["Car", "Tax"])
    assert c["tags"] == ["Car"] and c["title"] == "Auto insurance card 2026"
    assert ground(raw(title="12/04"), TEXT)["title"] == ""


# ---- filing ----

@pytest.fixture
def lib(tmp_path):
    db.migrate(tmp_path / "p.db", tmp_path / "b")
    con = db.connect(tmp_path / "p.db")
    yield Library(con)
    con.close()


def add(lib, content, title="IMG 2041", **kw):
    n = lib.con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    return lib.add(title=title, checksum=f"{n + 1:064x}", original_name="IMG_2041.jpg", original_ext=".jpg",
                   mime_type="image/jpeg", size=1, content=content, status="ready", **kw)


BILL = "ACME ENERGY CO.\nStatement date August 14, 2025\nAmount due $142.37\nDue by 09/05/2025\nAccount 4471229018"


def bill_card(**kw):
    c = ground(raw(kind="bill", category="utilities", vendor="Acme Energy Co.", title="Acme Energy bill August 2025",
                   total="$142.37", dates=[{"label": "issued", "date": "2025-08-14"}, {"label": "due", "date": "2025-09-05"}],
                   **kw), BILL, tag_names=["Bills", "Utilities"])
    return c


def test_files_a_new_document(lib):
    d = add(lib, BILL)
    bills = lib.create_label("tag", "Bills")
    util = lib.create_label("tag", "Utilities")
    acme = lib.create_label("correspondent", "Acme Energy")
    card = bill_card(tags=["Bills"])
    changes = analyze.apply(lib.con, lib.get(d), card, lib.labels("tag"), file_it=True, new_correspondents=True)
    doc = lib.get(d)
    assert doc["title"] == "Acme Energy bill August 2025" and doc["doc_date"] == "2025-08-14"
    assert doc["correspondent_id"] == acme                     # "Acme Energy Co." matched the existing one
    assert doc["document_type"] == "Bill"                      # created from the fixed list
    assert {t["id"] for t in doc["tags"]} == {bills, util}     # chosen + the category's tag
    assert set(doc["ai_fields"]) == {"title", "doc_date", "correspondent_id", "document_type_id", "tags"}
    assert not doc["needs_review"] and set(changes) == set(doc["ai_fields"])
    assert lib.history(d)[-2]["action"] == "ai_update"


def test_never_overrules_a_person(lib):
    d = add(lib, BILL, title="My electricity", doc_date="2025-01-01", given=["title", "doc_date"])
    lib.update(d, tags=[lib.create_label("tag", "Keep")])
    analyze.apply(lib.con, lib.get(d), bill_card(), lib.labels("tag"), file_it=True, new_correspondents=True)
    doc = lib.get(d)
    assert doc["title"] == "My electricity" and doc["doc_date"] == "2025-01-01"
    assert [t["name"] for t in doc["tags"]] == ["Keep"]
    assert doc["correspondent"] == "Acme Energy Co."          # not set by a person, so filed


def test_new_company_and_new_tag_need_review(lib):
    d = add(lib, BILL)
    analyze.apply(lib.con, lib.get(d), bill_card(), lib.labels("tag"), file_it=True, new_correspondents=True)
    doc = lib.get(d)
    assert doc["correspondent"] == "Acme Energy Co." and doc["needs_review"]
    assert doc["suggestions"]["new_tags"] == ["Utilities"]
    assert "new company added: Acme Energy Co." in doc["suggestions"]["review"]
    assert doc["tags"] == []                                   # never invents tags on its own


def test_without_new_companies_it_suggests_one(lib):
    d = add(lib, BILL)
    analyze.apply(lib.con, lib.get(d), bill_card(), lib.labels("tag"), file_it=True, new_correspondents=False)
    doc = lib.get(d)
    assert doc["correspondent_id"] is None and doc["suggestions"]["correspondent"] == "Acme Energy Co."
    assert lib.labels("correspondent") == []


def test_filing_off_only_reads_facts(lib):
    d = add(lib, BILL)
    assert analyze.apply(lib.con, lib.get(d), bill_card(), lib.labels("tag"), file_it=False,
                         new_correspondents=True) == {}
    doc = lib.get(d)
    assert doc["title"] == "IMG 2041" and not doc["needs_review"]


def test_little_text_needs_review(lib):
    d = add(lib, "Total 4.00")
    analyze.apply(lib.con, lib.get(d), ground(raw(vendor=""), "Total 4.00"), [], file_it=True, new_correspondents=True)
    assert "very little text was found; check the scan" in lib.get(d)["suggestions"]["review"]


@pytest.mark.parametrize("have,want,same", [
    ("DTE Energy", "DTE Energy Co.", True), ("DTE Energy", "dte energy company", True),
    ("Progressive", "Progressive Insurance", True), ("Riverside Family Clinic", "Riverside Family Clinic, Inc.", True),
    ("Apple", "Applebee's", False), ("Acme", "Northwind", False), ("AT&T", "ATT", True),
])
def test_match_correspondent(lib, have, want, same):
    cid = lib.create_label("correspondent", have)
    assert (match_correspondent(lib.con, want) == cid) is same


# ---- the job, with a fake AI ----

def test_job_files_and_reads_through_the_ai(lib, monkeypatch, tmp_path):
    d = add(lib, BILL)
    lib.create_label("tag", "Utilities")
    settings.save(lib.con, {"ai_provider": "ollama", "ai_url": "http://ollama.test"})
    seen = []

    def fake(request):
        body = json.loads(request.content)
        seen.append(body)
        reply = {"kind": "bill", "category": "utilities", "vendor": "Acme Energy", "title": "Acme Energy bill August 2025",
                 "summary": "Electric bill.", "total": "142.37", "dates": [{"label": "issued", "date": "2025-08-14"}],
                 "ids": [{"label": "account", "value": "4471229018"}], "tags": ["Utilities"]}
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}, "done": True})

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(ai, "PEOPLE_FIRST_SECONDS", 0)
    analyze.make_analyze()(lib.con, {"document_id": d, "payload": {}})
    assert seen[0]["format"]["properties"]["tags"]["items"]["enum"] == ["Utilities"]
    assert "Allowed tags: Utilities" in seen[0]["messages"][1]["content"]
    doc = lib.get(d)
    assert doc["title"] == "Acme Energy bill August 2025" and [t["name"] for t in doc["tags"]] == ["Utilities"]
    assert analyze.get_card(lib.con, d)["ids"] == [{"label": "account", "value": "4471229018"}]
    analyze.make_analyze()(lib.con, {"document_id": d, "payload": {}})
    assert len(seen) == 1  # card is current: nothing to do


def test_job_waits_for_people_and_for_a_down_server(lib, monkeypatch):
    from paperjay import jobs
    d = add(lib, BILL)
    settings.save(lib.con, {"ai_provider": "ollama", "ai_url": "http://ollama.test"})
    ai.note_question()
    with pytest.raises(jobs.Defer):
        analyze.make_analyze()(lib.con, {"document_id": d, "payload": {}})
    monkeypatch.setattr(ai, "PEOPLE_FIRST_SECONDS", 0)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(down))
    with pytest.raises(jobs.Defer) as e:
        analyze.make_analyze()(lib.con, {"document_id": d, "payload": {}})
    assert e.value.seconds == 600


# ---- lessons from the real model on real documents ----

DUE = ("Harbor Card Bank\nPayment Due Notice\nNew Balance $79.00\nPayment due 10/12/2026\nAccount 81234567\n"
       "Detach the payment coupon below")


def test_tags_must_be_supported_by_the_text():
    # every bill got "Coupon" (the payment stub); only tags whose word is in the text, or that name the category
    c = ground(raw(kind="bill", category="finance", tags=["Coupon", "Tax", "Finance"]), DUE,
               tag_names=["Coupon", "Tax", "Finance"])
    assert c["tags"] == ["Coupon", "Finance"] and "tag 'Tax'" in c["dropped"]
    c = ground(raw(kind="bill", category="finance", tags=["Coupon"]), "Harbor Card Bank Payment Due $79.00",
               tag_names=["Coupon"])
    assert c["tags"] == []


def test_account_numbers_are_not_totals():
    assert ground(raw(total="81234567"), DUE)["total"] is None
    assert ground(raw(total="$79"), DUE)["total"] == 79.0
    assert ground(raw(total="79.00"), DUE)["total"] == 79.0


def test_ai_never_sets_paid(lib):
    d = add(lib, DUE)
    paid = lib.create_label("tag", "Paid")
    card = ground(raw(kind="bill", category="finance", vendor="Harbor Card Bank", tags=["Paid"]),
                  DUE + "\nAmount paid last month", tag_names=["Paid"])
    assert card["tags"] == ["Paid"]  # the model said so, and the word is there…
    analyze.apply(lib.con, lib.get(d), card, lib.labels("tag"), file_it=True, new_correspondents=True,
                  done_tags=["paid"])
    assert paid not in [t["id"] for t in lib.get(d)["tags"]]  # …but only a person marks a bill paid


# ---- lessons from the real model on the demo library ----

def test_a_category_guess_needs_backing_from_the_text(lib):
    card_stmt = "Northwind Card Services\nStatement date: March 3, 2026\nMinimum payment $35.00"
    c = ground(raw(kind="statement", category="utilities", vendor="Northwind Card Services", tags=["Utilities"]),
               card_stmt, tag_names=["Utilities"])
    assert c["tags"] == [] and "tag 'Utilities'" in c["dropped"]
    clinic = "Riverside Family Clinic\nPatient receipt\nAmount paid $100.65"
    assert ground(raw(kind="receipt", category="medical", tags=["Medical"]), clinic, tag_names=["Medical"])["tags"] == ["Medical"]
    d = add(lib, card_stmt)
    util = lib.create_label("tag", "Utilities")
    analyze.apply(lib.con, lib.get(d), ground(raw(kind="statement", category="utilities", vendor="Northwind Card Services"),
                                               card_stmt), lib.labels("tag"), file_it=True, new_correspondents=True)
    assert util not in [t["id"] for t in lib.get(d)["tags"]] and "new_tags" not in lib.get(d)["suggestions"]


def test_type_must_fit_the_text(lib):
    receipt = "Gadget Store\nSales receipt\nPhone X Pro $999.00\nTotal $1,099.08"
    d = add(lib, receipt)
    analyze.apply(lib.con, lib.get(d), ground(raw(kind="bill", category="shopping", vendor="Gadget Store"), receipt),
                  [], file_it=True, new_correspondents=True)
    doc = lib.get(d)
    assert doc["document_type_id"] is None and "couldn't tell what kind of document this is" in doc["suggestions"]["review"]
    analyze.apply(lib.con, lib.get(d), ground(raw(kind="receipt", category="shopping", vendor="Gadget Store"), receipt),
                  [], file_it=True, new_correspondents=True)
    assert lib.get(d)["document_type"] == "Receipt"


def test_filing_again_replaces_the_ais_own_tags(lib):
    d = add(lib, BILL)
    bills, util = lib.create_label("tag", "Bills"), lib.create_label("tag", "Utilities")
    analyze.apply(lib.con, lib.get(d), bill_card(tags=["Bills"]), lib.labels("tag"), file_it=True, new_correspondents=True)
    assert {t["id"] for t in lib.get(d)["tags"]} == {bills, util}
    card = bill_card(tags=[])
    card["category"] = "other"
    analyze.apply(lib.con, lib.get(d), card, lib.labels("tag"), file_it=True, new_correspondents=True)
    assert lib.get(d)["tags"] == []                       # the AI's earlier tags are replaced, not kept
    lib.update(d, tags=[bills])                           # a person takes over the tags
    analyze.apply(lib.con, lib.get(d), bill_card(tags=["Utilities"]), lib.labels("tag"), file_it=True, new_correspondents=True)
    assert [t["id"] for t in lib.get(d)["tags"]] == [bills]
