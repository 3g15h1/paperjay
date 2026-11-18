"""File store: every document's files on disk, named by the sha256 of the original.

    media/originals/ab/abcd….pdf   the file exactly as it was uploaded (never changed)
    media/archive/ab/abcd….pdf     the searchable PDF made by OCR
    media/thumbs/ab/abcd….webp     the first-page thumbnail
    media/tmp/                     uploads being received

Uploads are written to tmp/ while being hashed (stage), then renamed into place (commit), so a
crash never leaves a half-written file under a real name. Files are fsynced before the rename.
"""
import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

KINDS = {"originals", "archive", "thumbs"}
_EXT_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1 << 20


def clean_ext(name_or_ext: str) -> str:
    """'Scan 3.JPG' -> '.jpg'. Anything odd becomes '.bin' so it can't escape the store or confuse a viewer."""
    ext = os.path.splitext(name_or_ext)[1] if not name_or_ext.startswith(".") else name_or_ext
    ext = ext.lower()
    return ext if _EXT_RE.match(ext) else ".bin"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class TooLarge(ValueError):
    pass


@dataclass
class Staged:
    path: Path
    checksum: str
    size: int


class FileStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        for kind in (*KINDS, "tmp"):
            (self.root / kind).mkdir(parents=True, exist_ok=True)

    def path(self, kind: str, checksum: str, ext: str) -> Path:
        if kind not in KINDS:
            raise ValueError(f"unknown file kind {kind!r}")
        if not _CHECKSUM_RE.match(checksum) or not _EXT_RE.match(ext):
            raise ValueError("bad checksum or extension")
        return self.root / kind / checksum[:2] / f"{checksum}{ext}"

    def stage(self, src: BinaryIO, max_bytes: int | None = None) -> Staged:
        """Copy an upload into tmp/ while hashing it."""
        h, size = hashlib.sha256(), 0
        fd, tmp = tempfile.mkstemp(dir=self.root / "tmp", prefix="upload-")
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := src.read(_CHUNK):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise TooLarge(f"file is larger than {max_bytes} bytes")
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            os.unlink(tmp)
            raise
        return Staged(Path(tmp), h.hexdigest(), size)

    def discard(self, staged: Staged) -> None:
        staged.path.unlink(missing_ok=True)

    def commit(self, staged: Staged, ext: str) -> Path:
        """Move a staged upload into originals/. If the same file is already stored, keep that one."""
        return self._place(staged.path, self.path("originals", staged.checksum, ext))

    def put(self, kind: str, checksum: str, ext: str, src: Path) -> Path:
        """Move a generated file (archive PDF, thumbnail) into place, replacing any older one."""
        dest = self.path(kind, checksum, ext)
        with open(src, "rb") as f:
            os.fsync(f.fileno())
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
        _fsync_dir(dest.parent)
        return dest

    def _place(self, src: Path, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            src.unlink(missing_ok=True)
        else:
            os.replace(src, dest)
            _fsync_dir(dest.parent)
        return dest

    def remove(self, checksum: str, ext: str) -> None:
        """Delete all of a document's files (used when it is purged from the trash)."""
        for kind, e in (("originals", ext), ("archive", ".pdf"), ("thumbs", ".webp")):
            self.path(kind, checksum, e).unlink(missing_ok=True)

    def files(self, kind: str):
        """Every file of one kind, as (checksum, ext, path). Used by the integrity check."""
        for p in sorted((self.root / kind).glob("??/*")):
            stem, dot, rest = p.name.partition(".")
            yield stem, dot + rest, p

    def clean_tmp(self, older_than: float = 24 * 3600) -> int:
        """Remove uploads abandoned by a crash. Returns how many were removed."""
        cutoff, n = time.time() - older_than, 0
        for p in (self.root / "tmp").iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                n += 1
        return n
