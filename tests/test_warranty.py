"""Warranties (0.7.2): read from the text, a person's own setting, Coming up and the calendar, keeping the
receipt, the Warranties list and Ask."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from paperjay import db, keeping, warranty
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.reminders import ics, upcoming
from conftest import sign_in_client


@pytest.mark.parametrize("text, until", [
    ("1-year limited manufacturer warranty.", "2027-09-05"),
    ("Warranty: 24 months", "2028-09-05"),
    ("warranty period of two years", "2028-09-05"),
    ("2 yr parts & labor warranty", "2028-09-05"),
    ("90 day guarantee", "2026-12-04"),
    ("This guarantee is valid for 5 years from purchase", "2031-09-05"),
    ("Extended protection plan, 3 years. Also 1 year manufacturer warranty", "2029-09-05"),  # longest wins
    ("Warranty valid until 03/14/2027", "2027-03-14"),
    ("Lifetime warranty on frames", warranty.LIFETIME),
    ("Purchased 01/31/2026. 1 month warranty", "2026-10-05"),
])
def test_detect(text, until):
    assert warranty.detect(text, "2026-09-05")["until"] == until


@pytest.mark.parametrize("text", ["Sold as is, no warranty", "Returns accepted within 30 days",
                                  "Register your warranty within 30 days", "warranty claims within 14 days",
                                  "Thank you for shopping", ""])
def test_no_warranty(text):
    assert warranty.detect(text, "2026-09-05") is None


def test_dates_follow_the_household_order_and_month_ends():
    assert warranty.detect("warranty expires on 14/03/2027", None, "DMY")["until"] == "2027-03-14"
    assert warranty.detect("warranty expires on 04/03/2027", None, "DMY")["until"] == "2027-03-04"
    assert warranty.detect("warranty expires on 04/03/2027", None, "MDY")["until"] == "2027-04-03"
    assert warranty.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert warranty.detect("2-year warranty", None) is None      # a length needs a purchase date


def test_person_setting_wins():
    assert warranty.end("2-year warranty", "2026-01-01", None)["source"] == "text"
    assert warranty.end("2-year warranty", "2026-01-01", "none") is None
    assert warranty.end("", "2026-01-01", "2030-01-01") == {"until": "2030-01-01", "phrase": "", "source": "person"}


class D:
    def __init__(self, id, content, created, title="Doc", warranty_until=None):
        self.id, self.content, self.created, self.title = id, content, created, title
        self.correspondent, self.tags, self.warranty_until = "Outlet", [], warranty_until


def test_coming_up_and_calendar():
    today = date(2026, 10, 10)
    docs = [D(1, "2-year limited warranty", "2024-11-01", "Dishwasher receipt"),
            D(2, "1-year warranty", "2026-09-01", "Phone receipt"),            # ends next year
            D(3, "Lifetime warranty", "2026-01-01", "Frames"),
            D(4, "", "2026-01-01", "Kettle", warranty_until="2026-10-20")]
    r = upcoming(docs, {}, today, 60)
    assert [(i["id"], i["label"], i["days_left"]) for i in r["items"]] == [(4, "warranty", 10), (1, "warranty", 22)]
    assert r["items"][1]["text"] == "The warranty on Dishwasher receipt (Outlet) ends in 22 days (Sun Nov 1)"
    assert "SUMMARY:Warranty ends: Dishwasher receipt" in ics(r["items"])


def _client(tmp_path):
    return TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False))


def _add(tmp_path, title, content, day, **kw):
    con = db.connect(tmp_path / "paperjay.db")
    from paperjay.library import Library
    doc_id = Library(con).add(title=title, checksum=title * 4, original_name=f"{title}.pdf", original_ext=".pdf",
                              mime_type="application/pdf", size=1, doc_date=day, status="ready", content=content, **kw)
    con.close()
    return doc_id


def test_api_list_document_and_edit(tmp_path):
    soon = (date.today() - timedelta(days=700)).isoformat()
    with _client(tmp_path) as c:
        sign_in_client(c)
        a = _add(tmp_path, "Dishwasher receipt", "Quiet dishwasher. 2-year limited warranty.", soon)
        b = _add(tmp_path, "Old toaster", "Toaster. 1 year warranty.", "2020-01-01")
        _add(tmp_path, "Groceries", "Milk and bread", soon)
        r = c.get("/api/warranties").json()
        assert [i["id"] for i in r["items"]] == [a, b] and r["running"] == 1
        assert r["items"][0]["phrase"] == "2-year limited warranty" and r["items"][1]["ended"]
        assert any(i["label"] == "warranty" and i["id"] == a for i in c.get("/api/upcoming").json()["items"])

        doc = c.get(f"/api/documents/{a}").json()
        assert doc["warranty"]["source"] == "text"
        assert c.patch(f"/api/documents/{a}", json={"warranty_until": "none"}).status_code == 200
        assert c.get(f"/api/documents/{a}").json()["warranty"] is None
        assert [i["id"] for i in c.get("/api/warranties").json()["items"]] == [b]     # the index saw the change
        assert c.patch(f"/api/documents/{a}", json={"warranty_until": "2031-01-01"}).json()["warranty_until"] == "2031-01-01"
        assert c.patch(f"/api/documents/{a}", json={"warranty_until": "later"}).status_code == 400
        assert c.get(f"/api/documents/{a}").json()["warranty"]["source"] == "person"


def test_receipt_is_kept_until_the_warranty_ends(con, add_doc, lib):
    doc_id = add_doc(name="w.pdf", title="TV receipt", doc_date="2023-05-01", status="ready",
                     content="Television. 5 year warranty.", paper_no=1)
    lib.update(doc_id, by="system", category="shopping")
    k = keeping.for_document(con, doc_id)
    assert k["until"] == "2028-05-01" and k["why"] == "under warranty"     # not 2024-12-31 (shop receipts)
    assert keeping.ready(con, date(2026, 1, 1)) == []
    lib.update(doc_id, warranty_until="none")
    assert keeping.for_document(con, doc_id)["until"] == "2024-12-31"


def test_ask_answers_warranty_questions(tmp_path):
    bought = (date.today() - timedelta(days=700)).isoformat()
    with _client(tmp_path) as c:
        sign_in_client(c)
        _add(tmp_path, "Dishwasher receipt", "Home Appliance Outlet. Quiet dishwasher $649.00. "
                                             "2-year limited warranty on parts and labor.", bought)
        _add(tmp_path, "Water bill", "City Water Department. Amount due $61.20", bought)
        r = c.post("/api/ask", json={"q": "When does the dishwasher warranty end?", "no_llm": True}).json()
        until = warranty.add_months(date.fromisoformat(bought), 24)
        assert r["mode"] == "facts", r
        assert f"{until:%B} {until.day}, {until.year}" in r["answer"] and "Dishwasher receipt" in r["answer"]
