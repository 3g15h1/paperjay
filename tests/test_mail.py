"""Documents from email: filters, attachments, remembering what was taken, and what happens to the email after."""
import io
from datetime import date, datetime
from email.message import EmailMessage

import pytest
from PIL import Image

from paperjay import mail


def text_pdf(text: str) -> bytes:
    """A tiny PDF-looking file, different for each text (only its first bytes are checked when it's added)."""
    return f"%PDF-1.4\n% {text}\n%%EOF\n".encode()


def _photo(size=(1200, 1600)) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(size, 60).convert("RGB").save(buf, "JPEG", quality=95)
    return buf.getvalue()


def message(sender="bills@acme.com", subject="Your invoice", attach=(), body="Hello") -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "me@example.com", subject
    m.set_content(body)
    for name, data, ctype in attach:
        main, sub = ctype.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes()


class FakeIMAP:
    """Just enough of imaplib.IMAP4 for mail.run: UIDs → raw emails, flags, folders."""

    def __init__(self, emails: dict[int, bytes], uidvalidity=7, move=True):
        self.emails, self.uidvalidity, self.has_move = dict(emails), uidvalidity, move
        self.flags: dict[int, set] = {u: set() for u in emails}
        self.moved: dict[str, list[int]] = {}
        self.selected = None
        self.logged_out = False

    def select(self, folder, readonly=False):
        self.selected, self.readonly = folder.strip('"'), readonly
        return ("OK", [str(len(self.emails)).encode()]) if self.selected in ("INBOX", "Bills") else ("NO", [b"no such"])

    def response(self, code):
        return code, [str(self.uidvalidity).encode()]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Bills"', b'(\\Noselect) "/" "[Gmail]"']

    def capability(self):
        return "OK", [b"IMAP4rev1 MOVE" if self.has_move else b"IMAP4rev1"]

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        if cmd == "SEARCH":
            unseen = "UNSEEN" in args
            return "OK", [" ".join(str(u) for u in sorted(self.emails)
                                   if not (unseen and "\\Seen" in self.flags[u])).encode()]
        if cmd == "FETCH":
            uids = [int(x) for x in args[0].split(",")]
            out = []
            for u in uids:
                raw = self.emails[u]
                part = raw.split(b"\n\n", 1)[0] if "HEADER" in args[1] else raw
                out += [(f"{u} (UID {u} BODY[] {{{len(part)}}}".encode(), part), b")"]
            return "OK", out
        uids = [int(x) for x in args[0].split(",")]
        if cmd == "STORE":
            for u in uids:
                self.flags[u].add(args[2].strip("()"))
            return "OK", [b""]
        if cmd in ("MOVE", "COPY"):
            self.moved.setdefault(args[1].strip('"'), []).extend(uids)
            if cmd == "MOVE":
                for u in uids:
                    self.emails.pop(u)
            return "OK", [b""]
        raise AssertionError(cmd)

    def expunge(self):
        for u in [u for u, f in self.flags.items() if "\\Deleted" in f]:
            self.emails.pop(u, None)
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


@pytest.fixture
def account(con):
    return lambda **kw: mail.get(con, mail.create(con, {"host": "imap.example.com", "username": "me@example.com",
                                                         "password": "app-password", **kw}, None)["id"])


def _run(con, store, acc, imap):
    return mail.run(con, store, acc, 50 * 1024 * 1024, today=date(2026, 10, 20), opener=lambda a: imap)


def test_takes_pdf_and_photo_attachments(con, store, account):
    pdf = text_pdf("ACME Energy invoice total $12.34")
    imap = FakeIMAP({1: message(attach=[("bill.pdf", pdf, "application/pdf"), ("page.jpg", _photo(), "image/jpeg"),
                                       ("notes.txt", b"hi", "text/plain")])})
    res = _run(con, store, account(), imap)
    assert res.ok and res.emails == 1 and res.added == 2 and res.skipped == 0
    rows = con.execute("SELECT source, filename, outcome FROM uploads ORDER BY filename").fetchall()
    assert [tuple(r) for r in rows] == [("email", "bill.pdf", "accepted"), ("email", "page.jpg", "accepted")]
    assert imap.readonly and imap.logged_out
    assert mail.get(con, 1)["last_message"] == "1 email: 2 new documents"
    assert mail.get(con, 1)["last_added"] == 2 and mail.get(con, 1)["last_ok"] == 1


def test_each_email_is_taken_once(con, store, account):
    acc = account()
    imap = FakeIMAP({1: message(attach=[("a.pdf", text_pdf("one"), "application/pdf")])})
    assert _run(con, store, acc, imap).added == 1
    again = _run(con, store, acc, imap)
    assert again.emails == 0 and again.added == 0
    assert again.summary() == "No new emails with documents"
    # a new mailbox generation (UIDVALIDITY changed): the same file is recognised as a duplicate, not added twice
    imap.uidvalidity = 8
    third = _run(con, store, acc, imap)
    assert third.duplicates == 1 and third.added == 0


def test_filters(con, store, account):
    pdf = lambda t: [("x.pdf", text_pdf(t), "application/pdf")]
    imap = FakeIMAP({1: message("Acme <bills@acme.com>", "Invoice 12", pdf("a")),
                     2: message("news@shop.example", "Invoice sale", pdf("b")),
                     3: message("billing@mail.fastnet.net", "Your statement", pdf("c")),
                     4: message("bills@acme.com", "Newsletter", pdf("d"))})
    res = _run(con, store, account(from_filter="bills@acme.com, fastnet.net", subject_filter="invoice; statement"), imap)
    assert res.emails == 2 and res.added == 2
    # emails that didn't pass aren't remembered: changing the filters later can still take them
    assert {r[0] for r in con.execute("SELECT uid FROM mail_seen")} == {1, 3}


def test_logos_and_inline_pictures_are_skipped():
    m = EmailMessage()
    m["From"], m["Subject"] = "a@b.c", "Hi"
    m.set_content("hi")
    m.add_alternative("<img src='cid:logo'>", subtype="html")
    small = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(small, "PNG")
    m.get_payload()[1].add_related(small.getvalue(), "image", "png", cid="<logo>")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="doc.pdf")
    m.add_attachment(b"PK zip", maintype="application", subtype="zip", filename="x.zip")
    assert [n for n, _ in mail.attachments(m)] == ["doc.pdf"]


def test_mark_read_and_move(con, store, account):
    att = lambda t: [("x.pdf", text_pdf(t), "application/pdf")]
    imap = FakeIMAP({1: message(attach=att("one")), 2: message(attach=[])})
    _run(con, store, account(after="read"), imap)
    assert imap.flags[1] == {"\\Seen"} and imap.flags[2] == set()   # an email without documents is left alone
    assert not imap.readonly

    for has_move in (True, False):
        imap = FakeIMAP({5: message(attach=att(f"two {has_move}"))}, move=has_move)
        _run(con, store, account(after="move", move_to="Bills"), imap)
        assert imap.moved == {"Bills": [5]} and 5 not in imap.emails


def test_unread_only(con, store, account):
    imap = FakeIMAP({1: message(attach=[("x.pdf", text_pdf("old"), "application/pdf")]),
                     2: message(attach=[("y.pdf", text_pdf("new"), "application/pdf")])})
    imap.flags[1].add("\\Seen")
    assert _run(con, store, account(unread_only=True), imap).added == 1


def test_errors_are_recorded(con, store, account):
    acc = account(folder="Nope")
    res = _run(con, store, acc, FakeIMAP({}))
    assert not res.ok and "no folder 'Nope'" in res.message
    assert mail.get(con, acc["id"])["last_ok"] == 0

    def refuse(a):
        raise mail.MailError("the server refused the username or password")
    res = mail.run(con, store, acc, 1000, opener=refuse)
    assert not res.ok and "refused" in mail.get(con, acc["id"])["last_message"]


def test_account_validation_and_hidden_password(con):
    with pytest.raises(ValueError, match="password"):
        mail.create(con, {"host": "imap.x.com", "username": "u"}, None)
    with pytest.raises(ValueError, match="host name"):
        mail.create(con, {"host": "imap x; rm", "username": "u", "password": "p"}, None)
    with pytest.raises(ValueError, match="folder to move"):
        mail.create(con, {"host": "imap.x.com", "username": "u", "password": "p", "after": "move"}, None)
    a = mail.create(con, {"host": "imap.x.com", "username": "u", "password": "secret"}, None)
    assert a["password"] == mail.HIDDEN and a["name"] == "u" and a["port"] == 993
    b = mail.update(con, a["id"], {"password": mail.HIDDEN, "name": "Bills"})
    assert b["name"] == "Bills" and mail.get(con, a["id"])["password"] == "secret"
    assert "secret" not in str(mail.accounts(con))


def test_folders_parsing():
    assert mail.folders(FakeIMAP({})) == ["INBOX", "Bills"]


def test_nightly_due():
    assert mail.due(datetime(2026, 10, 20, 2, 30), "02:30", None)
    assert mail.due(datetime(2026, 10, 20, 9, 0), "02:30", "2026-10-19")      # missed (server was off): catch up
    assert not mail.due(datetime(2026, 10, 20, 2, 29), "02:30", "2026-10-19")
    assert not mail.due(datetime(2026, 10, 20, 3, 0), "02:30", "2026-10-20")  # already ran today


def test_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from paperjay.app import create_app
    from paperjay.config import Config
    from conftest import sign_in_client
    monkeypatch.setattr(mail, "test", lambda acc: {"ok": True, "folders": ["INBOX"], "emails": 0, "matching": 0})
    app = create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)
    with TestClient(app) as client:
        sign_in_client(client, "member", "m")
        assert client.get("/api/mail").status_code == 403
        sign_in_client(client, "admin", "a")
        r = client.post("/api/mail/accounts", json={"host": "imap.example.com", "username": "me", "password": "pw"})
        assert r.status_code == 201, r.text
        acc = r.json()
        assert acc["password"] == mail.HIDDEN
        assert client.get("/api/mail").json()["mail_time"] == "02:30"
        assert client.patch(f"/api/mail/accounts/{acc['id']}", json={"after": "move"}).status_code == 400
        assert client.patch(f"/api/mail/accounts/{acc['id']}", json={"after": "move", "move_to": "Done"}).json()["move_to"] == "Done"
        assert client.put("/api/settings", json={"mail_time": "25:00"}).status_code == 400
        assert client.put("/api/settings", json={"mail_time": "03:15"}).json()["mail_time"] == "03:15"
        assert client.delete(f"/api/mail/accounts/{acc['id']}").status_code == 204
        assert client.get("/api/mail").json()["accounts"] == []


# ---- the email itself as a PDF (0.7.7) ----

def test_email_pdf_when_nothing_is_attached(con, store, account):
    import subprocess
    acc = account(email_pdf="no_attachment")
    imap = FakeIMAP({1: message(subject="Your November bill", body="Amount due $61.20 by 12/13/2026"),
                     2: message(subject="Statement", attach=[("s.pdf", text_pdf("stmt"), "application/pdf")])})
    res = _run(con, store, acc, imap)
    assert res.ok and res.emails == 2 and res.added == 2
    names = sorted(r[0] for r in con.execute("SELECT filename FROM uploads"))
    assert names == ["Your November bill.pdf", "s.pdf"]           # no PDF of the email that had a document
    row = con.execute("SELECT checksum FROM documents WHERE original_name = 'Your November bill.pdf'").fetchone()
    text = subprocess.run(["pdftotext", str(store.path("originals", row[0], ".pdf")), "-"],
                          capture_output=True, text=True).stdout
    assert "Your November bill" in text and "Amount due $61.20" in text and "From: bills@acme.com" in text


def test_email_pdf_always_and_never(con, store, account):
    imap = FakeIMAP({1: message(subject="Invoice", attach=[("i.pdf", text_pdf("inv"), "application/pdf")])})
    assert _run(con, store, account(email_pdf="always"), imap).added == 2
    plain = FakeIMAP({1: message(subject="Only text", body="nothing attached")})
    res = _run(con, store, account(name="other", email_pdf="never"), plain)
    assert res.added == 0 and res.emails == 0
    with pytest.raises(ValueError):
        account(name="bad", email_pdf="sometimes")


def test_html_email_becomes_text():
    from paperjay import emailpdf
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = "shop@example.com", "Order 12", "Fri, 07 Nov 2026 09:12:00 +0000"
    m.set_content("<html><head><style>p{color:red}</style><title>t</title></head><body><p>Hi <b>Alex</b></p>"
                  "<table><tr><td>Total</td><td>$12.00</td></tr></table><script>alert(1)</script>"
                  "<img src='http://tracker.example/p.gif'></body></html>", subtype="html")
    assert emailpdf.body_text(m) == "Hi Alex\n\nTotal $12.00"
    assert emailpdf.render(m).startswith(b"%PDF")
    assert emailpdf.file_name(m) == "Order 12.pdf"
    m2 = EmailMessage()
    m2["Subject"] = 'a/b:c*?"<>|'
    m2.set_content("x")
    assert emailpdf.file_name(m2) == "a b c.pdf"
