"""Settings is a list of sections; each one opens on its own page (/settings/<key>) with a back button."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from paperjay.app import create_app
from paperjay.config import Config

JS = Path(__file__).parent.parent / "paperjay" / "static" / "js"
SETTINGS = (JS / "settings.js").read_text(encoding="utf-8")
KEYS = re.findall(r'\{ key: "(\w+)"', SETTINGS)


def test_section_pages_are_served(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path, consume_dir=None), start_worker=False)) as client:
        for path in ["/settings", "/settings/ai", "/settings/account"]:
            r = client.get(path)
            assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
    assert 'route("/settings/:section"' in (JS / "app.js").read_text(encoding="utf-8")


def test_every_section_has_a_page_and_its_setup_runs_only_there():
    assert len(KEYS) == len(set(KEYS)) and "account" in KEYS and "ai" in KEYS
    html = SETTINGS[SETTINGS.index("const HTML = {"):SETTINGS.index("const meta = SECTIONS")]
    for key in KEYS:
        assert re.search(rf"^\s+{key}: \(\) =>", html, re.M), f"no page for {key}"
        if key == "email":
            assert 'sec === "email" ? mailSetup' in SETTINGS
        else:
            assert f'sec === "{key}"' in SETTINGS, f"setup for {key} isn't limited to its page"


def test_section_pages_and_sub_pages_have_a_back_button():
    assert '<a class="btn ghost sm" href="/settings">← Settings</a>' in SETTINGS
    for name in ["secure.js", "labels.js", "trash.js"]:
        assert 'href="/settings">← Settings</a>' in (JS / name).read_text(encoding="utf-8"), name


def test_old_links_to_a_spot_on_the_long_page_still_land():
    # home.js linked /settings#calendar before the sections had their own pages
    assert 'href="/settings/account#calendar"' in (JS / "home.js").read_text(encoding="utf-8")
    assert re.search(r"OLD_ANCHORS = \{[^}]*calendar: \"account\"", SETTINGS)


def test_overview_groups_every_section_once_with_a_colour():
    # 0.6.2: grouped cards like a phone's settings; every section (except the account card) in exactly one group
    groups = re.search(r"const GROUPS = \[(.*?)\n\];", SETTINGS, re.S).group(1)
    ids = re.findall(r'"([/\w]+)"', re.sub(r'title: "[^"]*"', "", groups))
    assert len(ids) == len(set(ids))
    assert set(KEYS) - {"account"} <= set(ids) and {"/labels", "/trash", "/secure"} <= set(ids)
    for key in KEYS:
        assert re.search(rf'\{{ key: "{key}", color: "(blue|green|amber|red|purple|teal|pink|orange)"', SETTINGS), key
    css = (JS.parent / "app.css").read_text(encoding="utf-8")
    for colour in ("purple", "teal", "pink", "orange"):
        assert css.count(f"--{colour}:") == 3, colour   # dark, light, and light by system setting
