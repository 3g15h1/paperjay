"""Rule extractor (spec A.1): filing from the text alone, no AI."""
from paperjay import analyze, reminders, rules

BILL = """ACME ENERGY
Statement date: August 1, 2025
Account number: 1234 5678 9012
Service address: 12 Main Street
Electricity used: 812 kWh
Amount due: $229.92
Payment due by September 19, 2025
"""


def test_shared_patterns_live_in_rules():
    assert analyze.KIND_EVIDENCE is rules.KIND_EVIDENCE
    assert analyze.CATEGORY_EVIDENCE is rules.CATEGORY_EVIDENCE
    assert analyze.kind_supported is rules.kind_supported
    assert analyze.category_supported is rules.category_supported
    assert reminders.PATTERNS is rules.DATE_PATTERNS


def test_vendor_from_existing_correspondent():
    card = rules.extract(BILL, correspondents=["Acme Energy", "Other Co"])
    assert card["vendor"] == "Acme Energy"
    assert card["conf"]["vendor"] == 0.9
    assert card["vendor_may_file"] is True


def test_vendor_header_heuristic_only_suggests():
    card = rules.extract("Riverside Family Clinic, LLC\nPatient: Jane Doe\nAmount due: $40.00\n",
                         correspondents=[])
    assert card["vendor"] == "Riverside Family Clinic, LLC"
    assert card["conf"]["vendor"] == 0.5
    assert card["vendor_may_file"] is False


def test_vendor_from_sender_domain():
    card = rules.extract("Your bill is ready", sender="billing@acme-energy.example",
                         correspondents=["Acme Energy"], sender_map={"acme-energy.example": "Acme Energy"})
    assert card["vendor"] == "Acme Energy"
    assert card["conf"]["vendor"] == 0.85


def test_w2_kind_and_topics():
    card = rules.extract("Form W-2 Wage and Tax Statement\nEmployer: Example Corp\nFederal income tax withheld 1234")
    assert card["kind"] == "tax"
    assert "w-2" in card["topics"]


def test_total_labelled_due_confidence():
    card = rules.extract(BILL)
    assert card["total"] == 229.92
    assert card["conf"]["total"] == 0.9


def test_total_plain_total_line_lower_confidence():
    card = rules.extract("Corner Shop\nReceipt\nItem one\nTotal: $12.50\nThank you")
    assert card["total"] == 12.50
    assert card["conf"]["total"] == 0.7


def test_receipt_takes_last_total_line():
    card = rules.extract("Shop\nSubtotal total: $10.00\nTax total: $0.80\nTotal: $10.80\npaid thank you receipt")
    assert card["total"] == 10.80


def test_dates_labelled_and_doc_date():
    card = rules.extract(BILL)
    by_label = {d["label"]: d["date"] for d in card["dates"]}
    assert by_label["issued"] == "2025-08-01"
    assert by_label["due"] == "2025-09-19"
    assert card["doc_date"] == "2025-08-01"
    assert card["conf"]["dates"] == 0.9


def test_ids_captured_and_dates_phones_rejected():
    text = ("Policy number: ABC-123456\nAccount: 987654\nCall us at 555-123-4567\n"
            "Member since 2020-01-15\nReference: 2025-08-01")
    card = rules.extract(text)
    values = {i["label"]: i["value"] for i in card["ids"]}
    assert values.get("policy") == "ABC-123456"
    assert all("555" not in v for v in values.values())
    assert all("2025-08-01" not in v and "2020-01-15" not in v for v in values.values())


def test_people_only_from_labels():
    card = rules.extract("Patient: Jane Doe\nJane Doe visited the clinic. Insured: John Smith")
    people = {p["name"] for p in card["people"]}
    assert "Jane Doe" in people and "John Smith" in people
    card2 = rules.extract("Jane Doe visited the clinic last week for a checkup")
    assert card2["people"] == []


def test_title_built_only_for_filename_titles():
    card = rules.extract(BILL, title="IMG_2041", correspondents=["Acme Energy"])
    assert card["title"] == "Acme Energy Bill August 2025"
    card2 = rules.extract(BILL, title="My electric bill", correspondents=["Acme Energy"])
    assert card2["title"] == ""


def test_looks_like_filename():
    f = rules.looks_like_filename
    assert f("IMG_4021", "IMG_4021.jpeg")
    assert f("pdf", "pdf.pdf")
    assert f("2104W2", "2104W2.pdf")
    assert f("Scan0042", "Scan0042.pdf")
    assert f("SC-X210N_20190311_093011", "x.pdf")
    assert f("Image", "Image.png")
    assert f("UMM UA MP", "other.pdf")
    assert f("emailreceipt_20241003R2181163377", "x.pdf")
    assert f("POLX-1234567-40-0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0 (1)", "x.pdf")
    assert not f("Store reward certificate", "Store reward certificate .pdf")
    assert not f("Xfinity bill August 2025", "bill.pdf")
    assert not f("Request for Tax Payer Assistance 04-20-2022", "x.pdf")
