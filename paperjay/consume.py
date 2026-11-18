"""Consume folder: drop a file in (from a scanner, a network share, a sync app…) and it is added.

A file is taken once its size and modification time have stayed the same for two checks in a row, so
files still being copied are left alone. Hidden and temporary files are ignored, and so are subfolders.
Added files and duplicates are deleted from the folder; files that can't be read go to rejected/.
"""
import logging
import shutil
import threading
import time
from pathlib import Path

from . import intake
from .db import connect
from .store import FileStore

log = logging.getLogger("paperjay")

_TEMP_SUFFIXES = (".part", ".crdownload", ".tmp", ".partial", ".download", "~")


def _candidate(p: Path) -> bool:
    n = p.name
    return p.is_file() and not n.startswith((".", "~$")) and not n.lower().endswith(_TEMP_SUFFIXES)


class ConsumeFolder:
    def __init__(self, folder: Path, db_path: Path, store: FileStore, max_bytes: int, on_added=None,
                 interval: float = 5.0, settle: float = 2.0):
        self.folder, self.db_path, self.store, self.max_bytes = Path(folder), db_path, store, max_bytes
        self.on_added, self.interval, self.settle = on_added, interval, settle
        self._seen: dict[Path, tuple[int, float]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="paperjay-consume", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(30)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:
                log.exception("consume folder: scan failed")
            self._stop.wait(self.interval)

    def scan(self) -> list[intake.Upload]:
        """One pass: take every file that has settled. Returns what happened to each."""
        now, current, ready = time.time(), {}, []
        for p in sorted(self.folder.iterdir()):
            if not _candidate(p):
                continue
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            sig = (st.st_size, st.st_mtime)
            current[p] = sig
            if self._seen.get(p) == sig and now - st.st_mtime >= self.settle:
                ready.append(p)
        self._seen = current
        results = []
        if not ready:
            return results
        con = connect(self.db_path)
        try:
            for p in ready:
                results.append(self._take(con, p))
                self._seen.pop(p, None)
        finally:
            con.close()
        return results

    def _take(self, con, p: Path) -> intake.Upload:
        with open(p, "rb") as f:
            up = intake.ingest(con, self.store, f, p.name, source="consume", max_bytes=self.max_bytes)
        if up.outcome == "rejected":
            dest = self.folder / "rejected"
            dest.mkdir(exist_ok=True)
            target = dest / p.name
            if target.exists():
                target = dest / f"{p.stem}-{int(time.time())}{p.suffix}"
            shutil.move(p, target)
        else:
            p.unlink(missing_ok=True)
            if up.outcome == "accepted" and self.on_added:
                self.on_added()
        return up
