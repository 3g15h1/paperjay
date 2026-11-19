"""The phone tab bar (0.5.4): Ask, Library, +, Review, More, with + in the middle; Browse lives
inside the Library, and the Ask box is the one search box on a phone (no floating search button)."""
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "paperjay" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def tabs():
    bar = re.search(r'<nav class="tabs"[^>]*>(.*?)</nav>', HTML, re.S).group(1)
    return re.findall(r'<a href="([^"]+)"[^>]*>', bar)


def test_plus_is_in_the_middle_of_the_tab_bar():
    hrefs = tabs()
    assert hrefs == ["/", "/library", "/upload", "/review", "/settings"]
    assert len(hrefs) % 2 == 1 and hrefs[len(hrefs) // 2] == "/upload"


def test_tab_bar_sizes_itself_to_the_tabs_shown():
    # a fixed column count leaves a gap (and moves + off centre) when a tab is hidden,
    # e.g. + for a read-only account
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    rule = re.search(r"\.tabs \{ display: grid;[^}]*\}", css).group(0)
    assert "grid-auto-flow: column" in rule and "repeat(" not in rule


def test_browse_is_under_the_library():
    assert 'href="/browse"' not in HTML
    browse = (STATIC / "js" / "browse.js").read_text(encoding="utf-8")
    library = (STATIC / "js" / "library.js").read_text(encoding="utf-8")
    assert 'export const nav = "library"' in browse
    assert 'libSwitch("browse")' in browse and 'libSwitch("library")' in library


def test_ask_box_is_the_search_box():
    assert "search-fab" not in HTML and "m-search-input" not in HTML
    home = (STATIC / "js" / "home.js").read_text(encoding="utf-8")
    assert 'from "./search.js"' in home and "data-searchbox" in home


def test_tab_bar_style_raised_round_add_button():
    # 0.6.1: active tab in the accent colour; + is a raised round button with a label under it
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".tabs a.on { color: var(--accent); }" in css
    plus = re.search(r"\.tabs \.add span\.plus \{[^}]*\}", css).group(0)
    assert "border-radius: 50%" in plus and "margin-top: -" in plus and "box-shadow" in plus
    bar = re.search(r'<nav class="tabs"[^>]*>(.*?)</nav>', HTML, re.S).group(1)
    assert re.search(r'class="add write-only">.*?</span>Add</a>', bar)
