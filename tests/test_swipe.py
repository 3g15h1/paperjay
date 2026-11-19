"""Swipe left to delete in the Library (0.5.5): every card is wrapped with its own Delete button,
the swipe is off while selecting and for read-only accounts, and Delete moves to the trash with Undo."""
from pathlib import Path

JS = Path(__file__).parent.parent / "paperjay" / "static" / "js"


def test_library_cards_are_swipeable():
    lib = (JS / "library.js").read_text(encoding="utf-8")
    assert "swipeWrap(d.id, docCard(" in lib
    assert "swipeToDelete(els.list" in lib and "swiper.detach()" in lib


def test_swipe_is_off_while_selecting_and_for_read_only():
    lib = (JS / "library.js").read_text(encoding="utf-8")
    assert '!selecting && !document.body.classList.contains("readonly")' in lib


def test_delete_goes_to_the_trash_with_undo():
    lib = (JS / "library.js").read_text(encoding="utf-8")
    start = lib.index("onDelete:")
    block = lib[start:lib.index("let timer;", start)]
    assert 'action: "trash"' in block and 'action: "restore"' in block and '"Undo"' in block


def test_swipe_never_opens_the_document():
    sw = (JS / "swipe.js").read_text(encoding="utf-8")
    assert "swallowClick = true" in sw and '"click"' in sw and "}, true);" in sw  # capture-phase click guard
    assert "passive: false" in sw   # touchmove can stop the page scrolling sideways



def test_delete_button_is_a_trash_icon_with_a_name():
    sw = (JS / "swipe.js").read_text(encoding="utf-8")
    assert 'icon("trash")' in sw and 'aria-label="Delete"' in sw   # icon only, still named for screen readers
    html = (JS.parent / "index.html").read_text(encoding="utf-8")
    assert 'id="i-trash"' in html


def test_full_swipe_deletes_and_needs_a_long_swipe():
    sw = (JS / "swipe.js").read_text(encoding="utf-8")
    assert 'classList.contains("armed")' in sw and "onDelete(+row.dataset.id, row)" in sw
    # must be well past the button: node evaluates fullAt for a phone row and a narrow grid card
    import re, shutil, subprocess
    if not shutil.which("node"):
        return
    open_px = int(re.search(r"const OPEN = (\d+)", sw).group(1))
    fn = re.search(r"export const fullAt = (.*?);\s", sw).group(1)
    out = subprocess.run(["node", "-e", f"const OPEN={open_px}; const f={fn}; console.log(f(360), f(170))"],
                         capture_output=True, text=True).stdout.split()
    row, card = map(float, out)
    assert row >= 360 * 0.6 and card >= open_px + 60 and card < 170   # reachable, but never by accident


def test_delete_button_matches_the_failed_chip():
    css = (JS.parent / "app.css").read_text(encoding="utf-8")
    rule = css[css.index(".swipe-del {"):css.index(".swipe-del svg.i")]
    assert "var(--bad-soft)" in rule and "color: var(--bad)" in rule
