from datetime import date

import pytest

from paperjay import filetypes
from paperjay.dates import find_date
from paperjay.intake import title_from_filename
from paperjay.processing import title_from_text

TODAY = date(2026, 10, 17)


@pytest.mark.parametrize("head,expected", [
    (b"%PDF-1.7\n", filetypes.PDF),
    (b"\r\n%PDF-1.4", filetypes.PDF),
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", filetypes.JPEG),
    (b"\x89PNG\r\n\x1a\n\x00", filetypes.PNG),
    (b"II*\x00\x08\x00", filetypes.TIFF),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", filetypes.WEBP),
    (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", filetypes.HEIC),
    (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42", None),  # a video
    (b"PK\x03\x04", None),
    (b"MZ\x90\x00", None),
    (b"<html><script>", None),
])
def test_sniff(tmp_path, head, expected):
    p = tmp_path / "f"
    p.write_bytes(head + b"\x00" * 64)
    assert filetypes.sniff(p) == expected


@pytest.mark.parametrize("text,order,expected", [
    ("Statement Date: August 14, 2025", "MDY", date(2025, 8, 14)),
    ("Date 14 Aug 2025", "MDY", date(2025, 8, 14)),
    ("Invoice 2025-08-14", "MDY", date(2025, 8, 14)),
    ("Paid 03/04/2025", "MDY", date(2025, 3, 4)),
    ("Paid 03/04/2025", "DMY", date(2025, 4, 3)),
    ("Paid 25/04/2025", "MDY", date(2025, 4, 25)),       # only one way to read it
    ("Date: 8/14/25", "MDY", date(2025, 8, 14)),
    ("Sept. 3rd, 2024 then 2025-01-01", "MDY", date(2024, 9, 3)),  # first one wins
    ("Born 1962-05-01, issued 2024-02-02", "MDY", date(2024, 2, 2)),  # too old is skipped
    ("Expires 2031-01-01", "MDY", None),                 # too far ahead
    ("Order 13/13/2025 #2025-99-99", "MDY", None),
    ("nothing here", "MDY", None),
])
def test_find_date(text, order, expected):
    assert find_date(text, order, today=TODAY) == expected


@pytest.mark.parametrize("name,title,generic", [
    ("IMG_2041.HEIC", "IMG 2041", True),
    ("PXL_20251014_101500.jpg", "PXL 20251014 101500", True),
    ("scan0003.pdf", "scan0003", True),
    ("20251014.pdf", "20251014", True),
    ("Water_bill_August.pdf", "Water bill August", False),
    ("DTE statement 2025-08.pdf", "DTE statement 2025-08", False),
])
def test_title_from_filename(name, title, generic):
    assert title_from_filename(name) == (title, generic)


def test_title_from_text():
    assert title_from_text("\n  42 \n\n ACME POWER COMPANY \nStatement") == "ACME POWER COMPANY"
    assert title_from_text("12/01/2025\n$4.00\n") is None


def test_stuck_tool_is_killed_with_its_children(tmp_path):
    import os
    import time

    from paperjay import ocr
    marker = tmp_path / "child.pid"
    started = time.time()
    with pytest.raises(TimeoutError):
        ocr.run(["sh", "-c", f"sleep 30 & echo $! > {marker}; wait"], timeout=1)
    assert time.time() - started < 10
    time.sleep(0.2)
    child = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)  # the grandchild is gone too
