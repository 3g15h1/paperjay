"""Release checks and the Update button."""
import httpx
import pytest
from fastapi.testclient import TestClient

from paperjay import updates
from paperjay.app import create_app
from paperjay.config import Config

from conftest import sign_in_client


@pytest.fixture(autouse=True)
def github(monkeypatch):
    def handler(request):
        assert request.url.path == "/repos/3g15h1/paperjay/releases/latest"
        return httpx.Response(200, json={"tag_name": "v9.1.0", "html_url": "https://github.com/x", "body": "New things",
                                         "published_at": "2026-11-01T00:00:00Z"})
    monkeypatch.setattr(updates, "TRANSPORT", httpx.MockTransport(handler))
    updates._cache.clear()


def test_versions():
    assert updates.newer("v0.1.0", "0.1.0.dev0") and updates.newer("v0.10.0", "0.9.9")
    assert not updates.newer("v0.1.0", "0.1.0") and not updates.newer("v0.1.0", "0.2.0")


def test_check_and_request(monkeypatch, tmp_path):
    r = updates.check()
    assert r["latest"] == "v9.1.0" and r["newer"] and r["notes"] == "New things"
    monkeypatch.setenv("PAPERJAY_INSTALL", "docker")
    with pytest.raises(RuntimeError):
        updates.request("v9.1.0")
    assert "docker compose pull" in updates.instructions()
    monkeypatch.setenv("PAPERJAY_INSTALL", "lxc")
    monkeypatch.setenv("PAPERJAY_UPDATE_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        updates.request("v9.1.0; rm -rf /")
    updates.request("v9.1.0")
    assert (tmp_path / "update-request").read_text() == "v9.1.0"
    (tmp_path / "update-status.json").write_text('{"state": "ok", "tag": "v9.1.0"}')
    assert updates.status()["state"] == "ok"


def test_api_is_admin_only(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERJAY_INSTALL", "lxc")
    monkeypatch.setenv("PAPERJAY_UPDATE_DIR", str(tmp_path))
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        assert c.get("/api/update").json()["latest"] == "v9.1.0"
        assert c.post("/api/update", json={"tag": "v9.1.0"}).json()["ok"]
        sign_in_client(c, role="member")
        assert c.get("/api/update").status_code == 403


# ---- 0.5.3: token, kept answer, periodic check, notice, Update without a tag ----

def _counting(monkeypatch, status=200, tag="v9.1.0", seen=None):
    calls = []

    def handler(request):
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"message": "Not Found"})
        return httpx.Response(200, json={"tag_name": tag, "html_url": "https://github.com/x", "body": "",
                                         "published_at": "2026-11-01T00:00:00Z"})
    monkeypatch.setattr(updates, "TRANSPORT", httpx.MockTransport(handler))
    updates._cache.clear()
    return calls


def test_token_is_sent_when_readable(monkeypatch, tmp_path):
    calls = _counting(monkeypatch)
    tok = tmp_path / "github-token"
    tok.write_text("ghp_example\n")
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tok))
    updates.check(force=True)
    assert calls[-1].headers["Authorization"] == "Bearer ghp_example"
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tmp_path / "missing"))
    updates.check(force=True)
    assert "Authorization" not in calls[-1].headers


def test_private_repo_without_token_says_so(monkeypatch, tmp_path):
    _counting(monkeypatch, status=404)
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tmp_path / "missing"))
    r = updates.check(force=True)
    assert "token" in r["error"] and not r.get("newer")


def test_answer_is_kept_across_restarts_and_failures(monkeypatch, con):
    calls = _counting(monkeypatch)
    assert updates.check(force=True, con=con)["latest"] == "v9.1.0"
    updates._cache.clear()                       # a restart
    r = updates.known(con)
    assert r["latest"] == "v9.1.0" and r["newer"] and r["checked_at"]
    assert updates.check(con=con)["latest"] == "v9.1.0" and len(calls) == 1   # still fresh: GitHub not asked
    _counting(monkeypatch, status=500)
    r = updates.check(force=True, con=con)
    assert r["latest"] == "v9.1.0" and r["newer"] and "error" in r   # a failed check keeps what was found


def test_periodic_respects_the_setting_and_the_interval(monkeypatch, con):
    from paperjay import settings
    calls = _counting(monkeypatch)
    assert updates.periodic(con) is None and not calls          # tests run with UPDATE_CHECK=false
    settings.save(con, {"update_check": True})
    assert updates.periodic(con)["newer"] and len(calls) == 1
    updates.periodic(con)
    assert len(calls) == 1                                      # asked again only after CHECK_EVERY
    updates._cache["at"] -= updates.CHECK_EVERY + 1
    updates.periodic(con)
    assert len(calls) == 2


def test_update_check_env_is_a_real_boolean(monkeypatch, con):
    from paperjay import settings
    monkeypatch.setenv("UPDATE_CHECK", "false")
    assert settings.get(con, "update_check") is False
    monkeypatch.setenv("UPDATE_CHECK", "yes")
    assert settings.get(con, "update_check") is True


def test_notice_dismiss_and_next_release(monkeypatch, con, tmp_path):
    monkeypatch.setenv("PAPERJAY_UPDATE_DIR", str(tmp_path))
    _counting(monkeypatch)
    assert updates.notice(con) == {"show": False}               # never checked: nothing, and GitHub not asked
    updates.check(force=True, con=con)
    n = updates.notice(con)
    assert n["show"] and n["latest"] == "v9.1.0"
    with pytest.raises(ValueError):
        updates.dismiss(con, "../etc")
    updates.dismiss(con, "v9.1.0")
    assert not updates.notice(con)["show"]
    _counting(monkeypatch, tag="v9.2.0")
    updates.check(force=True, con=con)
    assert updates.notice(con)["show"]                          # a later release shows it again
    import json
    import time
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "running", "tag": "v9.2.0", "at": time.time()}))
    assert updates.notice(con) == {"show": False, "updating": True}
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "ok", "tag": "v9.2.0", "at": time.time()}))
    _counting(monkeypatch, tag="v0.0.1")
    updates.check(force=True, con=con)
    assert updates.notice(con) == {"show": False}               # not newer than this version


def test_request_refuses_a_second_update_while_one_runs(monkeypatch, tmp_path):
    import json
    import time
    monkeypatch.setenv("PAPERJAY_INSTALL", "lxc")
    monkeypatch.setenv("PAPERJAY_UPDATE_DIR", str(tmp_path))
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "running", "tag": "v9.1.0", "at": time.time()}))
    with pytest.raises(RuntimeError):
        updates.request("v9.1.0")
    # a "running" left behind long ago (the updater was killed) doesn't block for ever
    (tmp_path / "update-status.json").write_text(json.dumps({"state": "running", "tag": "v9.1.0", "at": 1}))
    updates.request("v9.1.0")


def test_api_update_without_tag_and_notice(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERJAY_INSTALL", "lxc")
    monkeypatch.setenv("PAPERJAY_UPDATE_DIR", str(tmp_path))
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as c:
        sign_in_client(c)
        assert c.get("/api/update/notice").json() == {"show": False}
        assert c.get("/api/update?force=true").json()["checked_at"]
        assert c.get("/api/update/notice").json()["show"]
        r = c.post("/api/update", json={})
        assert r.json()["tag"] == "v9.1.0" and (tmp_path / "update-request").read_text() == "v9.1.0"
        assert c.post("/api/update", json={"tag": "v0.0.1"}).status_code == 400   # not newer
        assert c.post("/api/update/dismiss", json={"tag": "v9.1.0"}).json()["ok"]
        assert not c.get("/api/update/notice").json()["show"]
        assert c.put("/api/settings", json={"update_check": False}).json()["update_check"] is False
        sign_in_client(c, role="member")
        assert c.get("/api/update/notice").status_code == 403
        assert c.post("/api/update/dismiss", json={"tag": "v9.1.0"}).status_code == 403
