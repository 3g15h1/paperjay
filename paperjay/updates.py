"""Updates: is there a newer release, telling admins when one comes out, and (on the Proxmox/LXC install)
the "Update" button.

How PaperJay was installed is told by PAPERJAY_INSTALL:
  lxc     the Proxmox installer: the button writes the wanted release tag to UPDATE_DIR/update-request;
          a root systemd path unit (paperjay-update) backs up the database, installs that release next to
          the current one, checks it answers, and otherwise switches back. Its outcome is in update-status.json.
  docker  the image is updated by pulling it; the page shows the command.
  (other) a manual install; the page shows how to upgrade with pip.

Checking: the upkeep thread asks GitHub every CHECK_EVERY (unless the "update_check" setting is off), and the
settings page can ask right away. The last answer is kept in the settings table, so a restart doesn't forget a
new version. Admins see a notice about a newer version until they update or dismiss it (per version).
While the repository is private GitHub only answers with a token: GITHUB_TOKEN_FILE (default
/etc/paperjay/github-token, the file the installer and update.sh use) is sent when PaperJay can read it.
"""
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

import httpx

from . import __version__
from .db import get_setting, set_setting

log = logging.getLogger("paperjay")

REPO = "3g15h1/paperjay"
TRANSPORT = None
CHECK_EVERY = 6 * 3600
LAST_KEY = "update_last_check"       # the last answer from GitHub (JSON), kept across restarts
DISMISSED_KEY = "update_dismissed"   # the release an admin said "not now" to
_TAG = re.compile(r"^v\d{1,4}\.\d{1,4}\.\d{1,4}$")
_cache: dict = {}
_lock = threading.Lock()


def install_kind() -> str:
    return os.environ.get("PAPERJAY_INSTALL", "manual")


def update_dir() -> Path:
    return Path(os.environ.get("PAPERJAY_UPDATE_DIR", "/var/lib/paperjay"))


def _token() -> str:
    path = Path(os.environ.get("GITHUB_TOKEN_FILE", "/etc/paperjay/github-token"))
    try:
        return path.read_text().strip()[:500]
    except OSError:
        return ""


def _version_tuple(v: str) -> tuple:
    nums = re.findall(r"\d+", v.split("+")[0].replace(".dev", ".-1."))
    return tuple(int(n) for n in nums[:3]) + ((-1,) if "dev" in v or "rc" in v else (0,))


def newer(tag: str, current: str = __version__) -> bool:
    return _version_tuple(tag.lstrip("v")) > _version_tuple(current)


def _fresh(result: dict) -> dict:
    """A kept answer, judged again against the running version (PaperJay may have been updated since)."""
    out = dict(result, current=__version__, install=install_kind())
    if out.get("latest"):
        out["newer"] = newer(out["latest"])
    return out


def _load(con: sqlite3.Connection | None) -> None:
    """After a restart, start from the answer kept in the database. Call with _lock held."""
    if _cache or con is None:
        return
    try:
        kept = json.loads(get_setting(con, LAST_KEY) or "null")
    except ValueError:
        kept = None
    if isinstance(kept, dict) and isinstance(kept.get("checked_at"), (int, float)):
        _cache.update(at=kept["checked_at"], result=kept)


def _ask_github() -> dict:
    result = {"checked_at": time.time()}
    headers = {"Accept": "application/vnd.github+json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(transport=TRANSPORT, timeout=10, headers=headers) as c:
            r = c.get(f"https://api.github.com/repos/{REPO}/releases/latest")
        if r.status_code == 404:
            result["error"] = "no published release yet" if token else \
                "no published release found (a private repository needs a GitHub token in /etc/paperjay/github-token)"
        elif r.status_code in (401, 403):
            result["error"] = "GitHub refused the request (check the token, or try again in an hour)"
        else:
            r.raise_for_status()
            rel = r.json()
            result.update(latest=rel["tag_name"], url=rel.get("html_url"), notes=(rel.get("body") or "")[:4000],
                          published=rel.get("published_at"))
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        result["error"] = f"couldn't reach GitHub ({type(e).__name__})"
    return result


def check(force: bool = False, con: sqlite3.Connection | None = None) -> dict:
    """The newest release on GitHub, asked at most every CHECK_EVERY unless forced. With a database
    connection the answer is also kept there, and read from there after a restart."""
    with _lock:
        _load(con)
        if not force and _cache.get("at", 0) > time.time() - CHECK_EVERY:
            return _fresh(_cache["result"])
    result = _ask_github()
    with _lock:
        before = _cache.get("result") or {}
        if "error" in result and before.get("latest"):
            # a failed check doesn't forget the release found earlier
            result = {**{k: v for k, v in before.items() if k != "error"}, **result}
        _cache.update(at=result["checked_at"], result=result)
    if con is not None:
        set_setting(con, LAST_KEY, json.dumps(result))
    return _fresh(result)


def known(con: sqlite3.Connection | None = None) -> dict:
    """The last answer, without asking GitHub ({} when there's none)."""
    with _lock:
        _load(con)
        return _fresh(_cache["result"]) if _cache else {}


def periodic(con: sqlite3.Connection) -> dict | None:
    """Called by the upkeep thread: check when it's time (unless switched off), and log a new release once."""
    from . import settings
    if not settings.get(con, "update_check"):
        return None
    before = known(con).get("latest")
    r = check(con=con)
    if r.get("newer") and r.get("latest") != before:
        log.info("PaperJay %s is available (this is %s)", r["latest"], __version__)
    return r


def notice(con: sqlite3.Connection) -> dict:
    """What the app shows admins: a newer version they haven't dismissed. Never asks GitHub by itself."""
    if _running(status()):
        return {"show": False, "updating": True}
    r = known(con)
    if not r.get("newer"):
        return {"show": False}
    return {"show": get_setting(con, DISMISSED_KEY) != r["latest"], "latest": r["latest"], "current": __version__,
            "install": install_kind()}


def dismiss(con: sqlite3.Connection, tag: str) -> None:
    if not _TAG.match(tag):
        raise ValueError("not a release tag")
    set_setting(con, DISMISSED_KEY, tag)


def _running(st: dict) -> bool:
    """An update is under way (a "running" older than 30 minutes is one the updater never finished)."""
    try:
        return st.get("state") == "running" and time.time() - float(st.get("at") or 0) < 1800
    except (TypeError, ValueError):
        return False


def status() -> dict:
    """The outcome of the last update (LXC install), written by the root updater."""
    try:
        out = json.loads((update_dir() / "update-status.json").read_text())
        return out if isinstance(out, dict) else {}
    except (OSError, ValueError):
        return {}


def request(tag: str) -> None:
    if install_kind() != "lxc":
        raise RuntimeError("the Update button works on the Proxmox install; see the instructions shown instead")
    if not _TAG.match(tag):
        raise ValueError("not a release tag")
    if _running(status()):
        raise RuntimeError("an update is already running")
    d = update_dir()
    tmp = d / "update-request.part"
    tmp.write_text(tag)
    os.replace(tmp, d / "update-request")


def instructions() -> str:
    kind = install_kind()
    if kind == "docker":
        return "docker compose pull && docker compose up -d   (your data in ./data is kept and backed up before any upgrade)"
    if kind == "lxc":
        return "Use the Update button, or run the installer again on the Proxmox host."
    return "pip install --upgrade paperjay, then restart PaperJay."
