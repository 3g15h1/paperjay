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
