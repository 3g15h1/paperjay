"""Migration 0008 (spec section 5): the A–Z index table, paper numbers and paper
reservations; paper_no and category become filing fields a person can set."""
import sqlite3

import pytest

from paperjay import db
from paperjay.library import FILING_FIELDS, Library


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_fresh_database_has_0008_schema(con):
    assert db.schema_version(con) == db.available_migrations()[-1][0] >= 8
    assert {"index_terms", "paper_reservations"} <= _tables(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
    assert "paper_no" in cols
    idx = {r[1] for r in con.execute("PRAGMA index_list(documents)")}
    assert "documents_paper_no" in idx
    term_idx = {r[1] for r in con.execute("PRAGMA index_list(index_terms)")}
    assert "index_terms_doc" in term_idx


def test_upgrade_from_0007_keeps_data_and_backs_up(tmp_path):
    migs = db.available_migrations()
    assert migs[7][0] == 8 and migs[6][0] == 7
    v7 = [m for m in migs if m[0] <= 7]
    path = tmp_path / "p.db"
    assert db.migrate(path, tmp_path / "b", v7) == 7
    con = db.connect(path)
    # written as a v7 install had it: today's Library code expects today's schema (0.7.8 indexes
    # the household's own fields, a table v7 doesn't have)
    with db.transaction(con):
        doc_id = con.execute(
            "INSERT INTO documents(title, checksum, original_name, original_ext, mime_type, size, content, status) "
            "VALUES ('Water bill', ?, 'bill.pdf', '.pdf', 'application/pdf', 3, 'City Water. Account No: 1234.', "
            "'ready')", ("a" * 64,)).lastrowid
    con.close()

    assert db.migrate(path, tmp_path / "b") == migs[-1][0]
    backups = list((tmp_path / "b").glob("paperjay-v7-*.db"))
    assert len(backups) == 1  # the pre-migration copy, the rollback shape
    old = sqlite3.connect(backups[0])
    assert old.execute("PRAGMA user_version").fetchone()[0] == 7
    old.close()
    con = db.connect(path)
    try:
        assert Library(con).get(doc_id)["title"] == "Water bill"
        assert _tables(con) >= {"index_terms", "paper_reservations"}
    finally:
        con.close()


def test_paper_no_unique_only_when_set(con, add_doc):
    a = add_doc(data=b"first document")
    b = add_doc(data=b"second document")
    lib = Library(con)
    lib.update(a, paper_no=124)
    lib.update(b, paper_no=125)
    with pytest.raises(ValueError):
        lib.update(b, paper_no=124)     # another document's number
    lib.update(a, paper_no=None)        # clearing is always fine
    lib.update(b, paper_no=124)         # …and now it is free
    assert lib.get(b)["paper_no"] == 124
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction(con):
            con.execute("UPDATE documents SET paper_no = 124 WHERE id = ?", (a,))


def test_paper_no_and_category_are_person_filing_fields(con, add_doc):
    assert {"paper_no", "category"} <= FILING_FIELDS
    from paperjay import auth
    user = auth.create_user(con, "sam", "correct horse battery", "member")
    doc_id = add_doc()
    lib = Library(con)
    lib.update(doc_id, user_id=user.id, paper_no=57, category="medical")
    doc = lib.get(doc_id)
    assert doc["paper_no"] == 57 and doc["category"] == "medical"
    # a person's values are recorded like any other filing field (audit "update")
    assert {"paper_no", "category"} <= lib.person_fields(doc_id)
    actions = [h["action"] for h in lib.history(doc_id)]
    assert "update" in actions


def test_paper_no_must_be_a_positive_whole_number(con, add_doc):
    lib = Library(con)
    doc_id = add_doc()
    for bad in (0, -3, "12", 2.5, True):
        with pytest.raises(ValueError):
            lib.update(doc_id, paper_no=bad)
