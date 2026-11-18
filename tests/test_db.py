import sqlite3

import pytest

from paperjay import db


def test_fresh_database_gets_latest_schema_without_backup(tmp_path):
    v = db.migrate(tmp_path / "p.db", tmp_path / "backups")
    assert v == db.available_migrations()[-1][0]
    assert not (tmp_path / "backups").exists()
    con = db.connect(tmp_path / "p.db")
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(tmp_path):
    a = db.migrate(tmp_path / "p.db", tmp_path / "b")
    assert db.migrate(tmp_path / "p.db", tmp_path / "b") == a


def test_upgrade_backs_up_first(tmp_path):
    base = db.available_migrations()
    db.migrate(tmp_path / "p.db", tmp_path / "b", base)
    con = db.connect(tmp_path / "p.db")
    db.set_setting(con, "marker", "before")
    con.close()
    nxt = len(base) + 1
    v = db.migrate(tmp_path / "p.db", tmp_path / "b",
                   base + [(nxt, f"{nxt:04d}_x.sql", "CREATE TABLE extra (x INTEGER);")])
    assert v == nxt
    backups = list((tmp_path / "b").glob("paperjay-v*.db"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    assert old.execute("PRAGMA user_version").fetchone()[0] == len(base)
    assert old.execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0] == "before"


def test_failed_migration_changes_nothing(tmp_path):
    base = db.available_migrations()
    db.migrate(tmp_path / "p.db", tmp_path / "b", base)
    nxt = len(base) + 1
    bad = (nxt, f"{nxt:04d}_bad.sql", "CREATE TABLE half (x INTEGER); INSERT INTO nowhere VALUES (1);")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(tmp_path / "p.db", tmp_path / "b", base + [bad])
    con = db.connect(tmp_path / "p.db")
    assert db.schema_version(con) == len(base)
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone()


def test_refuses_database_from_newer_version(tmp_path):
    db.migrate(tmp_path / "p.db", tmp_path / "b")
    con = db.connect(tmp_path / "p.db")
    con.execute("PRAGMA user_version = 999")
    con.close()
    with pytest.raises(db.SchemaTooNew):
        db.migrate(tmp_path / "p.db", tmp_path / "b")


def test_transaction_rolls_back_on_error(con):
    with pytest.raises(RuntimeError):
        with db.transaction(con):
            db.set_setting(con, "k", "v")
            raise RuntimeError("boom")
    assert db.get_setting(con, "k") is None
