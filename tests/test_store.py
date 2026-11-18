import hashlib
import io

import pytest

from paperjay.store import FileStore, TooLarge, clean_ext


def test_stage_commit_and_dedupe(store):
    data = b"%PDF-1.7 hello"
    s = store.stage(io.BytesIO(data))
    assert s.checksum == hashlib.sha256(data).hexdigest() and s.size == len(data)
    p = store.commit(s, ".pdf")
    assert p.read_bytes() == data and p.parent.name == s.checksum[:2]
    again = store.stage(io.BytesIO(data))
    assert store.commit(again, ".pdf") == p
    assert not again.path.exists()
    assert list((store.root / "tmp").iterdir()) == []


def test_too_large_leaves_nothing_behind(store):
    with pytest.raises(TooLarge):
        store.stage(io.BytesIO(b"x" * 100), max_bytes=10)
    assert list((store.root / "tmp").iterdir()) == []


def test_paths_cannot_escape(store):
    with pytest.raises(ValueError):
        store.path("originals", "../../etc/passwd", ".pdf")
    with pytest.raises(ValueError):
        store.path("secrets", "a" * 64, ".pdf")
    assert clean_ext("Scan 3.JPG") == ".jpg"
    assert clean_ext("evil.p/df") == ".bin"
    assert clean_ext("noext") == ".bin"


def test_put_and_remove(store, tmp_path):
    s = store.stage(io.BytesIO(b"orig"))
    store.commit(s, ".jpg")
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(b"archive")
    store.put("archive", s.checksum, ".pdf", pdf)
    assert store.path("archive", s.checksum, ".pdf").read_bytes() == b"archive"
    store.remove(s.checksum, ".jpg")
    assert not store.path("originals", s.checksum, ".jpg").exists()
    assert not store.path("archive", s.checksum, ".pdf").exists()
