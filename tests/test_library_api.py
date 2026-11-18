"""The library management API and the web app shell."""
import httpx
import pytest
from fastapi.testclient import TestClient

from paperjay import ai, ask, db
from paperjay.app import create_app
from paperjay.config import Config

from conftest import sign_in_client
from fake_ai import load_library


@pytest.fixture
def c(tmp_path, monkeypatch):
    load_library(tmp_path / "paperjay.db", ai_on=False)
    monkeypatch.setattr(ask, "INDEX", ask.Index())
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None)), follow_redirects=False) as client:
        sign_in_client(client)
        yield client


def test_list_filter_and_search(c):
    assert c.get("/api/documents").json()["total"] == 11
    r = c.get("/api/documents", params={"q": "insurance", "sort": "relevance"}).json()
    assert {d["id"] for d in r["results"]} >= {2, 3}
    tags = {t["name"]: t["id"] for t in c.get("/api/labels/tag").json()}
    r = c.get("/api/documents", params={"tags": tags["car"]}).json()
    assert {d["id"] for d in r["results"]} == {2, 3}
    assert c.get("/api/documents", params={"tags": "x"}).status_code == 400
    assert c.get("/api/documents", params={"date_from": "2026-01-01"}).json()["total"] == 3


def test_edit_notes_history(c):
    corr = c.post("/api/labels/correspondent", json={"name": "Northwind"}).json()
    r = c.patch("/api/documents/8", json={"title": "Card bill", "correspondent_id": corr["id"]}).json()
    assert r["title"] == "Card bill" and r["correspondent"] == "Northwind"
    assert c.patch("/api/documents/8", json={"content": "x"}).status_code == 400
    assert c.patch("/api/documents/8", json={"correspondent_id": 9999}).status_code == 400
    assert c.patch("/api/documents/8", json={"doc_date": "08/14/2025"}).status_code == 400
    notes = c.post("/api/documents/8/notes", json={"body": "paid online"}).json()
    assert notes[0]["body"] == "paid online"
    actions = [h["action"] for h in c.get("/api/documents/8/history").json()]
    # the person's edit and the note are both recorded (a background profile refile may add its
    # own auto_update entries in between — Phase A files documents with the AI off)
    assert "note" in actions and "update" in actions
    assert actions.index("note") > actions.index("update")
    assert c.get("/api/documents", params={"q": "online"}).json()["results"][0]["id"] == 8


def test_trash_restore_purge_and_empty(c, tmp_path):
    assert c.delete("/api/documents/1").status_code == 409          # not in the trash yet
    c.post("/api/documents/1/trash")
    c.post("/api/documents/7/trash")
    assert c.get("/api/summary").json()["trash"] == 2
    c.post("/api/documents/7/restore")
    assert c.delete("/api/documents/1").json() == {"ok": True}
    assert c.get("/api/documents/1").status_code == 404
    c.post("/api/documents/7/trash")
    assert c.post("/api/trash/empty").json() == {"deleted": 1}


def test_bulk(c):
    tag = c.post("/api/labels/tag", json={"name": "Taxes 2025"}).json()
    assert c.post("/api/documents/bulk", json={"ids": [4, 5], "action": "add_tags", "value": [tag["id"]]}).json() == {"done": 2}
    assert {d["id"] for d in c.get("/api/documents", params={"tags": tag["id"]}).json()["results"]} == {4, 5}
    c.post("/api/documents/bulk", json={"ids": [4], "action": "remove_tags", "value": [tag["id"]]})
    c.post("/api/documents/bulk", json={"ids": [4, 5, 999], "action": "trash"})
    assert c.get("/api/summary").json()["trash"] == 2
    assert c.post("/api/documents/bulk", json={"ids": [4], "action": "explode"}).status_code == 400


def test_labels_rename_color_merge_delete(c):
    a = c.post("/api/labels/tag", json={"name": "Bill"}).json()
    b = c.post("/api/labels/tag", json={"name": "Bills"}).json()
    assert c.post("/api/labels/tag", json={"name": "bills"}).status_code == 409
    assert c.patch(f"/api/labels/tag/{a['id']}", json={"name": "Bills"}).status_code == 409
    assert c.patch(f"/api/labels/tag/{a['id']}", json={"color": "#f2c14e"}).json()["color"] == "#f2c14e"
    assert c.patch(f"/api/labels/tag/{a['id']}", json={"color": "red"}).status_code == 400
    c.post("/api/documents/bulk", json={"ids": [1], "action": "add_tags", "value": [a["id"]]})
    merged = c.post("/api/labels/tag/merge", json={"source_ids": [a["id"]], "target_id": b["id"]}).json()
    assert [t["document_count"] for t in merged if t["id"] == b["id"]] == [1] and a["id"] not in [t["id"] for t in merged]
    assert c.delete(f"/api/labels/tag/{b['id']}").json() == {"ok": True}
    assert c.get("/api/labels/nonsense").status_code == 404


def test_saved_views(c):
    v = c.post("/api/views", json={"name": " Medical  2025 ", "params": {"q": "clinic", "evil": 1, "tags": []}}).json()
    assert v["name"] == "Medical 2025" and v["params"] == {"q": "clinic"}
    assert c.post("/api/views", json={"name": " "}).status_code == 400
    assert [x["name"] for x in c.get("/api/views").json()] == ["Medical 2025"]
    c.delete(f"/api/views/{v['id']}")
    assert c.get("/api/views").json() == []


def test_app_shell_manifest_and_share(c):
    for path in ("/", "/library", "/documents/3", "/settings", "/upload?shared=x"):
        r = c.get(path)
        assert r.status_code == 200 and "PaperJay" in r.text and "__VERSION__" not in r.text
    assert c.get("/static/js/app.js").status_code == 200 and c.get("/static/app.css").headers["cache-control"] == "no-cache"
    m = c.get("/manifest.webmanifest").json()
    assert m["share_target"]["action"] == "/share" and any(i.get("purpose") == "maskable" for i in m["icons"])
    assert "fetch" in c.get("/sw.js").text
    r = c.post("/share", files={"file": ("bill.pdf", b"%PDF-1.4 shared")})
    assert r.status_code == 303 and r.headers["location"].startswith("/upload?shared=")
    assert c.get("/nope").status_code == 404
