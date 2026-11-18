"""SQLite database: one file in DATA_DIR, WAL mode, numbered migrations.

Connections run in autocommit mode; write with `with transaction(con):`, which takes the write lock
up front (BEGIN IMMEDIATE) so two writers wait for each other instead of failing halfway.

The schema version is PRAGMA user_version. The files in migrations/ (NNNN_name.sql) are applied in
order, each in one transaction together with its version bump. Before an existing database is
changed it is copied to DATA_DIR/backups, so a bad upgrade can always be undone.
"""
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

log = logging.getLogger("paperjay")

MIN_SQLITE = (3, 35, 0)  # UPDATE ... RETURNING
KEEP_BACKUPS = 10
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class SchemaTooNew(RuntimeError):
    pass


def connect(path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(f"PaperJay needs SQLite {'.'.join(map(str, MIN_SQLITE))} or newer "
                           f"(this Python has {sqlite3.sqlite_version})")
    # check_same_thread=False: FastAPI may run a request's dependency and handler on different
    # threads. A connection is still only ever used by one request or worker at a time.
    con = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 15000")
    return con


@contextmanager
def transaction(con: sqlite3.Connection):
    """Run the block in one transaction. Nested use joins the outer transaction, which commits or rolls back."""
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def available_migrations() -> list[tuple[int, str, str]]:
    found = []
    for f in resources.files("paperjay.migrations").iterdir():
        m = _MIGRATION_RE.match(f.name)
        if m:
            found.append((int(m.group(1)), f.name, f.read_text(encoding="utf-8")))
    found.sort()
    if [n for n, _, _ in found] != list(range(1, len(found) + 1)):
        raise RuntimeError(f"migrations must be numbered 0001, 0002, … without gaps: {[f for _, f, _ in found]}")
    return found


def schema_version(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]


def backup(con: sqlite3.Connection, dest: Path) -> Path:
    """Consistent copy of a live database (sqlite's online backup), written atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    out = sqlite3.connect(tmp)
    try:
        con.backup(out)
    finally:
        out.close()
    tmp.replace(dest)
    return dest


def _prune_backups(folder: Path) -> None:
    old = sorted(folder.glob("paperjay-v*.db"), key=lambda p: p.stat().st_mtime)[:-KEEP_BACKUPS]
    for p in old:
        p.unlink(missing_ok=True)


def migrate(path, backup_dir, migrations=None) -> int:
    """Bring the database at `path` up to the newest schema. Returns the resulting version."""
    migrations = available_migrations() if migrations is None else migrations
    latest = migrations[-1][0] if migrations else 0
    con = connect(path)
    try:
        current = schema_version(con)
        if current > latest:
            raise SchemaTooNew(f"the database is schema v{current}, but this PaperJay only knows up to v{latest}. "
                               "Upgrade PaperJay, or restore the backup made before the last upgrade.")
        pending = [m for m in migrations if m[0] > current]
        if not pending:
            return current
        if current > 0:
            dest = Path(backup_dir) / f"paperjay-v{current}-{time.strftime('%Y%m%d-%H%M%S')}.db"
            backup(con, dest)
            _prune_backups(Path(backup_dir))
            log.info("backed up the database to %s before upgrading it", dest)
        for num, name, sql in pending:
            log.info("applying migration %s", name)
            try:
                # executescript commits any open transaction first, so the BEGIN/COMMIT go inside the script
                con.executescript(f"BEGIN IMMEDIATE;\n{sql}\n;PRAGMA user_version = {num};\nCOMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                raise
        return schema_version(con)
    finally:
        con.close()


def get_setting(con: sqlite3.Connection, key: str, default=None) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
