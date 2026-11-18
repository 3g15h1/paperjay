"""Setting up a mailbox the easy way: type your email address, make an app password, pick the senders.

    discover("me@gmail.com")   → the provider, its IMAP server, and the steps to make an app password
                                 (a link straight to the right page). Known providers come from the table
                                 below; other domains are recognised by where their mail goes (MX: Google
                                 Workspace, Microsoft 365, Fastmail, Zoho, iCloud…), then looked up in
                                 Thunderbird's public provider database, then guessed (imap.<domain>).
    find_senders(account)      → who sent emails with PDFs or photos attached lately, most first, so people
                                 tick senders instead of typing addresses.
"""
import email.utils
import logging
import random
import re
import socket
import struct
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from . import mail

log = logging.getLogger("paperjay")

GOOGLE = {"label": "Gmail", "host": "imap.gmail.com", "url": "https://myaccount.google.com/apppasswords", "steps": [
    "Open Google's App passwords page with the button below and sign in if asked.",
    "If it says the setting isn't available, turn on 2-Step Verification first (Google Account → Security), then open the page again.",
    "Type PaperJay as the name and tap Create.",
    "Copy the 16-letter password Google shows and paste it below. Spaces don't matter."]}
PROVIDERS = {
    "google": GOOGLE,
    "icloud": {"label": "iCloud Mail", "host": "imap.mail.me.com", "url": "https://account.apple.com/account/manage", "steps": [
        "Open your Apple Account page with the button below and sign in.",
        "Choose Sign-In and Security → App-Specific Passwords, then the + button.",
        "Type PaperJay as the name and tap Create.",
        "Copy the password Apple shows (like abcd-efgh-ijkl-mnop) and paste it below."]},
    "yahoo": {"label": "Yahoo Mail", "host": "imap.mail.yahoo.com", "url": "https://login.yahoo.com/myaccount/security/app-password", "steps": [
        "Open Yahoo's App passwords page with the button below and sign in.",
        "Type PaperJay as the app name and tap Generate password.",
        "Copy the password and paste it below."]},
    "aol": {"label": "AOL Mail", "host": "imap.aol.com", "url": "https://login.aol.com/myaccount/security/app-password", "steps": [
        "Open AOL's App passwords page with the button below and sign in.",
        "Type PaperJay as the app name and tap Generate password.",
        "Copy the password and paste it below."]},
    "fastmail": {"label": "Fastmail", "host": "imap.fastmail.com", "url": "https://app.fastmail.com/settings/security/apppasswords", "steps": [
        "Open Fastmail's App passwords page with the button below.",
        "Tap New app password, choose access to Mail (IMAP), and name it PaperJay.",
        "Copy the password and paste it below."]},
    "zoho": {"label": "Zoho Mail", "host": "imappro.zoho.com", "url": "https://accounts.zoho.com/home#security/app_password", "steps": [
        "In Zoho Mail, make sure IMAP is on: Settings → Mail Accounts → IMAP Access.",
        "Open your Zoho account's App passwords with the button below and generate one named PaperJay.",
        "Copy the password and paste it below."]},
    "yandex": {"label": "Yandex Mail", "host": "imap.yandex.com", "url": "https://id.yandex.com/security/app-passwords", "steps": [
        "Open Yandex's App passwords page with the button below and choose Mail.",
        "Name it PaperJay, create it, and paste the password below."]},
    "gmx": {"label": "GMX", "host": "imap.gmx.com", "url": "https://www.gmx.com/", "password": "normal", "steps": [
        "Sign in to GMX and turn on email access for other programs: Settings → POP3 & IMAP → allow access.",
        "Then use your normal GMX password below."]},
    "webde": {"label": "WEB.DE", "host": "imap.web.de", "url": "https://web.de/", "password": "normal", "steps": [
        "Sign in to WEB.DE and turn on access for other programs: Settings → POP3/IMAP → allow access.",
        "Then use your normal WEB.DE password below."]},
    "microsoft": {"label": "Outlook / Microsoft 365", "unsupported": True, "steps": [
        "Microsoft no longer lets programs sign in to Outlook.com, Hotmail, Live or Microsoft 365 mailboxes with a password, "
        "and PaperJay can't use Microsoft's own sign-in yet.",
        "What works today: in Outlook, add a rule that forwards emails from your billers to another mailbox you add here "
        "(for example a Gmail address)."]},
    "proton": {"label": "Proton Mail", "unsupported": True, "steps": [
        "Proton Mail is end-to-end encrypted, so other programs can only read it through Proton Mail Bridge (paid plans).",
        "If you run Bridge on a computer at home, choose “Set it up by hand” and use the server details Bridge shows. "
        "Otherwise, forward your bills to another mailbox you add here."]},
}
DOMAINS = {
    "gmail.com": "google", "googlemail.com": "google",
    "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud",
    "yahoo.com": "yahoo", "ymail.com": "yahoo", "rocketmail.com": "yahoo", "yahoo.co.uk": "yahoo", "yahoo.fr": "yahoo",
    "yahoo.de": "yahoo", "yahoo.ca": "yahoo", "yahoo.com.au": "yahoo",
    "aol.com": "aol", "aim.com": "aol", "verizon.net": "aol",
    "fastmail.com": "fastmail", "fastmail.fm": "fastmail",
    "zoho.com": "zoho", "zohomail.com": "zoho",
    "yandex.com": "yandex", "yandex.ru": "yandex",
    "gmx.com": "gmx", "gmx.us": "gmx", "gmx.net": "gmx", "gmx.de": "gmx", "gmx.at": "gmx", "gmx.ch": "gmx", "web.de": "webde",
    "outlook.com": "microsoft", "hotmail.com": "microsoft", "live.com": "microsoft", "msn.com": "microsoft",
    "hotmail.co.uk": "microsoft", "outlook.de": "microsoft", "live.co.uk": "microsoft",
    "proton.me": "proton", "protonmail.com": "proton", "pm.me": "proton",
}
# where a domain's mail goes (its MX record) → provider; for custom domains (you@yourfamily.com)
MX_PROVIDERS = [("google.com", "google"), ("googlemail.com", "google"), ("outlook.com", "microsoft"),
                ("messagingengine.com", "fastmail"), ("zoho.com", "zoho"), ("zoho.eu", "zoho"), ("icloud.com", "icloud"),
                ("yahoodns.net", "yahoo"), ("protonmail.ch", "proton"), ("yandex.net", "yandex")]
GMX_HOSTS = {"gmx.net": "imap.gmx.net", "gmx.de": "imap.gmx.net", "gmx.at": "imap.gmx.net", "gmx.ch": "imap.gmx.net"}
ISPDB = "https://autoconfig.thunderbird.net/v1.1/"
EMAIL = re.compile(r"^[^@\s]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$")


# ---------- MX lookup (a few lines instead of a DNS library) ----------

def _nameservers() -> list[str]:
    try:
        with open("/etc/resolv.conf") as f:
            ns = [ln.split()[1] for ln in f if ln.startswith("nameserver") and len(ln.split()) > 1]
    except OSError:
        ns = []
    return [n for n in ns if ":" not in n] or ["1.1.1.1"]


def _name(msg: bytes, pos: int) -> tuple[str, int]:
    labels, end = [], None
    for _ in range(64):
        n = msg[pos]
        if n & 0xC0 == 0xC0:                       # compressed: a pointer to a name earlier in the message
            end = end or pos + 2
            pos = ((n & 0x3F) << 8) | msg[pos + 1]
            continue
        if n == 0:
            return ".".join(labels), end or pos + 1
        labels.append(msg[pos + 1:pos + 1 + n].decode("ascii", "replace"))
        pos += 1 + n
    raise ValueError("bad DNS name")


def mx(domain: str, timeout: float = 3.0) -> list[str]:
    """The domain's mail servers, best first ([] if none or no answer)."""
    qid = random.randrange(65536)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + b"".join(
        bytes([len(p)]) + p.encode("idna") for p in domain.strip(".").split(".")) + b"\0" + struct.pack(">HH", 15, 1)
    for server in _nameservers()[:2]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(q, (server, 53))
                msg = s.recv(4096)
            rid, flags, qd, an = struct.unpack(">HHHH", msg[:8])
            if rid != qid or flags & 0x000F:
                return []
            pos = 12
            for _ in range(qd):
                pos = _name(msg, pos)[1] + 4
            out = []
            for _ in range(an):
                pos = _name(msg, pos)[1]
                rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", msg[pos:pos + 10])
                pos += 10
                if rtype == 15:
                    pref = struct.unpack(">H", msg[pos:pos + 2])[0]
                    out.append((pref, _name(msg, pos + 2)[0].lower()))
                pos += rdlen
            return [h for _, h in sorted(out)]
        except (OSError, ValueError, struct.error, IndexError):
            continue
    return []


def _base(host: str) -> str:
    parts = host.strip(".").split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "net", "org") \
        else ".".join(parts[-2:])


def ispdb(domain: str, address: str) -> dict | None:
    """Thunderbird's public database of email providers' settings."""
    try:
        r = httpx.get(ISPDB + domain, timeout=8, follow_redirects=True)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
    except (httpx.HTTPError, ET.ParseError):
        return None
    for srv in root.iter("incomingServer"):
        if srv.get("type") != "imap":
            continue
        sock = (srv.findtext("socketType") or "").upper()
        user = (srv.findtext("username") or "%EMAILADDRESS%").replace("%EMAILADDRESS%", address) \
            .replace("%EMAILLOCALPART%", address.split("@")[0]).replace("%EMAILDOMAIN%", domain)
        return {"label": root.findtext(".//displayName") or domain, "host": srv.findtext("hostname"),
                "port": int(srv.findtext("port") or 993), "security": "ssl" if sock == "SSL" else "starttls" if sock == "STARTTLS" else "none",
                "username": user}
    return None


def discover(address: str) -> dict:
    """How to connect to this address's mailbox, and how to make the password for it."""
    address = (address or "").strip()
    m = EMAIL.match(address)
    if not m:
        raise ValueError("type a whole email address, like you@example.com")
    domain = m.group(1).lower()
    out = {"email": address, "username": address, "port": 993, "security": "ssl", "found": True}
    key = DOMAINS.get(domain)
    if not key:
        for host in mx(domain):
            key = next((k for suffix, k in MX_PROVIDERS if host == suffix or host.endswith("." + suffix)), None)
            if key:
                break
            info = ispdb(_base(host), address)            # e.g. mail hosted by a web host or internet provider
            if info:
                return {**out, **info, "provider": "other", "steps": _generic_steps(info["label"])}
            break
    if key:
        p = PROVIDERS[key]
        host = GMX_HOSTS.get(domain, p.get("host", ""))
        if key == "google" and domain not in ("gmail.com", "googlemail.com"):
            p = {**p, "label": "Google Workspace"}
        return {**out, "provider": key, "label": p["label"], "host": host, "url": p.get("url"), "steps": p["steps"],
                "unsupported": p.get("unsupported", False), "password": p.get("password", "app")}
    info = ispdb(domain, address)
    if info:
        return {**out, **info, "provider": "other", "steps": _generic_steps(info["label"])}
    return {**out, "provider": "other", "label": domain, "host": f"imap.{domain}", "found": False,
            "steps": _generic_steps(domain) + ["We couldn't look up this provider's server, so check the server details below "
                                               "(they're on your provider's help pages, usually under “IMAP”)."]}


def _generic_steps(label: str) -> list[str]:
    return [f"Many providers, probably {label} too, want a separate “app password” for programs like PaperJay. "
            "Look for “app passwords” in your email account's security settings and make one named PaperJay.",
            "If your provider has no app passwords, use your normal email password."]


# ---------- which senders send documents ----------

# in an email's structure: a PDF part, a part named *.pdf, or a photo sent as an attachment (not a logo inside the text)
DOC_PART = re.compile(rb'"APPLICATION" "(?:PDF|X-PDF)"|\.PDF(?:"|\s|\))'
                      rb'|"IMAGE" "(?:JPEG|JPG|PNG|HEIC|HEIF|TIFF|WEBP)"[^()]*(?:\([^()]*\))?[^()]*\("ATTACHMENT"', re.IGNORECASE)


def _groups(data) -> list[bytes]:
    """imaplib's FETCH answer as one bytes string per message."""
    out = []
    for item in data or []:
        head = item[0] if isinstance(item, tuple) else (item or b"")
        piece = item[0] + item[1] if isinstance(item, tuple) else head
        if re.match(rb"\d+ \(", head):        # each message's answer starts with "<number> ("
            out.append(piece)
        elif out:
            out[-1] += piece
    return out


def with_documents(imap, uids: list[int]) -> list[int]:
    """The emails among these that have a PDF or a photo attached (judged from their structure, nothing downloaded)."""
    found = []
    for i in range(0, len(uids), 200):
        typ, data = imap.uid("FETCH", ",".join(map(str, uids[i:i + 200])), "(UID BODYSTRUCTURE)")
        if typ != "OK":
            continue
        for g in _groups(data):
            m = re.search(rb"UID (\d+)", g)
            if m and DOC_PART.search(g):
                found.append(int(m.group(1)))
    return found


def find_senders(acc: dict, days: int = 120, limit: int = 1000) -> dict:
    """Sign in and list who sent emails with documents attached in the last `days` days, most first."""
    imap = mail.open_mailbox(acc)
    try:
        mail._select(imap, acc.get("folder") or "INBOX", readonly=True)
        uids = mail._search(imap, {"look_back_days": days, "unread_only": False}, date.today())[-limit:]
        docs = with_documents(imap, uids)
        heads = mail._fetch_headers(imap, docs) if docs else {}
    finally:
        mail._close(imap)
    senders: dict[str, dict] = {}
    for uid in sorted(heads, reverse=True):
        sender, subject = heads[uid]
        name, addr = email.utils.parseaddr(sender)
        addr = addr.lower()
        if not addr:
            continue
        s = senders.setdefault(addr, {"address": addr, "name": name or addr, "count": 0, "subject": subject.strip()[:90]})
        s["count"] += 1
        if name and s["name"] == addr:          # a display name from any of their emails beats the bare address
            s["name"] = name
    return {"ok": True, "emails": len(uids), "with_documents": len(docs), "days": days,
            "senders": sorted(senders.values(), key=lambda s: (-s["count"], s["name"].lower()))}
