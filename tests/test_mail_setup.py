"""Easy mailbox setup: recognising the provider, Thunderbird's database, and finding senders of documents."""
import struct

import pytest

from paperjay import mail, mail_setup


def test_known_providers_need_no_lookups(monkeypatch):
    monkeypatch.setattr(mail_setup, "mx", lambda d: pytest.fail("no DNS for known domains"))
    g = mail_setup.discover("Someone@Gmail.com")
    assert (g["provider"], g["host"], g["port"], g["security"], g["username"]) == ("google", "imap.gmail.com", 993, "ssl", "Someone@Gmail.com")
    assert g["url"].startswith("https://") and g["steps"]
    assert mail_setup.discover("x@gmx.de")["host"] == "imap.gmx.net"
    assert mail_setup.discover("x@gmx.de")["password"] == "normal"
    assert mail_setup.discover("x@hotmail.com")["unsupported"]
    with pytest.raises(ValueError):
        mail_setup.discover("not an address")


def test_custom_domains_by_where_their_mail_goes(monkeypatch):
    monkeypatch.setattr(mail_setup, "mx", lambda d: ["aspmx.l.google.com"])
    d = mail_setup.discover("me@ourfamily.org")
    assert d["provider"] == "google" and d["label"] == "Google Workspace" and d["host"] == "imap.gmail.com"
    monkeypatch.setattr(mail_setup, "mx", lambda d: ["ourfamily-org.mail.protection.outlook.com"])
    assert mail_setup.discover("me@ourfamily.org")["unsupported"]


def test_thunderbird_database_and_guess(monkeypatch):
    xml = """<clientConfig version="1.1"><emailProvider id="isp.example"><domain>isp.example</domain>
      <displayName>Example ISP</displayName>
      <incomingServer type="pop3"><hostname>pop.isp.example</hostname><port>995</port><socketType>SSL</socketType></incomingServer>
      <incomingServer type="imap"><hostname>imap.isp.example</hostname><port>143</port><socketType>STARTTLS</socketType>
        <username>%EMAILLOCALPART%</username></incomingServer></emailProvider></clientConfig>"""

    class R:
        status_code, text = 200, xml
    monkeypatch.setattr(mail_setup, "mx", lambda d: [])
    monkeypatch.setattr(mail_setup.httpx, "get", lambda *a, **k: R())
    d = mail_setup.discover("jo@isp.example")
    assert (d["label"], d["host"], d["port"], d["security"], d["username"], d["found"]) == \
        ("Example ISP", "imap.isp.example", 143, "starttls", "jo", True)

    class Missing:
        status_code, text = 404, ""
    monkeypatch.setattr(mail_setup.httpx, "get", lambda *a, **k: Missing())
    d = mail_setup.discover("jo@small.example")
    assert d["host"] == "imap.small.example" and not d["found"]


def test_mx_answer_parsing(monkeypatch):
    """A real-shaped DNS answer, with a compressed name, read without a DNS library."""
    sent = {}

    class Sock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, t): pass
        def sendto(self, q, addr): sent["q"] = q
        def recv(self, n):
            q = sent["q"]
            qid = q[:2]
            question = q[12:]
            def rr(pref, host):
                labels = b"".join(bytes([len(p)]) + p.encode() for p in host.split(".")) + b"\0"
                data = struct.pack(">H", pref) + labels
                return b"\xc0\x0c" + struct.pack(">HHIH", 15, 1, 300, len(data)) + data
            return qid + struct.pack(">HHHHH", 0x8180, 1, 2, 0, 0) + question + rr(20, "alt1.mx.example") + rr(10, "mx.example")
    monkeypatch.setattr(mail_setup.socket, "socket", Sock)
    monkeypatch.setattr(mail_setup, "_nameservers", lambda: ["192.0.2.53"])
    assert mail_setup.mx("example.org") == ["mx.example", "alt1.mx.example"]


class FakeIMAP:
    """BODYSTRUCTURE answers shaped like Dovecot's and Gmail's."""
    structures = {
        1: b'1 (UID 1 BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 5 1 NIL NIL NIL NIL)("APPLICATION" "PDF" ("NAME" "bill.pdf") NIL NIL "BASE64" 9000 NIL ("ATTACHMENT" ("FILENAME" "bill.pdf")) NIL NIL) "MIXED" ("BOUNDARY" "x") NIL NIL NIL))',
        2: b'2 (UID 2 BODYSTRUCTURE ("TEXT" "HTML" ("CHARSET" "utf-8") NIL NIL "7BIT" 500 10 NIL NIL NIL NIL))',
        3: b'3 (UID 3 BODYSTRUCTURE (("TEXT" "HTML" NIL NIL NIL "7BIT" 5 1 NIL NIL NIL NIL)("IMAGE" "PNG" ("NAME" "logo.png") "<logo>" NIL "BASE64" 900 NIL ("INLINE" NIL) NIL NIL) "RELATED" NIL NIL NIL NIL))',
        4: b'4 (UID 4 BODYSTRUCTURE (("TEXT" "PLAIN" NIL NIL NIL "7BIT" 5 1 NIL NIL NIL NIL)("IMAGE" "JPEG" ("NAME" "receipt.jpg") NIL NIL "BASE64" 90000 NIL ("ATTACHMENT" ("FILENAME" "receipt.jpg")) NIL NIL) "MIXED" NIL NIL NIL NIL))',
        5: (b'5 (UID 5 BODYSTRUCTURE (("TEXT" "PLAIN" NIL NIL NIL "7BIT" 5 1 NIL NIL NIL NIL)("APPLICATION" "OCTET-STREAM" ("NAME" {21}', b'Statement October.pdf'),
    }
    froms = {1: "Acme Energy <bills@acme.com>", 4: "Shop <receipts@shop.example>", 5: "bills@acme.com"}

    def __init__(self):
        self.closed = False


    def select(self, folder, readonly=False):
        return "OK", [b"5"]

    def response(self, code):
        return code, [b"1"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b"1 2 3 4 5"]
        uids = [int(x) for x in args[0].split(",")]
        if "BODYSTRUCTURE" in args[1]:
            out = []
            for u in uids:
                s = self.structures[u]
                out += [s, b' NIL NIL "BASE64" 9000 NIL NIL NIL NIL) "MIXED" NIL NIL NIL NIL))'] if isinstance(s, tuple) else [s]
            return "OK", out
        out = []
        for u in uids:
            hdr = f"From: {self.froms[u]}\r\nSubject: Doc {u}\r\n\r\n".encode()
            out += [(f"{u} (UID {u} BODY[HEADER.FIELDS (FROM SUBJECT)] {{{len(hdr)}}}".encode(), hdr), b")"]
        return "OK", out

    def logout(self):
        self.closed = True


def test_finds_senders_of_documents(monkeypatch):
    imap = FakeIMAP()
    monkeypatch.setattr(mail, "open_mailbox", lambda acc: imap)
    r = mail_setup.find_senders({"folder": "INBOX"})
    assert r["emails"] == 5 and r["with_documents"] == 3       # the pdf, the photo, the pdf with a long name
    assert [(s["address"], s["count"]) for s in r["senders"]] == [("bills@acme.com", 2), ("receipts@shop.example", 1)]
    assert r["senders"][0]["name"] == "Acme Energy" and imap.closed


def test_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from conftest import sign_in_client
    from paperjay.app import create_app
    from paperjay.config import Config
    monkeypatch.setattr(mail, "open_mailbox", lambda acc: FakeIMAP())
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        assert c.post("/api/mail/discover", json={"email": "a@gmail.com"}).json()["host"] == "imap.gmail.com"
        assert c.post("/api/mail/discover", json={"email": "nope"}).status_code == 400
        r = c.post("/api/mail/senders", json={"host": "imap.gmail.com", "username": "a@gmail.com", "password": "x"}).json()
        assert r["ok"] and r["senders"][0]["address"] == "bills@acme.com"

        def refuse(acc):
            raise mail.MailError("the server refused the username or password")
        monkeypatch.setattr(mail, "open_mailbox", refuse)
        r = c.post("/api/mail/senders", json={"host": "imap.gmail.com", "username": "a@gmail.com", "password": "x"}).json()
        assert not r["ok"] and "refused" in r["message"]


def test_dovecot_shaped_structures():
    """Lower case, NIL instead of parameter lists: how Dovecot answers."""
    photo = b'4 (UID 4 BODYSTRUCTURE (("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 4 1 NIL NIL NIL NIL)("image" "jpeg" NIL NIL NIL "base64" 760822 NIL ("attachment" ("filename" "receipt.jpg")) NIL NIL) "mixed" NIL NIL NIL NIL))'
    logo = b'5 (UID 5 BODYSTRUCTURE (("text" "plain" NIL NIL NIL "7bit" 4 1 NIL NIL NIL NIL)(("text" "html" NIL NIL NIL "7bit" 22 1 NIL NIL NIL NIL)("image" "png" NIL "<logo>" NIL "base64" 140 NIL ("inline" NIL) NIL NIL) "related" NIL NIL NIL NIL) "alternative" NIL NIL NIL NIL))'
    assert mail_setup.DOC_PART.search(photo) and not mail_setup.DOC_PART.search(logo)
