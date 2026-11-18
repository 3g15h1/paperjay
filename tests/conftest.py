import io
import os

import pytest

# Tests fake search by meaning through the AI server (tests/fake_ai.py); the built-in model has its own tests
# (test_embedder.py) that swap in a fake backend, so no test downloads it.
os.environ.setdefault("EMBED_SOURCE", "ai")
# No test asks the real GitHub whether a new version is out (test_updates.py fakes it)
os.environ.setdefault("UPDATE_CHECK", "false")
os.environ.setdefault("GITHUB_TOKEN_FILE", "/nonexistent/paperjay-test-token")

from paperjay import db
from paperjay.library import Library
from paperjay.store import FileStore


@pytest.fixture
def con(tmp_path):
    db.migrate(tmp_path / "paperjay.db", tmp_path / "backups")
    c = db.connect(tmp_path / "paperjay.db")
    yield c
    c.close()


@pytest.fixture
def store(tmp_path):
    return FileStore(tmp_path / "media")


@pytest.fixture
def lib(con):
    return Library(con)


@pytest.fixture
def add_doc(lib, store):
    """Store a file and add it as a document; returns the id."""
    def add(data: bytes = None, name="scan.pdf", **kw):
        data = data if data is not None else f"file {name} {len(kw)} {sorted(kw.items())}".encode()
        staged = store.stage(io.BytesIO(data))
        store.commit(staged, ".pdf")
        kw.setdefault("title", name)
        return lib.add(checksum=staged.checksum, original_name=name, original_ext=".pdf",
                       mime_type="application/pdf", size=staged.size, **kw)
    return add


def sign_in_client(client, role="admin", username=None):
    """Create a person with an API token and send it with every request of this test client."""
    from paperjay import auth
    c = db.connect(client.app.state.config.db_path)
    try:
        user = auth.create_user(c, username or f"test-{role}", "correct horse battery", role)
        _, token = auth.create_token(c, user.id, "tests")
    finally:
        c.close()
    client.headers["Authorization"] = f"Token {token}"
    return user
