"""Documents from email: PaperJay checks mailboxes (IMAP) and adds the PDFs and photos attached to emails.

Every night at the time set in Settings (mail_time, default 02:30, the server's local time) each enabled
mailbox is checked; "Check now" does the same for one. A check looks at the emails of the last
look_back_days (only unread ones if unread_only), keeps those that pass the sender and subject filters,
and adds their attachments like uploads (duplicates are recognised as usual). Emails already taken are
remembered (mail_seen, by IMAP UID), so each is taken once. Afterwards the email is left alone, marked
read, or moved to another folder.

Attachments that aren't documents are skipped, and so are small inline pictures (logos, signatures).
A mailbox can also add the email itself as a PDF (email_pdf: when it has no document attached, or always),
for bills that are written in the email rather than attached.
Gmail, iCloud, Fastmail, Yahoo and most providers need an app password for IMAP; Outlook.com only
allows OAuth, which PaperJay doesn't do.
"""
import email
import email.policy
import email.utils
import imaplib
import io
import logging
import re
import socket
import sqlite3
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import PurePath

from . import emailpdf, intake, settings
from .db import connect, get_setting, set_setting, transaction

log = logging.getLogger("paperjay")

TIMEOUT = 60
MAX_PER_RUN = 300                 # emails taken in one check; the rest wait for the next one
SMALL_INLINE = 40 * 1024          # inline pictures smaller than this are logos, not documents
DOC_TYPES = ("application/pdf", "image/")
DOC_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp"}
FIELDS = ("name", "host", "port", "security", "username", "password", "folder", "from_filter", "subject_filter",
          "unread_only", "look_back_days", "after", "move_to", "enabled", "email_pdf")
HIDDEN = "••••••"


class MailError(Exception):
    """Something a person can fix: wrong password, unknown folder, server not reachable…"""


@dataclass
class Result:
    ok: bool = True
    emails: int = 0               # emails that passed the filters and were taken
    added: int = 0                # new documents
    duplicates: int = 0
    skipped: int = 0              # attachments that weren't documents
    message: str = ""
    uploads: list = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            return self.message
        if not self.emails:
            return "No new emails with documents"
        parts = [f"{self.added} new document{'s' if self.added != 1 else ''}"]
        if self.duplicates:
            parts.append(f"{self.duplicates} already here")
        return f"{self.emails} email{'s' if self.emails != 1 else ''}: " + ", ".join(parts) + (
            f" ({self.message})" if self.message else "")


# ---------- accounts ----------

def _clean(values: dict, partial: bool = False) -> dict:
    out = {}
    for k in FIELDS:
        if k not in values:
            continue
        v = values[k]
        if k in ("port", "look_back_days"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k.replace('_', ' ')} must be a number")
            if k == "port" and not 1 <= v <= 65535:
                raise ValueError("port must be 1 to 65535")
            if k == "look_back_days" and not 1 <= v <= 3650:
                raise ValueError("look back must be 1 to 3650 days")
        elif k in ("unread_only", "enabled"):
            v = 1 if v in (True, 1, "1", "true", "on") else 0
        else:
            v = str(v or "").strip() if k != "password" else str(v or "")
            if k == "security" and v not in ("ssl", "starttls", "none"):
                raise ValueError("security must be ssl, starttls or none")
            if k == "after" and v not in ("keep", "read", "move"):
                raise ValueError("after must be keep, read or move")
            if k == "email_pdf" and v not in ("never", "no_attachment", "always"):
                raise ValueError("email_pdf must be never, no_attachment or always")
            if k == "host" and not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", v):
                raise ValueError("server must be a host name like imap.example.com")
        out[k] = v
    if not partial:
        for k in ("host", "username", "password"):
            if not out.get(k):
                raise ValueError(f"{'server' if k == 'host' else k} is needed")
        out.setdefault("name", out["username"])
        out["name"] = out["name"] or out["username"]
    if out.get("after") == "move" and not out.get("move_to") and (not partial or "move_to" in out):
        raise ValueError("choose the folder to move emails to")
    return out


def public(row) -> dict:
    d = dict(row)
    d["password"] = HIDDEN if d.get("password") else ""
    d["unread_only"], d["enabled"] = bool(d["unread_only"]), bool(d["enabled"])
    d["last_ok"] = None if d["last_ok"] is None else bool(d["last_ok"])
    return d


def accounts(con: sqlite3.Connection) -> list[dict]:
    return [public(r) for r in con.execute("SELECT * FROM mail_accounts ORDER BY name, id")]


def get(con: sqlite3.Connection, account_id: int) -> dict | None:
    row = con.execute("SELECT * FROM mail_accounts WHERE id = ?", (account_id,)).fetchone()
    return dict(row) if row else None


def create(con: sqlite3.Connection, values: dict, user_id: int | None) -> dict:
    v = _clean(values)
    cols = list(v) + ["user_id"]
    with transaction(con):
        cur = con.execute(f"INSERT INTO mail_accounts({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          [*v.values(), user_id])
    return public(con.execute("SELECT * FROM mail_accounts WHERE id = ?", (cur.lastrowid,)).fetchone())


def update(con: sqlite3.Connection, account_id: int, values: dict) -> dict | None:
    values = {k: v for k, v in values.items() if not (k == "password" and v == HIDDEN)}
    v = _clean(values, partial=True)
    current = get(con, account_id)
    if current is None:
        return None
    merged = {**current, **v}
    _clean({k: merged[k] for k in FIELDS})  # the result as a whole must still make sense
    if v:
        with transaction(con):
            con.execute(f"UPDATE mail_accounts SET {', '.join(f'{k} = ?' for k in v)} WHERE id = ?",
                        [*v.values(), account_id])
    return public(con.execute("SELECT * FROM mail_accounts WHERE id = ?", (account_id,)).fetchone())


def delete(con: sqlite3.Connection, account_id: int) -> bool:
    with transaction(con):
        return con.execute("DELETE FROM mail_accounts WHERE id = ?", (account_id,)).rowcount > 0


# ---------- talking to the mail server ----------

def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_mailbox(acc: dict) -> imaplib.IMAP4:
    """Connect and sign in. Raises MailError with something a person can act on."""
    host, port = acc["host"], int(acc["port"])
    try:
        if acc["security"] == "ssl":
            imap = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        else:
            imap = imaplib.IMAP4(host, port, timeout=TIMEOUT)
            if acc["security"] == "starttls":
                imap.starttls(ssl.create_default_context())
    except ssl.SSLCertVerificationError as e:
        raise MailError(f"{host}'s certificate isn't valid ({e.verify_message})")
    except ssl.SSLError as e:
        raise MailError(f"couldn't start a secure connection to {host}:{port} — is the security setting right? ({e.reason})")
    except (socket.gaierror, ConnectionRefusedError, TimeoutError, socket.timeout, OSError) as e:
        raise MailError(f"couldn't reach {host}:{port} ({getattr(e, 'strerror', None) or e})")
    except imaplib.IMAP4.error as e:
        raise MailError(f"{host} didn't accept a secure connection ({e})")
    try:
        imap.login(acc["username"], acc["password"])
    except imaplib.IMAP4.error as e:
        _close(imap)
        log.info("mail %s: sign-in refused: %s", acc["host"], _text(e))
        raise MailError("the email address or password wasn't accepted. Check the password; most providers need an app password, not your normal one.")
    return imap


def _close(imap) -> None:
    try:
        imap.logout()
    except Exception:
        pass


def _text(e) -> str:
    s = e.args[0] if getattr(e, "args", None) else e
    return (s.decode(errors="replace") if isinstance(s, bytes) else str(s)).strip()


def folders(imap) -> list[str]:
    typ, data = imap.list()
    out = []
    for line in data or []:
        if not line:
            continue
        line = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m or "\\Noselect" in m["flags"]:
            continue
        name = m["name"].strip()
        out.append(name[1:-1].replace('\\"', '"').replace("\\\\", "\\") if name.startswith('"') else name)
    return sorted(out, key=lambda f: (f.upper() != "INBOX", f.lower()))


def _select(imap, folder: str, readonly: bool) -> int:
    typ, data = imap.select(_quote(folder), readonly=readonly)
    if typ != "OK":
        raise MailError(f"there is no folder {folder!r} in this mailbox")
    uv = imap.response("UIDVALIDITY")[1]
    try:
        return int(uv[0]) if uv and uv[0] else 0
    except (TypeError, ValueError):
        return 0


def _search(imap, acc: dict, today: date) -> list[int]:
    since = (today - timedelta(days=int(acc["look_back_days"]))).strftime("%d-%b-%Y")
    criteria = ["SINCE", since] + (["UNSEEN"] if acc["unread_only"] else [])
    typ, data = imap.uid("SEARCH", *criteria)
    if typ != "OK":
        raise MailError(f"the server couldn't search the folder ({_text(data[0] if data else '')})")
    return sorted(int(x) for x in (data[0] or b"").split())


def _words(text: str) -> list[str]:
    return [w.strip().lower() for w in re.split(r"[,;\n]", text or "") if w.strip()]


def wanted(acc: dict, sender: str, subject: str) -> bool:
    """The sender and subject filters. A sender filter matches a whole address or a domain (and its subdomains)."""
    senders = _words(acc["from_filter"])
    if senders:
        addr = (email.utils.parseaddr(sender)[1] or sender).lower()
        domain = addr.rpartition("@")[2]
        if not any(addr == s or domain == s.lstrip("@") or domain.endswith("." + s.lstrip("@")) for s in senders):
            return False
    subjects = _words(acc["subject_filter"])
    return not subjects or any(s in (subject or "").lower() for s in subjects)


def attachments(msg) -> list[tuple[str, bytes]]:
    """The PDFs and photos in an email, as (filename, bytes). Logos and signatures are left out."""
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        name = part.get_filename() or ""
        ext = PurePath(name).suffix.lower()
        if not (ctype.startswith(DOC_TYPES) or ext in DOC_EXTS or (ctype == "application/octet-stream" and ext in DOC_EXTS)):
            continue
        data = part.get_payload(decode=True) or b""
        if not data:
            continue
        inline = (part.get_content_disposition() == "inline" or part["Content-ID"]) and not ext == ".pdf"
        if ctype.startswith("image/") and (inline or not name) and len(data) < SMALL_INLINE:
            continue
        if not name:
            name = "attachment" + (".pdf" if ctype == "application/pdf" else "." + ctype.split("/")[1])
        out.append((name, data))
    return out


def _header(raw: bytes) -> tuple[str, str]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return str(msg.get("From", "")), str(msg.get("Subject", ""))


def _fetch_headers(imap, uids: list[int]) -> dict[int, tuple[str, str]]:
    out = {}
    for i in range(0, len(uids), 100):
        chunk = ",".join(map(str, uids[i:i + 100]))
        typ, data = imap.uid("FETCH", chunk, "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        if typ != "OK":
            raise MailError("the server couldn't read the emails' headers")
        for item in data or []:
            if isinstance(item, tuple):
                m = re.search(rb"UID (\d+)", item[0])
                if m:
                    out[int(m.group(1))] = _header(item[1])
    return out


def _fetch(imap, uid: int):
    typ, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
    for item in data or []:
        if isinstance(item, tuple):
            return email.message_from_bytes(item[1], policy=email.policy.default)
    return None


def _after(imap, acc: dict, uids: list[int]) -> str:
    if not uids or acc["after"] == "keep":
        return ""
    s = ",".join(map(str, uids))
    if acc["after"] == "read":
        typ, _ = imap.uid("STORE", s, "+FLAGS.SILENT", "(\\Seen)")
        return "" if typ == "OK" else "couldn't mark the emails read"
    dest = _quote(acc["move_to"])
    typ, data = imap.capability()  # asked again: many servers only list MOVE once signed in
    if typ == "OK" and b"MOVE" in (data[0] or b"").upper().split():
        typ, data = imap.uid("MOVE", s, dest)
    else:
        typ, data = imap.uid("COPY", s, dest)
        if typ == "OK":
            imap.uid("STORE", s, "+FLAGS.SILENT", "(\\Deleted)")
            imap.expunge()
    return "" if typ == "OK" else f"couldn't move the emails to {acc['move_to']!r}"


# ---------- one check ----------

def test(acc: dict) -> dict:
    """Sign in and look around: the folders, and how many emails the filters would take now."""
    imap = open_mailbox(acc)
    try:
        names = folders(imap)
        _select(imap, acc.get("folder") or "INBOX", readonly=True)
        uids = _search(imap, {**acc, "look_back_days": acc.get("look_back_days") or 30,
                              "unread_only": acc.get("unread_only")}, date.today())
        heads = _fetch_headers(imap, uids[-200:]) if uids else {}
        matching = sum(wanted(acc, f, s) for f, s in heads.values())
        return {"ok": True, "folders": names, "emails": len(uids), "matching": matching,
                "checked": len(heads)}
    finally:
        _close(imap)


def run(con: sqlite3.Connection, store, acc: dict, max_bytes: int, today: date | None = None, opener=None) -> Result:
    """Check one mailbox and add what it finds. The account's last_* columns record the outcome."""
    res = Result()
    try:
        imap = (opener or open_mailbox)(acc)
    except MailError as e:
        res.ok, res.message = False, str(e)
        return _record(con, acc, res)
    try:
        uidvalidity = _select(imap, acc["folder"], readonly=acc["after"] == "keep")
        uids = _search(imap, acc, today or date.today())
        seen = {r[0] for r in con.execute("SELECT uid FROM mail_seen WHERE account_id = ? AND uidvalidity = ?",
                                          (acc["id"], uidvalidity))}
        new = [u for u in uids if u not in seen]
        heads = _fetch_headers(imap, new) if new else {}
        taken = []
        for uid in new:
            if uid not in heads or not wanted(acc, *heads[uid]):
                continue
            if len(taken) >= MAX_PER_RUN:
                res.message = "more emails left for the next check"
                break
            msg = _fetch(imap, uid)
            if msg is None:
                continue
            docs = attachments(msg)
            subject = str(msg.get("Subject", "")).strip()
            mode = acc.get("email_pdf") or "never"
            if mode == "always" or (mode == "no_attachment" and not docs):
                try:   # the email itself, for bills that are in its body (emailpdf.py)
                    docs = [(emailpdf.file_name(msg), emailpdf.render(msg))] + docs
                except Exception:
                    log.exception("mail %s: couldn't make a PDF of the email %r", acc["name"], subject[:80])
            for name, data in docs:
                up = intake.ingest(con, store, io.BytesIO(data), name, source="email", max_bytes=max_bytes,
                                   user_id=acc.get("user_id"),
                                   sender=email.utils.parseaddr(heads[uid][0])[1] or heads[uid][0])
                res.uploads.append(up)
                if up.outcome == "accepted":
                    res.added += 1
                elif up.outcome == "duplicate":
                    res.duplicates += 1
                else:
                    res.skipped += 1
            with transaction(con):
                con.execute("INSERT OR IGNORE INTO mail_seen(account_id, uidvalidity, uid, seen_at) VALUES (?, ?, ?, ?)",
                            (acc["id"], uidvalidity, uid, time.time()))
            if docs:
                res.emails += 1
                taken.append(uid)
                log.info("mail %s: %d attachment(s) from %r", acc["name"], len(docs), subject[:80])
        problem = _after(imap, acc, taken)
        if problem:
            res.message = problem
    except MailError as e:
        res.ok, res.message = False, str(e)
    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as e:
        res.ok, res.message = False, f"the mail server stopped answering ({_text(e)})"
    finally:
        _close(imap)
    return _record(con, acc, res)


def _record(con, acc: dict, res: Result) -> Result:
    with transaction(con):
        con.execute("UPDATE mail_accounts SET last_run_at = ?, last_ok = ?, last_message = ?, last_added = ? WHERE id = ?",
                    (time.time(), int(res.ok), res.summary(), res.added, acc["id"]))
    (log.info if res.ok else log.warning)("mail %s: %s", acc["name"], res.summary())
    return res


# ---------- nightly ----------

def due(now: datetime, at: str, last_day: str | None) -> bool:
    """Is the nightly check due? Once a day, at or after `at` (HH:MM)."""
    h, m = (int(x) for x in at.split(":"))
    return (now.hour, now.minute) >= (h, m) and last_day != now.date().isoformat()


class Poller:
    """Checks every enabled mailbox once a day at mail_time, and runs "Check now" in the background."""

    def __init__(self, db_path, store, max_bytes: int, on_added=None, interval: float = 30):
        self.db_path, self.store, self.max_bytes, self.on_added, self.interval = db_path, store, max_bytes, on_added, interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.running: set[int] = set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="paperjay-mail", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            con = connect(self.db_path)
            try:
                at = settings.get(con, "mail_time")
                if at and due(datetime.now(), at, get_setting(con, "mail:last_day")):
                    with transaction(con):
                        set_setting(con, "mail:last_day", date.today().isoformat())
                    ids = [r[0] for r in con.execute("SELECT id FROM mail_accounts WHERE enabled = 1 ORDER BY id")]
                    for account_id in ids:
                        self.check(account_id, wait=True)
            except Exception:
                log.exception("mail: nightly check failed")
            finally:
                con.close()

    def check(self, account_id: int, wait: bool = False) -> bool:
        """Check one mailbox now. False if it's already being checked."""
        with self._lock:
            if account_id in self.running:
                return False
            self.running.add(account_id)

        def go():
            con = connect(self.db_path)
            try:
                acc = get(con, account_id)
                if acc:
                    res = run(con, self.store, acc, self.max_bytes)
                    if res.added and self.on_added:
                        self.on_added()
            except Exception:
                log.exception("mail: checking mailbox %d failed", account_id)
            finally:
                con.close()
                with self._lock:
                    self.running.discard(account_id)
        if wait:
            go()
        else:
            threading.Thread(target=go, name=f"paperjay-mail-{account_id}", daemon=True).start()
        return True
