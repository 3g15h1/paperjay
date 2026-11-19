"""MCP server and access keys with chosen permissions (0.6)."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from paperjay import auth, mcp_api
from paperjay.app import create_app
from paperjay.config import Config
from tests.docgen import text_pdf


@pytest.fixture
def app(tmp_path, con, monkeypatch):
    monkeypatch.setattr(auth, "THROTTLE", auth.Throttle())
    return create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)


@pytest.fixture
def people(con):
    return {role: auth.create_user(con, f"pat-{role}", "correct horse battery", role)
            for role in ("admin", "member", "readonly")}


def key(con, user, perms):
    return auth.create_token(con, user.id, "Claude", permissions=perms)[1]


def rpc(c, token, method, params=None, id_=1):
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


def call(c, token, tool, **args):
    res = rpc(c, token, "tools/call", {"name": tool, "arguments": args})
    assert "result" in res, res
    text = res["result"]["content"][0]["text"]
    if res["result"].get("isError"):
        return {"error": text}
    return json.loads(text)


def names(c, token):
    return {t["name"] for t in rpc(c, token, "tools/list")["result"]["tools"]}


# ---- keys and permissions ----

def test_key_permissions_are_checked_when_made(con, people):
    with pytest.raises(auth.AuthError):
        key(con, people["member"], [])
    with pytest.raises(auth.AuthError):
        key(con, people["member"], ["read", "admin"])
    with pytest.raises(auth.AuthError):   # a read-only account can't hand out more than it has
        key(con, people["readonly"], ["read", "delete"])
    key(con, people["readonly"], ["read"])
    t = auth.list_tokens(con, people["member"].id)
    assert t == []
    key(con, people["member"], ["delete", "read"])
    assert auth.list_tokens(con, people["member"].id)[0]["permissions"] == ["read", "delete"]


def test_old_keys_keep_full_access(con, people):
    _, token = auth.create_token(con, people["member"].id, "script")
    u = auth.token_user(con, token)
    assert u.permissions is None and auth.allowed(u, "POST", "/api/documents/1/trash")


def test_upgrade_from_0008_keeps_keys_with_full_access(tmp_path):
    from paperjay import db
    v8 = [m for m in db.available_migrations() if m[0] <= 8]
    path = tmp_path / "p.db"
    db.migrate(path, tmp_path / "b", v8)
    c = db.connect(path)
    c.execute("INSERT INTO users(username, password_hash, role) VALUES ('sam', 'x', 'member')")
    c.execute("INSERT INTO api_tokens(user_id, name, token_hash, prefix) VALUES (1, 'shortcut', ?, 'pj_abcd')",
              (auth._hash("pj_secret"),))
    c.close()
    assert db.migrate(path, tmp_path / "b") >= 9
    c = db.connect(path)
    u = auth.token_user(c, "pj_secret")
    assert u.permissions is None and auth.allowed(u, "POST", "/api/documents")
    assert auth.list_tokens(c, 1)[0]["permissions"] is None
    c.close()


def test_limited_key_cannot_reach_settings_people_or_other_keys(app, con, people, add_doc):
    token = key(con, people["admin"], ["read", "create", "update", "delete"])
    doc = add_doc(name="bill.pdf")
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/documents", headers=h).status_code == 200
        for method, path in [("GET", "/api/settings"), ("GET", "/api/users"), ("POST", "/api/auth/tokens"),
                             ("GET", "/api/export.zip"), ("POST", "/api/trash/empty"),
                             ("DELETE", f"/api/documents/{doc}")]:   # never deletes for good
            assert c.request(method, path, headers=h, json={}).status_code == 403, path


def test_each_permission_opens_only_its_own_requests(app, con, people, add_doc):
    doc = add_doc(name="bill.pdf")
    with TestClient(app) as c:
        def status(perms, method, path, body=None):
            return c.request(method, path, json=body, headers={"Authorization": f"Bearer {key(con, people['member'], perms)}"}).status_code
        assert status(["create"], "GET", "/api/documents") == 403
        assert status(["read"], "PATCH", f"/api/documents/{doc}", {"title": "x"}) == 403
        assert status(["update"], "PATCH", f"/api/documents/{doc}", {"title": "Water bill"}) == 200
        assert status(["update"], "POST", f"/api/documents/{doc}/trash") == 403
        assert status(["delete"], "POST", "/api/documents/bulk", {"ids": [doc], "action": "add_tags", "value": []}) == 403
        assert status(["update"], "POST", "/api/documents/bulk", {"ids": [doc], "action": "trash"}) == 403
        assert status(["delete"], "POST", "/api/documents/bulk", {"ids": [doc], "action": "trash"}) == 200


# ---- the MCP protocol ----

def test_handshake_and_tools_follow_the_key(app, con, people):
    with TestClient(app) as c:
        full = key(con, people["member"], ["read", "create", "update", "delete"])
        init = rpc(c, full, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "test", "version": "1"}})["result"]
        assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "paperjay"
        assert "tools" in init["capabilities"]
        old = rpc(c, full, "initialize", {"protocolVersion": "1999-01-01"})["result"]
        assert old["protocolVersion"] == mcp_api.PROTOCOL_VERSIONS[0]
        r = c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                   headers={"Authorization": f"Bearer {full}"})
        assert r.status_code == 202
        assert rpc(c, full, "ping")["result"] == {}
        assert rpc(c, full, "nope")["error"]["code"] == -32601
        assert names(c, full) == set(mcp_api.BY_NAME)

        reader = key(con, people["member"], ["read"])
        assert names(c, reader) == {"search_documents", "get_document", "ask", "list_labels", "upcoming"}
        res = rpc(c, reader, "tools/call", {"name": "delete_document", "arguments": {"document_id": 1}})
        assert res["error"]["code"] == -32602
        assert names(c, key(con, people["member"], ["create"])) == {"add_document", "add_note", "create_label"}
        # a read-only account's full-access key still only gets reading tools
        _, ro = auth.create_token(con, people["readonly"].id, "old key")
        assert names(c, ro) == names(c, reader)


def test_mcp_needs_an_access_key(app, con, people):
    with TestClient(app) as c:
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        assert c.post("/mcp", json=body).status_code == 401
        c.post("/api/auth/login", json={"username": "pat-admin", "password": "correct horse battery"})
        assert c.post("/mcp", json=body).status_code == 401    # a browser's sign-in isn't enough
        token = key(con, people["admin"], ["read"])
        h = {"Authorization": f"Bearer {token}"}
        assert c.post("/mcp", json=body, headers={**h, "Origin": "http://evil.example"}).status_code == 403
        assert c.get("/mcp", headers=h).status_code == 405
        assert c.post("/mcp", content=b"{", headers={**h, "Content-Type": "application/json"}).status_code == 400
        batch = c.post("/mcp", json=[body, {**body, "id": 2}], headers=h).json()
        assert [x["id"] for x in batch] == [1, 2]


def test_crud_through_tools(app, con, people, add_doc, tmp_path):
    doc = add_doc(name="water.pdf", title="Water bill", content="City Water Department amount due 61.20",
                  doc_date="2026-11-01")
    with TestClient(app) as c:
        token = key(con, people["member"], ["read", "create", "update", "delete"])
        # create
        tag = call(c, token, "create_label", kind="tag", name="Utilities")
        assert tag["name"] == "Utilities"
        assert call(c, token, "add_note", document_id=doc, text="paid by card")["ok"]
        pdf = text_pdf(tmp_path / "new.pdf")
        up = call(c, token, "add_document", filename="new.pdf",
                  content_base64=base64.b64encode(pdf.read_bytes()).decode(), title="New letter")
        assert up["uploads"][0]["outcome"] == "accepted"
        assert "error" in call(c, token, "add_document", filename="x.pdf", content_base64="***")
        # read
        found = call(c, token, "search_documents", query="water")
        assert found["total"] >= 1 and found["documents"][0]["id"] == doc
        d = call(c, token, "get_document", document_id=doc, max_chars=100)
        assert d["title"] == "Water bill" and "City Water" in d["text"] and d["notes"][0]["text"] == "paid by card"
        assert {x["name"] for x in call(c, token, "list_labels", kind="tag")} == {"Utilities"}
        assert "error" in call(c, token, "get_document", document_id=99999)
        # update
        changed = call(c, token, "update_document", document_id=doc, title="Water bill November",
                       add_tag_ids=[tag["id"]])
        assert changed["title"] == "Water bill November" and changed["tags"] == ["Utilities"]
        assert call(c, token, "update_document", document_id=doc, remove_tag_ids=[tag["id"]])["tags"] == []
        assert "error" in call(c, token, "update_document", document_id=doc)
        assert call(c, token, "rename_label", kind="tag", label_id=tag["id"], name="Bills")["name"] == "Bills"
        # delete (to the trash) and back
        assert call(c, token, "delete_document", document_id=doc)["ok"]
        assert call(c, token, "search_documents", query="water")["total"] == 0
        assert call(c, token, "search_documents", query="water", in_trash=True)["total"] == 1
        assert call(c, token, "restore_document", document_id=doc)["ok"]
        assert call(c, token, "search_documents", query="water")["total"] == 1
        assert call(c, token, "delete_label", kind="tag", label_id=tag["id"])["ok"]


def test_ask_tool_without_ai(app, con, people, add_doc):
    add_doc(name="ins.pdf", title="Auto insurance card", content="Example Mutual policy number EM-123456 expires 2026-12-29")
    with TestClient(app) as c:
        token = key(con, people["readonly"], ["read"])
        r = call(c, token, "ask", question="What is my policy number?", use_ai=False)
        assert "answer" in r and isinstance(r["sources"], list)
