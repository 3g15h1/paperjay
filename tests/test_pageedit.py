"""Fixing pages (0.7.3): turning, taking out, moving and splitting pages, joining documents. Always new
documents with the old filing and paper number; the old ones go to the trash."""
import io

import pikepdf
import pytest
from fastapi.testclient import TestClient

from paperjay import auth, db, pageedit
from paperjay.app import create_app
from paperjay.config import Config
from paperjay.library import Library
from paperjay.store import FileStore
from conftest import sign_in_client


def make_pdf(widths) -> bytes:
    """A PDF whose pages are told apart by their width."""
    pdf = pikepdf.new()
    for w in widths:
        pdf.add_blank_page(page_size=(w, 800))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def widths(store: FileStore, lib: Library, doc_id: int):
    d = lib.get(doc_id)
    with pikepdf.open(store.path("originals", d["checksum"], d["original_ext"])) as pdf:
        return [(int(p.mediabox[2]), int(p.obj.get("/Rotate", 0))) for p in pdf.pages]


@pytest.fixture
def env(tmp_path):
    db.migrate(tmp_path / "paperjay.db", tmp_path / "backups")
    con = db.connect(tmp_path / "paperjay.db")
    store = FileStore(tmp_path / "media")
    lib = Library(con)

    def add(ws, title="Statement", **kw):
        staged = store.stage(io.BytesIO(make_pdf(ws)))
        store.commit(staged, ".pdf")
        return lib.add(
            title=title, checksum=staged.checksum, original_name=f"{title}.pdf", original_ext=".pdf",
            mime_type="application/pdf", size=staged.size, status="ready", content="text", **kw)
    yield con, store, lib, add
    con.close()


def test_plan_validates():
    assert pageedit.plan(3, [{"n": 1}, {"n": 2, "rotate": 90}, {"n": 3, "keep": False}]) == [[(0, 0), (1, 90)]]
    assert pageedit.plan(3, [{"n": 3}, {"n": 1}, {"n": 2}], split_after=[1]) == [[(2, 0), (0, 0)], [(1, 0)]]
    for bad in ([], [{"n": 4}], [{"n": 1}, {"n": 1}], [{"n": 1, "rotate": 45}], [{"n": 1, "keep": False}],
                [{"n": True}], ["x"]):
        with pytest.raises(pageedit.EditError):
            pageedit.plan(3, bad)


def test_turn_and_take_out_keeps_the_filing(env):
    con, store, lib, add = env
    tag = lib.create_label("tag", "Bank")
    corr = lib.create_label("correspondent", "Example Credit Union")
    old = add([300, 400, 500], doc_date="2025-02-28", paper_no=7)
    lib.update(old, tags=[tag], correspondent_id=corr, category="finance", keep_until="2030-01-01")
    lib.add_note(old, "paid in full")
    [new] = pageedit.edit(con, store, old, [{"n": 1, "rotate": 90}, {"n": 2, "keep": False}, {"n": 3}])
    assert widths(store, lib, new) == [(300, 90), (500, 0)]
    d = lib.get(new)
    assert (d["title"], d["doc_date"], d["correspondent_id"], d["category"], d["keep_until"], d["paper_no"]) == \
        ("Statement", "2025-02-28", corr, "finance", "2030-01-01", 7)
    assert [t["id"] for t in d["tags"]] == [tag] and [n["body"] for n in lib.notes(new)] == ["paid in full"]
    assert {"title", "correspondent_id"} <= lib.person_fields(new)     # the AI leaves it alone
    o = lib.get(old)
    assert o["deleted_at"] and o["paper_no"] is None
    assert d["status"] == "pending"                                    # read again in the background


def test_split_into_parts(env):
    con, store, lib, add = env
    old = add([300, 400, 500, 600], paper_no=3)
    ids = pageedit.edit(con, store, old, [{"n": i} for i in (1, 2, 3, 4)], split_after=[1, 3])
    assert [widths(store, lib, i) for i in ids] == [[(300, 0)], [(400, 0), (500, 0)], [(600, 0)]]
    assert [lib.get(i)["title"] for i in ids] == [f"Statement (part {k} of 3)" for k in (1, 2, 3)]
    assert [lib.get(i)["paper_no"] for i in ids] == [3, None, None]


def test_nothing_to_change_and_unreadable(env):
    con, store, lib, add = env
    old = add([300, 400])
    with pytest.raises(pageedit.EditError, match="nothing"):
        pageedit.edit(con, store, old, [{"n": 1}, {"n": 2}])
    staged = store.stage(io.BytesIO(b"\x89PNG not really"))
    store.commit(staged, ".png")
    img = lib.add(title="photo", checksum=staged.checksum, original_name="p.png", original_ext=".png",
                  mime_type="image/png", size=staged.size, status="failed")
    with pytest.raises(pageedit.EditError, match="once PaperJay has read it"):
        pageedit.edit(con, store, img, [{"n": 1, "rotate": 90}])


def test_merge(env):
    con, store, lib, add = env
    t1, t2 = lib.create_label("tag", "A"), lib.create_label("tag", "B")
    a = add([300], title="Page one", paper_no=1)
    b = add([400, 500], title="Pages two and three", paper_no=2)
    lib.update(a, tags=[t1])
    lib.update(b, tags=[t2])
    new = pageedit.merge(con, store, [b, a])
    assert widths(store, lib, new) == [(400, 0), (500, 0), (300, 0)]
    d = lib.get(new)
    assert d["title"] == "Pages two and three" and d["paper_no"] == 2
    assert sorted(t["id"] for t in d["tags"]) == sorted([t1, t2])
    assert lib.get(a)["deleted_at"] and lib.get(b)["deleted_at"]
    with pytest.raises(pageedit.EditError):
        pageedit.merge(con, store, [a])
    parts = pageedit.edit(con, store, new, [{"n": 1}, {"n": 2}, {"n": 3}], split_after=[1])
    assert lib.get(pageedit.merge(con, store, parts))["title"] == "Pages two and three"   # whole again


def test_all_or_nothing(env, monkeypatch):
    con, store, lib, add = env
    old = add([300, 400], paper_no=5)
    calls = []
    real = pageedit._ingest

    def flaky(*a, **kw):
        if calls:
            raise pageedit.EditError("disk full")
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(pageedit, "_ingest", flaky)
    with pytest.raises(pageedit.EditError):
        pageedit.edit(con, store, old, [{"n": 1}, {"n": 2}], split_after=[1])
    assert lib.get(old)["paper_no"] == 5 and not lib.get(old)["deleted_at"]
    assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1   # the half-made one is gone
    monkeypatch.setattr(pageedit, "_ingest", real)
    assert len(pageedit.edit(con, store, old, [{"n": 1}, {"n": 2}], split_after=[1])) == 2   # and can be redone


def test_api_and_permissions(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        user = sign_in_client(c)
        con = db.connect(tmp_path / "paperjay.db")
        store, lib = FileStore(tmp_path / "media"), Library(con)
        ids = []
        for ws in ([300, 400], [500]):
            staged = store.stage(io.BytesIO(make_pdf(ws)))
            store.commit(staged, ".pdf")
            ids.append(lib.add(title=f"d{ws[0]}", checksum=staged.checksum, original_name="d.pdf", original_ext=".pdf",
                               mime_type="application/pdf", size=staged.size, status="ready", content="x"))
        _, create_only = auth.create_token(con, user.id, "k", permissions=["create"])
        con.close()
        body = {"pages": [{"n": 1, "rotate": 180}, {"n": 2}]}
        assert c.post(f"/api/documents/{ids[0]}/edit-pages", json={"pages": []}).status_code == 400
        assert c.post("/api/documents/999/edit-pages", json=body).status_code == 404
        saved = dict(c.headers)
        c.headers["Authorization"] = f"Token {create_only}"
        assert c.post(f"/api/documents/{ids[0]}/edit-pages", json=body).status_code == 403
        c.headers.update(saved)
        r = c.post(f"/api/documents/{ids[0]}/edit-pages", json=body)
        assert r.status_code == 200 and len(r.json()["documents"]) == 1
        new = r.json()["documents"][0]
        assert c.post("/api/documents/merge", json={"ids": [new, ids[1]]}).status_code == 200
        assert c.post("/api/documents/merge", json={"ids": ["x"]}).status_code == 400
        assert c.post("/api/documents/merge", json={"ids": [ids[0], ids[1]]}).status_code == 400   # in the trash
