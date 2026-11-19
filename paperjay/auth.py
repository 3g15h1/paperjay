"""Accounts, sign-in and permissions.

Household accounts with three roles:
  admin     everything, including people, settings and deleting documents for good
  member    add, file, edit and trash documents; ask questions
  readonly  look, search and ask

Passwords are hashed with scrypt (standard library). A browser signs in once and gets a session cookie
(HttpOnly, SameSite=Lax, 90 days, renewed while in use). Scripts and phone shortcuts use API tokens in an
`Authorization: Token …` (as Paperless-ngx does) or `Bearer …` header. Only hashes of sessions and tokens
are stored. A token can be limited to some of read, create, update and delete (keys for AI assistants, 0.6):
those only narrow what the account's role allows. Too many wrong passwords for one name and address in a row make sign-in wait.
"""
import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

from .db import transaction

ROLES = ("admin", "member", "readonly")
PERMISSIONS = ("read", "create", "update", "delete")
SESSION_DAYS = 90
COOKIE = "pj_session"
MIN_PASSWORD = 8
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}


class AuthError(Exception):
    pass


class TooManyTries(AuthError):
    pass


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    scope: str = "all"   # 'calendar' when signed in with a calendar-only token
    permissions: frozenset | None = None   # a token limited to some of PERMISSIONS; None = all the role allows
    via_token: bool = False

    @property
    def name(self) -> str:
        return self.display_name or self.username

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.name, "role": self.role}


# ---- passwords ----

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b(salt)}${b(dk)}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        want = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(want), maxmem=_SCRYPT["maxmem"])
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def _check_new_password(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"use at least {MIN_PASSWORD} characters")
    if username and password.lower() == username.lower():
        raise AuthError("the password can't be the same as the name")


# ---- people ----

def has_users(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM users WHERE password_hash IS NOT NULL LIMIT 1").fetchone() is not None


def _user(row) -> User:
    return User(row["id"], row["username"], row["display_name"], row["role"])


def create_user(con: sqlite3.Connection, username: str, password: str, role: str = "member",
                display_name: str = "") -> User:
    username = " ".join(str(username).split()).lower()
    if not username or len(username) > 64 or not username.replace(".", "").replace("-", "").replace("_", "").replace(
            "@", "").replace(" ", "").isalnum():
        raise AuthError("names can use letters, numbers, spaces and . - _ @")
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    _check_new_password(password, username)
    with transaction(con):
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError("that name is already taken")
        cur = con.execute("INSERT INTO users(username, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
                          (username, " ".join(display_name.split())[:80], hash_password(password), role))
    return get_user(con, cur.lastrowid)


def get_user(con: sqlite3.Connection, user_id: int) -> User | None:
    row = con.execute("SELECT * FROM users WHERE id = ? AND NOT disabled", (user_id,)).fetchone()
    return _user(row) if row else None


def list_users(con: sqlite3.Connection) -> list[dict]:
    return [{"id": r["id"], "username": r["username"], "display_name": r["display_name"] or r["username"],
             "role": r["role"], "disabled": bool(r["disabled"]), "created_at": r["created_at"],
             "can_sign_in": r["password_hash"] is not None}
            for r in con.execute("SELECT * FROM users ORDER BY username")]


def _admins(con) -> int:
    return con.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND NOT disabled "
                       "AND password_hash IS NOT NULL").fetchone()[0]


def update_user(con: sqlite3.Connection, user_id: int, *, role: str | None = None, disabled: bool | None = None,
                display_name: str | None = None, password: str | None = None) -> None:
    row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise AuthError("no such person")
    with transaction(con):
        if role is not None:
            if role not in ROLES:
                raise AuthError(f"role must be one of {', '.join(ROLES)}")
            con.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if disabled is not None:
            con.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
            if disabled:
                con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        if display_name is not None:
            con.execute("UPDATE users SET display_name = ? WHERE id = ?", (" ".join(display_name.split())[:80], user_id))
        if password is not None:
            _check_new_password(password, row["username"])
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
            con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # sign out everywhere
        if _admins(con) == 0:
            raise AuthError("there must always be at least one admin who can sign in")


def delete_user(con: sqlite3.Connection, user_id: int) -> None:
    """Documents, notes and history stay; they just no longer show who added them."""
    with transaction(con):
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if _admins(con) == 0:
            raise AuthError("there must always be at least one admin who can sign in")


# ---- sign-in ----

class Throttle:
    """After 5 wrong passwords for the same name from the same address, each further try must wait
    longer (2 s, 4 s, … up to 5 minutes). Kept in memory: a restart forgives, which is fine at home."""

    def __init__(self, free: int = 5, cap: float = 300):
        self.free, self.cap = free, cap
        self._fails: dict[tuple, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def wait_for(self, key: tuple) -> float:
        with self._lock:
            n, last = self._fails.get(key, (0, 0.0))
        if n < self.free:
            return 0.0
        return max(0.0, last + min(self.cap, 2 ** (n - self.free + 1)) - time.time())

    def failed(self, key: tuple) -> None:
        with self._lock:
            n, _ = self._fails.get(key, (0, 0.0))
            self._fails[key] = (n + 1, time.time())
            if len(self._fails) > 10000:  # never grow without bound
                self._fails.pop(next(iter(self._fails)))

    def succeeded(self, key: tuple) -> None:
        with self._lock:
            self._fails.pop(key, None)


THROTTLE = Throttle()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign_in(con: sqlite3.Connection, username: str, password: str, address: str = "",
            user_agent: str = "") -> tuple[User, str]:
    """Returns the user and a new session token, or raises AuthError (deliberately vague)."""
    key = (str(username).strip().lower(), address)
    wait = THROTTLE.wait_for(key)
    if wait > 0:
        raise TooManyTries(f"too many wrong passwords; try again in {int(wait) + 1} s")
    row = con.execute("SELECT * FROM users WHERE username = ? AND NOT disabled", (key[0],)).fetchone()
    if not row or not check_password(password, row["password_hash"]):
        if not row:
            check_password(password, hash_password("x" * 12))  # same time either way: don't reveal names
        THROTTLE.failed(key)
        raise AuthError("wrong name or password")
    THROTTLE.succeeded(key)
    token = secrets.token_urlsafe(32)
    now = time.time()
    with transaction(con):
        con.execute("INSERT INTO sessions(token_hash, user_id, created_at, last_seen, expires_at, user_agent) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (_hash(token), row["id"], now, now, now + SESSION_DAYS * 86400,
                                                  user_agent[:200]))
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return _user(row), token


def session_user(con: sqlite3.Connection, token: str) -> User | None:
    if not token:
        return None
    row = con.execute("SELECT s.last_seen, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                      "WHERE s.token_hash = ? AND NOT u.disabled", (_hash(token),)).fetchone()
    now = time.time()
    if not row or row["expires_at"] < now:
        return None
    if now - row["last_seen"] > 3600:  # renew at most hourly, so reads don't turn into writes
        con.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                    (now, now + SESSION_DAYS * 86400, _hash(token)))
    return _user(row)


def sign_out(con: sqlite3.Connection, token: str) -> None:
    con.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


# ---- API tokens ----

def role_permissions(role: str) -> tuple[str, ...]:
    """What a key made by someone with this role can be given: read-only people can only read."""
    return ("read",) if role == "readonly" else PERMISSIONS


def create_token(con: sqlite3.Connection, user_id: int, name: str, scope: str = "all",
                 permissions=None) -> tuple[int, str]:
    """Returns (id, token). The token is shown once; only its hash is kept. `permissions` (some of
    PERMISSIONS) limits the token; None lets it do everything the account can."""
    if scope not in ("all", "calendar"):
        raise AuthError("scope must be all or calendar")
    perms = None
    if permissions is not None:
        if scope != "all":
            raise AuthError("only access keys can have permissions")
        if isinstance(permissions, str):
            permissions = permissions.split(",")
        chosen = {str(x).strip().lower() for x in permissions if str(x).strip()}
        if not chosen:
            raise AuthError("choose at least one permission")
        if chosen - set(PERMISSIONS):
            raise AuthError(f"permissions are {', '.join(PERMISSIONS)}")
        user = get_user(con, user_id)
        if user is None:
            raise AuthError("no such person")
        if chosen - set(role_permissions(user.role)):
            raise AuthError("your account can't give a key more than it can do itself")
        perms = ",".join(p for p in PERMISSIONS if p in chosen)
    name = " ".join(str(name).split())[:60] or ("Calendar" if scope == "calendar" else
                                                 "AI assistant" if perms else "API token")
    token = "pj_" + secrets.token_urlsafe(30)
    with transaction(con):
        cur = con.execute("INSERT INTO api_tokens(user_id, name, token_hash, prefix, scope, permissions) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (user_id, name, _hash(token), token[:7], scope, perms))
    return cur.lastrowid, token


def list_tokens(con: sqlite3.Connection, user_id: int) -> list[dict]:
    out = []
    for r in con.execute("SELECT id, name, prefix, scope, permissions, created_at, last_used_at FROM api_tokens "
                         "WHERE user_id = ? ORDER BY id", (user_id,)):
        t = dict(r)
        t["permissions"] = t["permissions"].split(",") if t["permissions"] else None
        out.append(t)
    return out


def revoke_token(con: sqlite3.Connection, user_id: int, token_id: int) -> bool:
    return con.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user_id)).rowcount > 0


def token_user(con: sqlite3.Connection, token: str) -> User | None:
    row = con.execute("SELECT t.id AS token_id, t.scope, t.permissions AS token_permissions, t.last_used_at, u.* "
                      "FROM api_tokens t "
                      "JOIN users u ON u.id = t.user_id WHERE t.token_hash = ? AND NOT u.disabled",
                      (_hash(token),)).fetchone()
    if not row:
        return None
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if not (row["last_used_at"] or "").startswith(today):  # once a day is precise enough
        con.execute("UPDATE api_tokens SET last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                    (row["token_id"],))
    perms = frozenset(row["token_permissions"].split(",")) if row["token_permissions"] else None
    return User(row["id"], row["username"], row["display_name"], row["role"], row["scope"], perms, True)


# ---- what each role may do ----

# Paths anyone may open, signed in or not
OPEN = {"/login", "/setup", "/healthz", "/manifest.webmanifest", "/sw.js", "/api/auth/login", "/api/auth/setup",
        "/api/auth/status", "/paperjay-ca.crt", "/paperjay.mobileconfig"}
OPEN_PREFIXES = ("/static/",)
# Read-only people may also do these (they don't change documents)
READONLY_WRITES = {("POST", "/api/ask"), ("POST", "/mcp"), ("DELETE", "/mcp"), ("POST", "/api/feedback"), ("POST", "/api/auth/logout"),
                   ("POST", "/api/auth/password"), ("POST", "/api/auth/tokens")}
READONLY_WRITE_PREFIXES = ("/api/auth/tokens/",)
# Only admins: people, settings, deleting for good
ADMIN_ONLY = ("/api/users", "/api/settings", "/api/trash/empty", "/api/import", "/api/export", "/api/update", "/api/mail")


# What a limited token (User.permissions) needs for each request. Anything not listed is refused to a
# limited token: no settings, people, other keys, export or deleting for good. The role still applies after.
_DOC = r"/api/documents/\d+"
_LABEL = r"/api/labels/(tag|correspondent|document_type)"
_NEEDS = [
    ("GET", r"/api/(documents|labels|upcoming|summary|suggest|browse|index|facts|ask/stream|paper/next|tasks|"
            r"uploads|auth/status|ai/status|shred|warranties|fields)(/.*)?", {"read"}),
    ("POST", r"/api/ask", {"read"}),
    ("POST|GET|DELETE", r"/mcp", set(PERMISSIONS)),   # each tool is checked again as its own request
    ("POST", r"/api/documents|/api/documents/post_document/", {"create"}),
    ("POST", _DOC + r"/notes", {"create"}),
    ("POST", _LABEL, {"create"}),
    ("POST", r"/api/labels/correspondent/\d+/aliases", {"create"}),
    ("PATCH", _DOC + "|" + _LABEL + r"/\d+", {"update"}),
    ("PUT", _DOC + r"/fields/\d+", {"update"}),
    ("POST", _DOC + r"/(restore|reviewed|confirm-fields|confirm-filing|not-a-copy|refile|reprocess|analyze|"
                    r"suggestions/accept|shredded|unshred)", {"update"}),
    ("POST", r"/api/shred/bulk", {"update"}),
    ("POST", _DOC + r"/edit-pages|/api/documents/merge", {"create"}),   # delete is checked in the endpoint
    ("POST", _LABEL + r"/merge", {"update"}),
    ("POST", r"/api/documents/bulk", {"update", "delete"}),   # the action is checked in the endpoint
    ("POST", _DOC + r"/trash", {"delete"}),
    ("DELETE", _LABEL + r"/\d+|/api/labels/correspondent/\d+/aliases/\d+", {"delete"}),
]
_NEEDS = [(m, re.compile(p), need) for m, p, need in _NEEDS]


def token_needs(method: str, path: str) -> set | None:
    """The permissions any one of which lets a limited token make this request; None = never."""
    method = "GET" if method == "HEAD" else method
    for m, pattern, need in _NEEDS:
        if method in m.split("|") and pattern.fullmatch(path):
            return need
    return None


def may(user: User, permission: str) -> bool:
    """For checks that depend on the request body (bulk actions)."""
    return user.permissions is None or permission in user.permissions


def allowed(user: User, method: str, path: str) -> bool:
    if user.scope == "calendar":
        return method == "GET" and path == "/calendar.ics"
    if user.permissions is not None:
        need = token_needs(method, path)
        if not need or not (need & user.permissions):
            return False
    reading = method in ("GET", "HEAD")
    if path.startswith(ADMIN_ONLY) and not (reading and path.startswith("/api/settings")):
        return user.role == "admin"
    if method == "DELETE" and path.startswith("/api/documents/"):
        return user.role == "admin"
    if user.role == "readonly" and not reading:
        return (method, path) in READONLY_WRITES or path.startswith(READONLY_WRITE_PREFIXES)
    return True
