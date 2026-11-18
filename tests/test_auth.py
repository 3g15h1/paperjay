"""Accounts, sign-in and permissions."""
import pytest
from fastapi.testclient import TestClient

from paperjay import auth, db
from paperjay.app import create_app
from paperjay.config import Config


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "THROTTLE", auth.Throttle())
    return create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)


def client(app, **kw):
    return TestClient(app, follow_redirects=False, **kw)


def test_first_run_setup(app):
    with client(app) as c:
        assert c.get("/library").headers["location"] == "/setup"
        assert c.get("/api/documents").json()["setup"] is True
        assert c.get("/api/auth/status").json() == {"setup_needed": True, "user": None}
        assert c.post("/api/auth/setup", json={"username": "Sam", "password": "short"}).status_code == 400
        r = c.post("/api/auth/setup", json={"username": "Sam", "password": "a good password", "display_name": "Sam"})
        assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
        cookie = r.cookies.get(auth.COOKIE)
        assert cookie and "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
        assert c.get("/api/documents").status_code == 200           # signed in by setup
        assert c.post("/api/auth/setup", json={"username": "x", "password": "another password"}).status_code == 409


def test_sign_in_out_and_redirects(app):
    with client(app) as c:
        c.post("/api/auth/setup", json={"username": "sam", "password": "a good password"})
        c.post("/api/auth/logout")
        assert c.get("/api/documents").status_code == 401
        assert c.get("/documents/3?x=1").headers["location"] == "/login?next=/documents/3?x=1"
        assert c.get("/login").status_code == 200 and c.get("/static/app.css").status_code == 200
        assert c.post("/api/auth/login", json={"username": "sam", "password": "nope nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "SAM ", "password": "a good password"}).status_code == 200
        assert c.get("/api/auth/status").json()["user"]["username"] == "sam"


def test_repeated_wrong_passwords_must_wait(app):
    with client(app) as c:
        c.post("/api/auth/setup", json={"username": "sam", "password": "a good password"})
        for _ in range(5):
            assert c.post("/api/auth/login", json={"username": "sam", "password": "guess guess"}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "sam", "password": "a good password"})
        assert r.status_code == 429 and "try again" in r.json()["detail"]
        # unknown names fail the same way (no hint which names exist)
        assert c.post("/api/auth/login", json={"username": "ghost", "password": "x" * 9}).json()["detail"] == "wrong name or password"


def _person(app, role):
    con = db.connect(app.state.config.db_path)
    u = auth.create_user(con, f"p-{role}", "a good password", role)
    _, token = auth.create_token(con, u.id, "t")
    con.close()
    return {"Authorization": f"Token {token}"}


def test_roles(app):
    with client(app) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "a good password"})
        ro, member = _person(app, "readonly"), _person(app, "member")
        c.cookies.clear()
        assert c.get("/api/documents", headers=ro).status_code == 200
        assert c.post("/api/ask", json={"q": "x"}, headers=ro).status_code == 200
        assert c.post("/api/labels/tag", json={"name": "x"}, headers=ro).status_code == 403
        assert c.post("/api/documents", files={"file": ("a.pdf", b"%PDF-1.4 a")}, headers=ro).status_code == 403
        assert c.post("/api/labels/tag", json={"name": "x"}, headers=member).status_code == 200
        assert c.put("/api/settings", json={"ai_provider": "off"}, headers=member).status_code == 403
        assert c.get("/api/settings", headers=member).status_code == 200
        assert c.get("/api/users", headers=member).status_code == 403
        assert c.delete("/api/documents/1", headers=member).status_code == 403
        assert c.post("/api/trash/empty", headers=member).status_code == 403


def test_cross_site_browser_writes_are_refused(app):
    with client(app) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "a good password"})
        evil = {"Origin": "http://evil.example"}
        assert c.post("/api/labels/tag", json={"name": "x"}, headers=evil).status_code == 403
        assert c.post("/api/labels/tag", json={"name": "x"}, headers={"Origin": "http://testserver"}).status_code == 200


def test_tokens_paperless_style_and_calendar_scope(app):
    with client(app) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "a good password"})
        full = c.post("/api/auth/tokens", json={"name": "iPhone Shortcut"}).json()
        cal = c.post("/api/auth/tokens", json={"name": "Calendar", "scope": "calendar"}).json()
        assert full["token"].startswith("pj_") and [t["name"] for t in c.get("/api/auth/tokens").json()] == ["iPhone Shortcut", "Calendar"]
        c.cookies.clear()
        r = c.post("/api/documents/post_document/", files={"document": ("a.pdf", b"%PDF-1.4 shortcut")},
                   headers={"Authorization": f"Token {full['token']}"})
        assert r.status_code == 200 and isinstance(r.json(), str)
        assert c.get(f"/calendar.ics?token={cal['token']}").status_code == 200
        assert c.get("/api/documents", headers={"Authorization": f"Bearer {cal['token']}"}).status_code == 403
        assert c.get("/calendar.ics?token=pj_wrong").status_code == 401
        assert c.get("/api/documents", headers={"Authorization": "Token pj_wrong"}).status_code == 401


def test_people_and_last_admin(app):
    with client(app) as c:
        me = c.post("/api/auth/setup", json={"username": "admin", "password": "a good password"}).json()["user"]
        kid = c.post("/api/users", json={"username": "Kid", "password": "kids password", "role": "readonly"}).json()
        assert kid["username"] == "kid" and kid["role"] == "readonly"
        assert c.post("/api/users", json={"username": "kid", "password": "kids password"}).status_code == 400
        assert c.patch(f"/api/users/{me['id']}", json={"role": "member"}).status_code == 400   # last admin
        assert c.delete(f"/api/users/{me['id']}").status_code == 400
        with client(app) as k:
            assert k.post("/api/auth/login", json={"username": "kid", "password": "kids password"}).status_code == 200
            c.patch(f"/api/users/{kid['id']}", json={"disabled": True})
            assert k.get("/api/documents").status_code == 401                               # signed out at once
        assert c.delete(f"/api/users/{kid['id']}").json() == {"ok": True}


def test_password_change_signs_out_other_devices(app):
    with client(app) as a, client(app) as b:
        a.post("/api/auth/setup", json={"username": "admin", "password": "a good password"})
        b.post("/api/auth/login", json={"username": "admin", "password": "a good password"})
        assert a.post("/api/auth/password", json={"current": "wrong", "new": "x" * 10}).status_code == 400
        assert a.post("/api/auth/password", json={"current": "a good password", "new": "a better password"}).status_code == 200
        assert a.get("/api/documents").status_code == 200 and b.get("/api/documents").status_code == 401


def test_security_headers(app):
    with client(app) as c:
        r = c.get("/login")
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_password_hashes():
    h = auth.hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct" not in h
    assert auth.check_password("correct horse", h) and not auth.check_password("wrong", h)
    assert not auth.check_password("x", None) and not auth.check_password("x", "md5$abc")
