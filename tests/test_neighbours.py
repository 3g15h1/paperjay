"""The library layer (spec B.1): neighbours learn only from filing a person stands behind,
and vote by shared ID numbers first, then sender, then text similarity."""
import pytest

from paperjay import ask, neighbours, profile, rules, settings

_counter = [0]


def _doc(lib, add_doc, content, **kw):
    _counter[0] += 1
    return add_doc(data=f"unique bytes {_counter[0]}".encode(), name=f"doc{_counter[0]}.pdf",
                   content=content, status="ready", **kw)


def person_filed(lib, add_doc, content, correspondent=None, type_name=None, tags=()):
    """A document a person filed by hand (audit 'update' entries, never the AI)."""
    doc_id = _doc(lib, add_doc, content)
    fields = {}
    if correspondent:
        fields["correspondent_id"] = lib.get_or_create_label("correspondent", correspondent)[0]
    if type_name:
        fields["document_type_id"] = lib.get_or_create_label("document_type", type_name)[0]
    if tags:
        fields["tags"] = [lib.get_or_create_label("tag", t)[0] for t in tags]
    if fields:
        lib.update(doc_id, **fields)
    return doc_id


def imported(lib, add_doc, content, correspondent=None, type_name=None, tags=()):
    """A document whose filing came with an import (given at add, then an 'import' audit entry)."""
    given = [f for f, v in (("correspondent_id", correspondent), ("document_type_id", type_name),
                            ("tags", tags)) if v]
    doc_id = _doc(lib, add_doc, content, given=given)
    fields = {}
    if correspondent:
        fields["correspondent_id"] = lib.get_or_create_label("correspondent", correspondent)[0]
    if type_name:
        fields["document_type_id"] = lib.get_or_create_label("document_type", type_name)[0]
    if tags:
        fields["tags"] = [lib.get_or_create_label("tag", t)[0] for t in tags]
    if fields:
        lib.update(doc_id, by="system", **fields)
    lib._audit(None, "import", doc_id, {"source": "paperless-ngx"})
    return doc_id


def save_rules_card(con, doc_id, content, title=""):
    card = rules.extract(content, title)
    profile.save_card(con, doc_id, "rules", content, "", rules.RULES_VERSION, card)
    return card


def set_sender(con, doc_id, sender):
    con.execute("INSERT INTO uploads(id, source, filename, outcome, document_id, sender) "
                "VALUES (?, 'email', 'mail.pdf', 'accepted', ?, ?)", (f"up-{doc_id}", doc_id, sender))
    con.commit()


ACME_BILL = ("Acme Energy\nAccount No: 8529 1012 3456\nStatement date: August 1, 2025\n"
             "Amount due $100.00\nDue date: August 21, 2025\nElectric service for your home.")


# ---- trust ----

def test_person_filing_is_trusted(con, lib, add_doc):
    doc_id = person_filed(lib, add_doc, ACME_BILL, correspondent="Acme Energy", type_name="Bill")
    doc = lib.get(doc_id)
    trusted = neighbours.trusted_fields(con, doc)
    assert "correspondent_id" in trusted and "document_type_id" in trusted


def test_ai_filing_is_not_trusted(con, lib, add_doc):
    doc_id = _doc(lib, add_doc, ACME_BILL)
    cid = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    lib.update(doc_id, by="ai", correspondent_id=cid)
    doc = lib.get(doc_id)
    assert doc["ai_fields"] == ["correspondent_id"]
    assert neighbours.trusted_fields(con, doc) == set()


def test_imported_filing_not_trusted_by_default(con, lib, add_doc):
    doc_id = imported(lib, add_doc, ACME_BILL, correspondent="Acme Energy", type_name="Bill")
    assert neighbours.trusted_fields(con, lib.get(doc_id)) == set()
    settings.save(con, {"trust_imported_filing": True})
    trusted = neighbours.trusted_fields(con, lib.get(doc_id))
    assert "correspondent_id" in trusted


def test_imported_doc_person_edit_after_import_is_trusted(con, lib, add_doc):
    doc_id = imported(lib, add_doc, ACME_BILL, correspondent="Wrong Name")
    cid = lib.get_or_create_label("correspondent", "Acme Energy")[0]
    lib.update(doc_id, correspondent_id=cid)  # a person fixed it by hand afterwards
    trusted = neighbours.trusted_fields(con, lib.get(doc_id))
    assert "correspondent_id" in trusted


def test_ai_tagged_import_never_trusted_until_confirmed(con, lib, add_doc):
    settings.save(con, {"trust_imported_filing": True})
    doc_id = imported(lib, add_doc, ACME_BILL, correspondent="Acme Energy", tags=["AI-filed"])
    assert neighbours.trusted_fields(con, lib.get(doc_id)) == set()
    lib.confirm_filing(doc_id)
    assert "correspondent_id" in neighbours.trusted_fields(con, lib.get(doc_id))


def test_person_filed_then_ai_changed_is_not_trusted(con, lib, add_doc):
    doc_id = person_filed(lib, add_doc, ACME_BILL, correspondent="Acme Energy")
    other = lib.get_or_create_label("correspondent", "Other Co")[0]
    lib.update(doc_id, by="ai", correspondent_id=other)
    assert "correspondent_id" not in neighbours.trusted_fields(con, lib.get(doc_id))


# ---- signal 1: shared ID numbers ----

def test_shared_account_number_links(con, lib, add_doc):
    t = person_filed(lib, add_doc, ACME_BILL, correspondent="Acme Energy", type_name="Bill")
    save_rules_card(con, t, ACME_BILL)
    d = _doc(lib, add_doc, ACME_BILL.replace("August", "September").replace("$100.00", "$110.00"))
    save_rules_card(con, d, lib.get(d)["content"])
    card = neighbours.compute(con, d)
    assert card["vendor"] == "Acme Energy" and card["conf"]["vendor"] == 0.95
    assert card["kind"] == "bill"
    assert "account" in card["why"]["vendor"]


def test_invoice_numbers_never_link(con, lib, add_doc):
    text_t = "Some Shop\nInvoice No: 998877\nTotal $5.00\nThank you for your purchase."
    text_d = "Different Place\nInvoice No: 998877\nTotal $9.00\nPlease pay."
    t = person_filed(lib, add_doc, text_t, correspondent="Some Shop", type_name="Receipt")
    save_rules_card(con, t, text_t)
    d = _doc(lib, add_doc, text_d)
    save_rules_card(con, d, text_d)
    card = neighbours.compute(con, d)
    assert card["vendor"] == ""  # no shared-ID link, no index, no sender: no vote at all


def test_shared_id_with_untrusted_filing_does_not_link(con, lib, add_doc):
    t = imported(lib, add_doc, ACME_BILL, correspondent="Acme Energy", type_name="Bill")
    save_rules_card(con, t, ACME_BILL)
    d = _doc(lib, add_doc, ACME_BILL)
    save_rules_card(con, d, ACME_BILL)
    card = neighbours.compute(con, d)
    assert card["vendor"] == ""


# ---- signal 2: sender ----

def test_same_sender_address_votes_but_never_creates(con, lib, add_doc):
    t1 = person_filed(lib, add_doc, ACME_BILL, correspondent="Acme Energy")
    t2 = person_filed(lib, add_doc, ACME_BILL.replace("August", "July"), correspondent="Acme Energy")
    for t in (t1, t2):
        set_sender(con, t, "billing@acme-energy.example")
    d = _doc(lib, add_doc, "A document with no readable company name and no numbers.")
    set_sender(con, d, "billing@acme-energy.example")
    card = neighbours.compute(con, d)
    assert card["vendor"] == "Acme Energy"
    assert card["vendor_may_file"] is False  # a sender alone suggests; it never creates a company


def test_domain_maps_only_with_two_agreeing_trusted_docs(con, lib, add_doc):
    t1 = person_filed(lib, add_doc, ACME_BILL, correspondent="Acme Energy")
    set_sender(con, t1, "one@acme-energy.example")
    d = _doc(lib, add_doc, "No company name here either.")
    set_sender(con, d, "two@acme-energy.example")
    assert neighbours.compute(con, d)["vendor"] == ""  # one doc is not a mapping
    t2 = person_filed(lib, add_doc, ACME_BILL.replace("August", "July"), correspondent="Acme Energy")
    set_sender(con, t2, "three@acme-energy.example")
    card = neighbours.compute(con, d)
    assert card["vendor"] == "Acme Energy" and card["vendor_may_file"] is False


def test_free_mail_domain_never_maps(con, lib, add_doc):
    for i in range(2):
        t = person_filed(lib, add_doc, ACME_BILL, correspondent="Acme Energy")
        set_sender(con, t, f"person{i}@gmail.com")
    d = _doc(lib, add_doc, "Nothing identifying.")
    set_sender(con, d, "someone@gmail.com")
    assert neighbours.compute(con, d)["vendor"] == ""


def test_noreply_domain_never_maps(con, lib, add_doc):
    for i in range(2):
        t = person_filed(lib, add_doc, ACME_BILL, correspondent="Acme Energy")
        set_sender(con, t, "no-reply@acme-energy.example")
    d = _doc(lib, add_doc, "Nothing identifying.")
    set_sender(con, d, "alerts@acme-energy.example")
    assert neighbours.compute(con, d)["vendor"] == ""


# ---- signal 3: text similarity vote ----

SIMILAR = ("Water utility bill for the house on Maple Street. Account summary and charges for "
           "water and sewer service this month. Please pay the balance by the due date shown.")


def _index(con):
    idx = ask.Index()
    idx.refresh(con)
    return idx


def test_similarity_vote_needs_two_neighbours(con, lib, add_doc):
    t1 = person_filed(lib, add_doc, SIMILAR, correspondent="City Water", type_name="Bill")
    d = _doc(lib, add_doc, SIMILAR.replace("Maple", "Oak"))
    card = neighbours.compute(con, d, index=_index(con))
    assert card["vendor"] == ""  # one neighbour is not a vote
    t2 = person_filed(lib, add_doc, SIMILAR.replace("month", "period"), correspondent="City Water",
                      type_name="Bill")
    card = neighbours.compute(con, d, index=_index(con))
    assert card["vendor"] == "City Water" and card["kind"] == "bill"
    assert card["conf"]["vendor"] >= 0.6


def test_tags_need_text_evidence_or_three_same_vendor_neighbours(con, lib, add_doc):
    t1 = person_filed(lib, add_doc, SIMILAR, correspondent="City Water", tags=["Household"])
    t2 = person_filed(lib, add_doc, SIMILAR.replace("month", "period"), correspondent="City Water",
                      tags=["Household"])
    d = _doc(lib, add_doc, SIMILAR.replace("Maple", "Oak"))  # no 'household' word in D
    card = neighbours.compute(con, d, index=_index(con))
    assert "Household" not in card["tags"]  # two neighbours, no text evidence: not applied
    t3 = person_filed(lib, add_doc, SIMILAR.replace("month", "week"), correspondent="City Water",
                      tags=["Household"])
    card = neighbours.compute(con, d, index=_index(con))
    assert "Household" in card["tags"]  # three same-vendor neighbours carry it


def test_tag_with_text_evidence_applies(con, lib, add_doc):
    t1 = person_filed(lib, add_doc, SIMILAR, correspondent="City Water", tags=["Water"])
    t2 = person_filed(lib, add_doc, SIMILAR.replace("month", "period"), correspondent="City Water",
                      tags=["Water"])
    d = _doc(lib, add_doc, SIMILAR.replace("Maple", "Oak"))  # 'water' is all over D's text
    card = neighbours.compute(con, d, index=_index(con))
    assert "Water" in card["tags"]


def test_top_filings_for_hints(con, lib, add_doc):
    person_filed(lib, add_doc, SIMILAR, correspondent="City Water", type_name="Bill")
    person_filed(lib, add_doc, SIMILAR.replace("month", "period"), correspondent="City Water",
                 type_name="Bill")
    d = _doc(lib, add_doc, SIMILAR.replace("Maple", "Oak"))
    top = neighbours.top_filings(con, d, index=_index(con))
    assert top and top[0]["vendor"] == "City Water" and top[0]["count"] == 2


def test_trusted_count_reported(con, lib, add_doc):
    person_filed(lib, add_doc, SIMILAR, correspondent="City Water")
    imported(lib, add_doc, ACME_BILL, correspondent="Acme Energy")
    stats = neighbours.library_stats(con)
    assert stats["trusted"] == 1 and stats["documents"] == 2
