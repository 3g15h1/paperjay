"""Two-step sign-in codes: TOTP (RFC 6238, 30-second steps, 6 digits, SHA-1), which every authenticator app
reads (Google Authenticator, Microsoft Authenticator, Aegis, 1Password, Bitwarden, iOS Passwords…), and
one-time recovery codes for a lost phone. Standard library only."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6
WINDOW = 1          # a code from the step before or after also counts (clocks drift)
RECOVERY_CODES = 10


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    n = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(n % 10 ** DIGITS).zfill(DIGITS)


def code_now(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at if at is not None else time.time()) // STEP))


def verify(secret: str, code: str, last_step: int = 0, at: float | None = None) -> int | None:
    """The time step the code belongs to, or None. A step at or before last_step is refused (no replays)."""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((at if at is not None else time.time()) // STEP)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(secret: str, username: str, issuer: str = "PaperJay") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}"
            f"&digits={DIGITS}&period={STEP}")


def new_recovery_codes() -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
            for _ in range(RECOVERY_CODES)]


def hash_code(code: str) -> str:
    return hashlib.sha256("".join(c for c in code.lower() if c.isalnum()).encode()).hexdigest()
