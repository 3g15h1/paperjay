"""The household's own fields: a policy number, a licence plate, a member number… on any document.

A field has a name, a kind (text, number, money, date) and may fill itself from what PaperJay read in the
document (its fact card): the number whose label has some words ("policy" finds "Policy number"), one of its
dates ("expires"), or its total. What a person types always wins and is stored; a filled value is worked
out each time it's shown, so it follows the document's text and needs no upkeep. A person can also clear a
filled value on purpose (stored as ''). Stored values are searchable (they join the search index).
"""
import re
import sqlite3
from datetime import date

from .db import transaction

KINDS = ("text", "number", "money", "date")
MAX_FIELDS = 50


def _clean_fill(fill: str) -> str:
    fill = " ".join(str(fill or "").split()).lower()
    if not fill:
        return ""
    if fill == "total":
        return fill
    kind, _, words = fill.partition(":")
    if kind in ("id", "date") and words.strip():
        return f"{kind}:{words.strip()[:40]}"
    raise ValueError("fill is '', 'total', 'id:<words in its label>' or 'date:<label>'")


def all_fields(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, name, kind, fill, position FROM custom_fields "
                                         "ORDER BY position, id")]


def create(con: sqlite3.Connection, name: str, kind: str = "text", fill: str = "") -> dict:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("give the field a name")
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    fill = _clean_fill(fill)
    if con.execute("SELECT COUNT(*) FROM custom_fields").fetchone()[0] >= MAX_FIELDS:
        raise ValueError(f"at most {MAX_FIELDS} fields")
    if con.execute("SELECT 1 FROM custom_fields WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"there is already a field called {name!r}")
    with transaction(con):
        pos = (con.execute("SELECT MAX(position) FROM custom_fields").fetchone()[0] or 0) + 1
        cur = con.execute("INSERT INTO custom_fields(name, kind, fill, position) VALUES (?, ?, ?, ?)",
                          (name, kind, fill, pos))
    return get(con, cur.lastrowid)


def get(con: sqlite3.Connection, field_id: int) -> dict | None:
    r = con.execute("SELECT id, name, kind, fill, position FROM custom_fields WHERE id = ?", (field_id,)).fetchone()
    return dict(r) if r else None


def update(con: sqlite3.Connection, field_id: int, changes: dict) -> dict:
    f = get(con, field_id)
    if not f:
        raise KeyError(field_id)
    sets = {}
    if "name" in changes:
        name = " ".join(str(changes["name"] or "").split())[:60]
        if not name:
            raise ValueError("give the field a name")
        clash = con.execute("SELECT 1 FROM custom_fields WHERE name = ? AND id != ?", (name, field_id)).fetchone()
        if clash:
            raise ValueError(f"there is already a field called {name!r}")
        sets["name"] = name
    if "fill" in changes:
        sets["fill"] = _clean_fill(changes["fill"])
    if "position" in changes:
        sets["position"] = int(changes["position"])
    if sets:
        with transaction(con):
            con.execute(f"UPDATE custom_fields SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?",
                        (*sets.values(), field_id))
    return get(con, field_id)


def delete(con: sqlite3.Connection, field_id: int) -> bool:
    with transaction(con):
        ids = [r[0] for r in con.execute("SELECT document_id FROM document_fields WHERE field_id = ?", (field_id,))]
        gone = con.execute("DELETE FROM custom_fields WHERE id = ?", (field_id,)).rowcount > 0
    _reindex(con, ids)
    return gone


def check_value(kind: str, value: str) -> str:
    v = " ".join(str(value).split())[:200]
    if not v:
        return ""
    if kind == "number" and not re.fullmatch(r"-?\d+(?:[.,]\d+)?", v):
        raise ValueError(f"{v!r} isn't a number")
    if kind == "money":
        m = re.fullmatch(r"[^\d-]{0,3}(-?[\d,]*\d(?:\.\d{1,2})?)", v)
        if not m:
            raise ValueError(f"{v!r} isn't an amount")
        v = f"{float(m.group(1).replace(',', '')):.2f}"
    if kind == "date":
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"{v!r} isn't a date (YYYY-MM-DD)")
    return v


def filled(field: dict, card: dict | None) -> str | None:
    """The value a field fills itself with from a fact card, or None."""
    fill, card = field["fill"], card or {}
    if not fill:
        return None
    if fill == "total":
        return f"{card['total']:.2f}" if card.get("total") is not None else None
    kind, _, words = fill.partition(":")
    want = words.split()
    if kind == "id":
        for i in card.get("ids") or []:
            label = (i.get("label") or "").lower()
            if all(w in label for w in want):
                return i["value"]
    if kind == "date":
        found = [d["date"] for d in card.get("dates") or [] if (d.get("label") or "").lower() == words]
        return max(found) if found else None
    return None


def for_document(con: sqlite3.Connection, doc_id: int, card: dict | None) -> list[dict]:
    """Every field with this document's value: {id, name, kind, value, source: person|filled|none}."""
    own = {r[0]: r[1] for r in con.execute("SELECT field_id, value FROM document_fields WHERE document_id = ?",
                                           (doc_id,))}
    out = []
    for f in all_fields(con):
        if f["id"] in own:
            out.append({**f, "value": own[f["id"]] or None, "source": "person" if own[f["id"]] else "cleared"})
        else:
            v = filled(f, card)
            out.append({**f, "value": v, "source": "filled" if v is not None else "none"})
    return out


def set_value(con: sqlite3.Connection, doc_id: int, field_id: int, value, user_id=None) -> None:
    """value: text to keep; '' = no value (even if one would be filled); None = back to filling itself."""
    f = get(con, field_id)
    if not f:
        raise KeyError(field_id)
    with transaction(con):
        if value is None:
            con.execute("DELETE FROM document_fields WHERE document_id = ? AND field_id = ?", (doc_id, field_id))
        else:
            v = check_value(f["kind"], value)
            con.execute("INSERT INTO document_fields(document_id, field_id, value) VALUES (?, ?, ?) "
                        "ON CONFLICT(document_id, field_id) DO UPDATE SET value = excluded.value",
                        (doc_id, field_id, v))
        con.execute("INSERT INTO audit_log(user_id, action, document_id, detail) VALUES (?, 'field', ?, json_object("
                    "'field', ?, 'value', ?))", (user_id, doc_id, f["name"], value))
    _reindex(con, [doc_id])


def stored_text(con: sqlite3.Connection, doc_id: int) -> list[str]:
    """What the search index gets: "Licence plate ABC 123"."""
    return [f"{r[0]} {r[1]}" for r in con.execute(
        "SELECT f.name, v.value FROM document_fields v JOIN custom_fields f ON f.id = v.field_id "
        "WHERE v.document_id = ? AND v.value != ''", (doc_id,))]


def _reindex(con, ids) -> None:
    from .library import Library
    lib = Library(con)
    with transaction(con):
        for doc_id in set(ids):
            lib._reindex(doc_id)
