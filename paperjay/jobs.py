"""Background jobs (OCR, AI filing, embeddings…), queued in the database so they survive restarts.

A Worker runs N threads, each with its own connection. A thread claims the next ready job atomically
(one UPDATE … RETURNING), so two threads never get the same job. A failed job is retried with
exponential backoff until max_attempts; raise Permanent to fail it straight away. Jobs left 'running'
by a crash are put back in the queue at startup (recover).
"""
import json
import logging
import sqlite3
import threading
import time
from typing import Callable

from .db import connect, transaction

log = logging.getLogger("paperjay")

BACKOFF_BASE = 30.0
BACKOFF_MAX = 3600.0
KEEP_DONE_DAYS = 14


class Permanent(Exception):
    """Raise from a handler when retrying can't help (e.g. a file that isn't a document)."""


class Defer(Exception):
    """Raise from a handler to run the job again later without counting it as a failed attempt
    (e.g. people are using the AI right now, or the AI server is down)."""

    def __init__(self, seconds: float, reason: str = ""):
        super().__init__(reason or f"deferred {seconds:.0f} s")
        self.seconds = seconds


def defer(con: sqlite3.Connection, job_id: int, seconds: float, reason: str = "") -> None:
    con.execute("UPDATE jobs SET status = 'queued', attempts = MAX(attempts - 1, 0), run_after = ?, error = ? "
                "WHERE id = ?", (time.time() + seconds, reason or None, job_id))


def enqueue(con: sqlite3.Connection, kind: str, document_id: int | None = None, payload: dict | None = None,
            priority: int = 100, max_attempts: int = 3, delay: float = 0.0) -> int:
    """Queue a job. If the same kind of job for the same document is already waiting, that one is reused."""
    now = time.time()
    if document_id is not None:
        row = con.execute("SELECT id FROM jobs WHERE kind = ? AND document_id = ? AND status = 'queued'",
                          (kind, document_id)).fetchone()
        if row:
            return row[0]
    cur = con.execute(
        "INSERT INTO jobs(kind, document_id, payload, priority, max_attempts, run_after, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, document_id, json.dumps(payload or {}), priority, max_attempts, now + delay, now))
    return cur.lastrowid


def claim(con: sqlite3.Connection, worker: str, kinds=None) -> dict | None:
    now = time.time()
    kind_filter, params = "", [now]
    if kinds:
        kind_filter = f"AND kind IN ({', '.join('?' * len(kinds))})"
        params += list(kinds)
    row = con.execute(
        "UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = ?, worker = ?, error = NULL "
        "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= ? " + kind_filter +
        " ORDER BY priority, run_after, id LIMIT 1) RETURNING *",
        (now, worker, *params)).fetchone()
    if not row:
        return None
    job = dict(row)
    job["payload"] = json.loads(job["payload"])
    return job


def complete(con: sqlite3.Connection, job_id: int) -> None:
    con.execute("UPDATE jobs SET status = 'done', finished_at = ? WHERE id = ?", (time.time(), job_id))


def fail(con: sqlite3.Connection, job_id: int, error: str, retry: bool = True) -> bool:
    """Record a failure. Returns True if the job will be retried."""
    with transaction(con):
        row = con.execute("SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return False
        if retry and row["attempts"] < row["max_attempts"]:
            wait = min(BACKOFF_BASE * 2 ** (row["attempts"] - 1), BACKOFF_MAX)
            con.execute("UPDATE jobs SET status = 'queued', run_after = ?, error = ? WHERE id = ?",
                        (time.time() + wait, error[:2000], job_id))
            return True
        con.execute("UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                    (time.time(), error[:2000], job_id))
        return False


def recover(con: sqlite3.Connection) -> int:
    """Put jobs a crashed process left 'running' back in the queue. Only call before workers start."""
    return con.execute("UPDATE jobs SET status = 'queued', run_after = ? WHERE status = 'running'",
                       (time.time(),)).rowcount


def retry_failed(con: sqlite3.Connection, job_id: int) -> None:
    """Give a failed job a fresh set of attempts (the 'Retry' button)."""
    con.execute("UPDATE jobs SET status = 'queued', attempts = 0, run_after = ?, finished_at = NULL "
                "WHERE id = ? AND status = 'failed'", (time.time(), job_id))


def prune(con: sqlite3.Connection, days: int = KEEP_DONE_DAYS) -> int:
    return con.execute("DELETE FROM jobs WHERE status = 'done' AND finished_at < ?",
                       (time.time() - days * 86400,)).rowcount


def counts(con: sqlite3.Connection) -> dict:
    out = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    for r in con.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status"):
        out[r[0]] = r[1]
    return out


Handler = Callable[[sqlite3.Connection, dict], None]


class Worker:
    """Runs queued jobs on `threads` threads until stop() is called."""

    def __init__(self, db_path, handlers: dict[str, Handler], threads: int = 1, poll: float = 1.0):
        self.db_path, self.handlers, self.threads, self.poll = db_path, handlers, threads, poll
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        con = connect(self.db_path)
        try:
            n = recover(con)
            if n:
                log.info("put %d interrupted job(s) back in the queue", n)
        finally:
            con.close()
        for i in range(self.threads):
            t = threading.Thread(target=self._run, args=(f"worker-{i + 1}",), name=f"paperjay-worker-{i + 1}",
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def wake(self) -> None:
        """Tell idle threads there is new work, instead of waiting for the next poll."""
        self._wake.set()

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()

    def _run(self, name: str) -> None:
        con = connect(self.db_path)
        try:
            while not self._stop.is_set():
                try:
                    job = claim(con, name, kinds=list(self.handlers))
                except sqlite3.OperationalError as e:  # e.g. database locked for too long; try again shortly
                    log.warning("%s: couldn't claim a job: %s", name, e)
                    job = None
                if job is None:
                    self._wake.wait(self.poll)
                    self._wake.clear()
                    continue
                self._execute(con, name, job)
        finally:
            con.close()

    def _execute(self, con: sqlite3.Connection, name: str, job: dict) -> None:
        started = time.monotonic()
        try:
            self.handlers[job["kind"]](con, job)
        except Defer as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            defer(con, job["id"], e.seconds, str(e) if e.args and e.args[0] else "")
            return
        except Permanent as e:
            log.warning("%s: job %d (%s) failed: %s", name, job["id"], job["kind"], e)
            fail(con, job["id"], str(e), retry=False)
        except Exception as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            again = fail(con, job["id"], f"{type(e).__name__}: {e}")
            log.warning("%s: job %d (%s) failed%s: %s", name, job["id"], job["kind"],
                        ", will retry" if again else "", e, exc_info=not again)
        else:
            complete(con, job["id"])
            log.info("%s: job %d (%s) done in %.1fs", name, job["id"], job["kind"], time.monotonic() - started)
