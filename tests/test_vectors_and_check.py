import io

from fastapi.testclient import TestClient

from paperjay import check, db, vectors
from paperjay.app import create_app
from paperjay.config import Config


def test_vector_search_best_passage_per_document(con, lib, add_doc):
    a, b = add_doc(name="a.pdf"), add_doc(name="b.pdf")
    with db.transaction(con):
        vectors.put(con, a, "m", [(0, 10, [1, 0, 0]), (10, 20, [0.9, 0.1, 0])])
        vectors.put(con, b, "m", [(0, 5, [0, 1, 0])])
    idx = vectors.Index()
    r = idx.search(con, "m", [1, 0.05, 0])
    assert [x["document_id"] for x in r] == [a, b] and r[0]["char_end"] == 10
    lib.trash(a)  # trashed documents drop out without touching their embeddings
    assert [x["document_id"] for x in idx.search(con, "m", [1, 0, 0])] == [b]
    assert idx.search(con, "other-model", [1, 0, 0]) == []


def test_check_finds_missing_corrupt_and_orphan(con, lib, add_doc, store):
    good = add_doc(b"good")
    bad = add_doc(b"will be corrupted")
    gone = add_doc(b"will go missing")
    assert check.run(con, store)["ok"]
    store.path("originals", lib.get(bad)["checksum"], ".pdf").write_bytes(b"bitrot")
    store.path("originals", lib.get(gone)["checksum"], ".pdf").unlink()
    store.commit(store.stage(io.BytesIO(b"stray")), ".pdf")
    report = check.run(con, store)
    kinds = sorted((p["kind"], p.get("document_id")) for p in report["problems"])
    assert kinds == [("corrupt", bad), ("missing", gone), ("orphan", None)]
    assert good not in [p.get("document_id") for p in report["problems"]]


def test_app_starts_migrates_and_reports(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app) as client:
        from conftest import sign_in_client
        sign_in_client(client)
        h = client.get("/healthz").json()
        assert h["ok"] and h["schema"] == db.available_migrations()[-1][0]
        s = client.get("/api/stats").json()
        assert s["documents"]["documents"] == 0 and s["jobs"]["queued"] == 0
    assert (tmp_path / "paperjay.db").exists() and (tmp_path / "media" / "originals").is_dir()
