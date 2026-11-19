"""Two-step sign-in (0.8.0): TOTP codes, the sign-in flow, recovery codes, turning it on and off, an admin's
reset and the command line."""
import base64

import pytest
from fastapi.testclient import TestClient

from paperjay import auth, db, totp
from paperjay.app import create_app
from paperjay.config import Config


def test_rfc_6238_vectors():
    s = base64.b32encode(b"12345678901234567890").decode()
    assert [totp.code_now(s, t) for t in (59, 1111111109, 1234567890, 2000000000)] == \
        ["287082", "081804", "005924", "279037"]


def test_window_and_replay():
    s = totp.new_secret()
    now = 1_800_000_000
    step = now // 30
    assert totp.verify(s, totp.code_now(s, now), at=now) == step
    assert totp.verify(s, totp.code_now(s, now - 30), at=now) == step - 1      # a little clock drift
    assert totp.verify(s, totp.code_now(s, now - 90), at=now) is None
    assert totp.verify(s, totp.code_now(s, now), last_step=step, at=now) is None   # used once
    assert totp.verify(s, "12 34", at=now) is None
    assert totp.uri(s, "alex").startswith("otpauth://totp/PaperJay:alex?secret=")


def _client(tmp_path):
    return TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False))


def _turn_on(c, password="correct horse battery"):
    r = c.post("/api/auth/2fa/setup", json={"password": password})
    assert r.status_code == 200 and r.json()["qr"].lstrip().startswith(("<?xml", "<svg"))
    secret = r.json()["secret"]
    codes = c.post("/api/auth/2fa/confirm", json={"code": totp.code_now(secret)}).json()["recovery_codes"]
    return secret, codes


def _login(c, **body):
    return c.post("/api/auth/login", json={"username": "alex", "password": "correct horse battery", **body})


def test_sign_in_flow(tmp_path, monkeypatch):
    with _client(tmp_path) as c:
        con = db.connect(tmp_path / "paperjay.db")
        auth.create_user(con, "alex", "correct horse battery", "admin")
        con.close()
        assert _login(c).status_code == 200                  # off: the password is enough
        assert c.post("/api/auth/2fa/setup", json={"password": "wrong"}).status_code == 400
        assert c.post("/api/auth/2fa/confirm", json={"code": "000000"}).status_code == 400
        secret, codes = _turn_on(c)
        assert len(codes) == 10 and c.get("/api/auth/status").json()["user"]["two_step"] is True
        assert c.get("/api/auth/2fa").json() == {"enabled": True, "recovery_left": 10}

        fresh = _client(tmp_path).__enter__()
        r = _login(fresh)
        assert r.status_code == 401 and r.json()["code_needed"] is True and "pj_session" not in r.cookies
        assert _login(fresh, code="000000").status_code == 401
        later = __import__("time").time() + 30     # the next code (the one used to turn it on can't be reused)
        assert _login(fresh, code=totp.code_now(secret, later)).status_code == 200
        assert _login(_client(tmp_path).__enter__(), code=totp.code_now(secret, later)).status_code == 401  # replay
        assert _login(_client(tmp_path).__enter__(), code=codes[0].upper()).status_code == 200            # recovery
        assert _login(_client(tmp_path).__enter__(), code=codes[0]).status_code == 401                    # once
        assert c.get("/api/auth/2fa").json()["recovery_left"] == 9
        assert _login(_client(tmp_path).__enter__(), password="nope", code=codes[1]).status_code == 401
        assert c.get("/api/auth/2fa").json()["recovery_left"] == 9   # a wrong password never spends a code

        new = c.post("/api/auth/2fa/recovery", json={"password": "correct horse battery"}).json()["recovery_codes"]
        assert _login(_client(tmp_path).__enter__(), code=codes[1]).status_code == 401   # old codes stop working
        assert _login(_client(tmp_path).__enter__(), code=new[0]).status_code == 200

        # changing the password keeps this device signed in without asking for a code
        assert c.post("/api/auth/password", json={"current": "correct horse battery",
                                                  "new": "another long password"}).status_code == 200
        assert c.get("/api/auth/status").json()["user"]["username"] == "alex"
        assert c.post("/api/auth/2fa/disable", json={"password": "another long password"}).json() == {"enabled": False}
        assert c.post("/api/auth/login", json={"username": "alex", "password": "another long password"}).status_code == 200


def test_wrong_codes_count_towards_the_throttle(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "THROTTLE", auth.Throttle(free=2))
    with _client(tmp_path) as c:
        con = db.connect(tmp_path / "paperjay.db")
        auth.create_user(con, "alex", "correct horse battery", "admin")
        con.close()
        _login(c)
        _turn_on(c)
        other = _client(tmp_path).__enter__()
        for _ in range(2):
            assert _login(other, code="000000").status_code == 401
        assert _login(other, code="000000").status_code == 429


def test_keys_admin_reset_and_command_line(tmp_path, capsys):
    from paperjay.__main__ import main
    with _client(tmp_path) as c:
        con = db.connect(tmp_path / "paperjay.db")
        admin = auth.create_user(con, "root", "correct horse battery", "admin")
        auth.create_user(con, "alex", "correct horse battery", "readonly")
        _, key = auth.create_token(con, admin.id, "script")
        con.close()
        _login(c)
        secret, _ = _turn_on(c)                              # read-only people can protect their account too
        c.headers["Authorization"] = f"Token {key}"           # a key still works, and can't change two-step
        assert c.get("/api/documents").status_code == 200
        assert c.post("/api/auth/2fa/setup", json={"password": "correct horse battery"}).status_code == 403
        users = {u["username"]: u for u in c.get("/api/users").json()}
        assert users["alex"]["two_step"] and not users["root"]["two_step"]
        assert c.patch(f"/api/users/{users['alex']['id']}", json={"reset_two_step": True}).status_code == 200
        assert _login(_client(tmp_path).__enter__()).status_code == 200    # the lost phone: password alone
        del c.headers["Authorization"]
        _login(c)
        _turn_on(c)
    import os
    os.environ["DATA_DIR"] = str(tmp_path)
    try:
        main(["reset-2fa", "alex"])
        with pytest.raises(SystemExit):
            main(["reset-2fa", "nobody"])
    finally:
        del os.environ["DATA_DIR"]
    assert "two-step sign-in is off for 'alex'" in capsys.readouterr().out
    con = db.connect(tmp_path / "paperjay.db")
    assert auth.two_step(con, 2)["enabled"] is False
