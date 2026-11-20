"""Separator sheets and paper-number labels (0.7.9): finding the codes, splitting a scanned stack,
numbering from a label, and the setting."""
import io

import pikepdf
import pytest
import segno
from fpdf import FPDF

from paperjay import barcodes, db, intake, labels_pdf, ocr, settings
from paperjay.config import Config
from paperjay.library import Library
from paperjay.processing import make_consume


def page_pdf(width: int, qr: str | None = None) -> pikepdf.Pdf:
    """One page told apart by its width, with a small QR code (like a label) when asked."""
    pdf = FPDF(unit="pt", format=(width, 792))
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.text(40, 60, f"Document page {width}")
    if qr:
        buf = io.BytesIO()
        segno.make(qr, error="m").save(buf, kind="png", scale=6, border=1)
        pdf.image(io.BytesIO(buf.getvalue()), x=width - 130, y=40, w=90, h=90)
    return pikepdf.open(io.BytesIO(bytes(pdf.output())))


def stack(*pages) -> bytes:
    out = pikepdf.new()
    keep = []
    for p in pages:
        src = pikepdf.open(io.BytesIO(labels_pdf.separator("letter"))) if p == "SEP" else page_pdf(*p)
        keep.append(src)
        out.pages.extend(src.pages)
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def test_parts():
    S = barcodes.SEPARATOR
    assert barcodes.parts([[], [S], [], []]) == [[0], [2, 3]]
    assert barcodes.parts([[S], [], [S], [S], []]) == [[1], [4]]
    assert barcodes.parts([[], []]) == [[0, 1]]
    assert barcodes.paper_number(["https://pj.lan/library?paper_no=124"]) == 124
    assert barcodes.paper_number(["https://pj.lan/documents/7", "hello"]) is None


def test_codes_are_found(tmp_path):
    p = tmp_path / "s.pdf"
    p.write_bytes(stack((600,), "SEP", (610, "http://pj/library?paper_no=42")))
    codes = barcodes.page_codes(p)
    assert codes[0] == [] and codes[1] == [barcodes.SEPARATOR] and codes[2] == ["http://pj/library?paper_no=42"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    db.migrate(tmp_path / "paperjay.db", tmp_path / "backups")
    con = db.connect(tmp_path / "paperjay.db")
    from paperjay.store import FileStore
    store = FileStore(tmp_path / "media")
    (store.root / "tmp").mkdir(parents=True, exist_ok=True)
    config = Config(data_dir=tmp_path, consume_dir=None)
    read = []

    def fake_ocr(original, ftype, wd, opts):   # the codes are what's tested, not OCR
        read.append(original)
        return ocr.Result(text="text", page_count=1, archive=None, thumbnail=None, warnings=[])
    monkeypatch.setattr(ocr, "process", fake_ocr)
    consume = make_consume(config, store)

    def add(data: bytes, **kw):
        up = intake.ingest(con, store, io.BytesIO(data), "scan_0001.pdf", source="web", max_bytes=10**8, **kw)
        return up.document_id

    def run(doc_id):
        consume(con, {"document_id": doc_id, "attempts": 1, "max_attempts": 3, "payload": {}})
    yield con, store, add, run, read
    con.close()


def widths(store, lib, doc_id):
    d = lib.get(doc_id)
    with pikepdf.open(store.path("originals", d["checksum"], d["original_ext"])) as pdf:
        return [int(p.mediabox[2]) for p in pdf.pages]


def test_a_stack_is_split_at_separator_sheets(env):
    con, store, add, run, read = env
    lib = Library(con)
    tag = lib.create_label("tag", "Taxes")
    stack_id = add(stack((600,), (601,), "SEP", (610, "http://pj/library?paper_no=42"), "SEP", (620,)), tag_ids=[tag])
    run(stack_id)
    s = lib.get(stack_id)
    assert s["deleted_at"] and "Split at its separator sheets into 3 documents" in s["error"]
    assert not read                                            # the stack itself isn't read
    parts = [r[0] for r in con.execute("SELECT document_id FROM uploads WHERE source = 'split' ORDER BY rowid")]
    assert [widths(store, lib, i) for i in parts] == [[600, 601], [610], [620]]
    assert [lib.get(i)["paper_no"] for i in parts] == [None, 42, None]
    assert all([t["id"] for t in lib.get(i)["tags"]] == [tag] for i in parts)
    run(parts[1])                                              # a part is read normally, codes not looked for again
    assert lib.get(parts[1])["status"] == "ready" and len(read) == 1


def test_a_label_on_the_first_page_numbers_the_document(env):
    con, store, add, run, read = env
    lib = Library(con)
    doc_id = add(stack((600, "http://pj/library?paper_no=7"), (601,)))
    run(doc_id)
    d = lib.get(doc_id)
    assert d["paper_no"] == 7 and d["status"] == "ready" and not d["deleted_at"]
    other = add(stack((630, "http://pj/library?paper_no=7")))  # 7 is taken: left unnumbered
    run(other)
    assert lib.get(other)["paper_no"] is None


def test_the_setting_turns_it_off(env):
    con, store, add, run, read = env
    settings.save(con, {"barcodes": False})
    doc_id = add(stack((600,), "SEP", (610,)))
    run(doc_id)
    assert not Library(con).get(doc_id)["deleted_at"] and len(read) == 1


def test_separator_sheet_pdf(tmp_path):
    p = tmp_path / "sep.pdf"
    p.write_bytes(labels_pdf.separator("a4", copies=3))
    with pikepdf.open(p) as pdf:
        assert len(pdf.pages) == 3 and round(float(pdf.pages[0].mediabox[2])) == 595
    assert barcodes.page_codes(p, pages=1) == [[barcodes.SEPARATOR]]


def test_a_failed_split_takes_its_parts_back(env, monkeypatch):
    """Review before 0.8.2: a split that fails halfway leaves no parts behind and looks again on the retry."""
    con, store, add, run, read = env
    lib = Library(con)
    stack_id = add(stack((600,), "SEP", (610,), "SEP", (620,)), paper_next=True)
    real, calls = intake.ingest, []

    def flaky(*a, **kw):
        if kw.get("source") == "split" and calls:
            raise OSError("disk full")
        if kw.get("source") == "split":
            calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(intake, "ingest", flaky)
    with pytest.raises(OSError):
        run(stack_id)
    assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert lib.get(stack_id)["paper_no"] == 1 and not lib.get(stack_id)["deleted_at"]
    monkeypatch.setattr(intake, "ingest", real)
    run(stack_id)                                              # the retry splits it properly
    assert lib.get(stack_id)["deleted_at"]
    assert con.execute("SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL").fetchone()[0] == 3
