"""A made-up household library (the one Paperless Ask was tested with) and a fake Ollama server."""
import json
from datetime import date, timedelta

import httpx

from paperjay import db, settings
from paperjay.library import Library

SOON = date.today() + timedelta(days=10)
SOON_US = SOON.strftime("%m/%d/%Y")

DOCS = [
    {"id": 1, "title": "Electronics receipt", "created": "2024-10-03", "correspondent": "Gadget Store",
     "type": "Receipt", "tags": ["electronics"],
     "content": "Gadget Store Main Street\nOctober 03, 2024 07:45 PM\nPhone X Pro 256GB\nTotal $ 1,099.00\nVisa ending 0000"},
    {"id": 2, "title": "Auto insurance ID card", "created": "2026-04-22", "correspondent": "Example Mutual",
     "type": "Insurance", "tags": ["car"],
     "content": "Example Mutual Insurance\nAutomobile Insurance Identification Card\nPolicy Number: 123456789\n"
                "Effective Date: 04/22/2026 Expiration Date: 10/22/2026\nVehicle: 2020 Sedan"},
    {"id": 3, "title": "Auto insurance ID card", "created": "2025-04-22", "correspondent": "Example Mutual",
     "type": "Insurance", "tags": ["car"],
     "content": "Example Mutual Insurance\nAutomobile Insurance Identification Card\nPolicy Number: 123456789\n"
                "Effective Date: 04/22/2025 Expiration Date: 10/22/2025\nVehicle: 2020 Sedan"},
    {"id": 4, "title": "Utility statement August", "created": "2025-08-15", "correspondent": "AcmePower",
     "type": "Bill", "tags": [],
     "content": "AcmePower Electric Company\nStatement date August 15, 2025\nAmount due $ 142.37\nDue by 09/05/2025"},
    {"id": 5, "title": "Patient receipt", "created": "2025-03-10", "correspondent": "Riverside Family Clinic",
     "type": None, "tags": [],
     "content": "Riverside Family Clinic\nPatient receipt\nDate of service 03/10/2025\nAmount paid $100.65"},
    {"id": 6, "title": "Clinic statement", "created": "2025-03-20", "correspondent": "Riverside Family Clinic",
     "type": None, "tags": [],
     "content": "Riverside Family Clinic\nStatement\nPayment received 03/12/2025 $100.65 thank you"},
    {"id": 7, "title": "Pharmacy receipt", "created": "2024-06-01", "correspondent": None, "type": None, "tags": [],
     "content": "Corner Pharmacy\nPrescription\n06/01/2024\nTotal $ 50.00"},
    {"id": 8, "title": "Payment due notice", "created": "2026-01-01", "correspondent": None, "type": None, "tags": [],
     "content": f"Northwind Card Services\nMinimum payment $ 250.00\nPayment due date: {SOON_US}\nAccount 4000123412341234"},
    {"id": 9, "title": "Water bill", "created": "2026-01-01", "correspondent": None, "type": None, "tags": ["Paid"],
     "content": f"City Water Department\nAmount due $ 61.20\nDue by {SOON_US}"},
    {"id": 10, "title": "Photo of a medical form", "created": "2025-04-01", "correspondent": None, "type": None,
     "tags": [], "content": ""},  # OCR found no text
    {"id": 11, "title": "Riverside Apartments agreement", "created": "2025-06-01", "correspondent": None,
     "type": None, "tags": [],
     "content": "Riverside Apartments\nResidential Lease Agreement\nTenant agrees to a monthly payment of $ 1,450.00\n"
                "Landlord allows one cat. Term 06/01/2025 to 05/31/2026"},
]


def card(kind, category, vendor, total=None, dates=(), ids=(), summary="A document.", title="", tags=()):
    return {"kind": kind, "category": category, "vendor": vendor, "summary": summary, "total": total, "title": title,
            "tags": list(tags), "dates": [{"label": label, "date": d} for label, d in dates],
            "ids": [{"label": label, "value": v} for label, v in ids]}


# What the fake model "reads" in each document, including mistakes the grounding must catch
CARDS = {
    1: card("receipt", "shopping", "Gadget Store", "$1,099.00", [("issued", "2024-10-03")]),
    2: card("insurance", "insurance", "Example Mutual", "null", [("effective", "2026-04-22"), ("expires", "2026-10-22")],
            [("Policy Number", "123456789"), ("member", "JOHN DOE")]),
    3: card("insurance", "insurance", "Example Mutual", None, [("expires", "2025-10-22")], [("Policy Number", "123456789")]),
    4: card("bill", "utilities", "AcmePower", "142.37", [("issued", "2025-08-15"), ("due", "2025-09-05"), ("due", "2025-09-30")]),
    5: card("receipt", "medical", "Riverside Family Clinic", "$100.65", [("service", "2025-03-10")]),
    6: card("statement", "medical", "Riverside Family Clinic", "$100.65", [("paid", "2025-03-12")]),
    7: card("receipt", "medical", "Corner Pharmacy", "$ 50.00", [("paid", "2024-06-01")]),
    8: card("bill", "finance", "Northwind Card Services", "250.00", [("due", SOON.isoformat())],
            [("account", "4000123412341234")]),
    9: card("bill", "utilities", "Imaginary Vendor", "61.20", [("due", SOON.isoformat())]),
    11: card("contract", "home", "Riverside Apartments", "$1,450.00", [("effective", "2025-06-01"), ("expires", "2026-05-31")]),
}

# A tiny "embedding model": words that mean the same thing share a dimension
CONCEPTS = [{"lease", "rent", "rental", "apartment", "apartments", "landlord", "tenant", "housing"},
            {"insurance", "policy", "coverage", "insurer", "insured"},
            {"medical", "clinic", "patient", "doctor", "pharmacy", "prescription", "health"},
            {"phone", "gadget", "electronics", "device"},
            {"electric", "power", "utility", "water", "energy"}]


def fake_embedding(text):
    words = [w.strip(".,?!$:").lower() for w in text.split()]
    return [float(sum(w in c for w in words)) for c in CONCEPTS] + [0.05]  # never all zero


def load_library(path, ai_on=True, **extra_settings):
    """Create the database with the documents above, labelled, 'ready' as if OCR had run."""
    db.migrate(path, path.parent / "backups")
    con = db.connect(path)
    lib = Library(con)
    for d in DOCS:
        lib.add(title=d["title"], checksum=f"{d['id']:064x}", original_name=f"{d['title']}.pdf", original_ext=".pdf",
                mime_type="application/pdf", size=1, doc_date=d["created"], content=d["content"], status="ready",
                doc_id=d["id"])
        labels = {}
        if d["correspondent"]:
            labels["correspondent_id"] = lib.get_or_create_label("correspondent", d["correspondent"])[0]
        if d["type"]:
            labels["document_type_id"] = lib.get_or_create_label("document_type", d["type"])[0]
        if d["tags"]:
            labels["tags"] = [lib.get_or_create_label("tag", t)[0] for t in d["tags"]]
        if labels:
            lib.update(d["id"], **labels)
    conf = {"ai_provider": "ollama" if ai_on else "off", "ai_url": "http://ollama.test",
            "ai_model": "qwen2.5:3b-instruct", "ask_synonyms": "hydro=acmepower", **extra_settings}
    settings.save(con, conf)
    con.close()


class FakeOllama:
    def __init__(self, answer="Your car insurance expires on October 22, 2026 [1].", up=True):
        self.answer, self.up = answer, up
        self.chats, self.extractions, self.embeds = [], [], []
        self.embed_model_installed = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not self.up:
            raise httpx.ConnectError("ollama down", request=request)
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b-instruct"},
                                                        {"name": "nomic-embed-text:latest"}]})
        if path == "/api/embed":
            body = json.loads(request.content)
            if not self.embed_model_installed:
                return httpx.Response(404, json={"error": f"model \"{body['model']}\" not found, try pulling it first"})
            self.embeds.append(body)
            return httpx.Response(200, json={"embeddings": [fake_embedding(t) for t in body["input"]]})
        if path == "/api/chat":
            body = json.loads(request.content)
            if body.get("format"):
                self.extractions.append(body)
                text = body["messages"][1]["content"]
                doc = next(d for d in DOCS if d["content"] and " ".join(d["content"].split()) in text)
                return httpx.Response(200, json={"message": {"content": json.dumps(CARDS[doc["id"]])}, "done": True})
            self.chats.append(body)
            if not body.get("stream"):
                return httpx.Response(200, json={"message": {"content": "ready"}, "done": True})
            words = self.answer.split(" ")
            lines = [json.dumps({"message": {"content": w + ("" if i == len(words) - 1 else " ")}})
                     for i, w in enumerate(words)]
            lines.append(json.dumps({"done": True, "eval_count": len(words)}))
            return httpx.Response(200, content="\n".join(lines).encode())
        return httpx.Response(404)
