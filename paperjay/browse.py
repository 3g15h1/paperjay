"""Browse (spec C.1): the filing cabinet. The library grouped along a path of dimensions —
category → company → year by default, or starting from type, tag, topic or year → month —
with a count at every level and an "Unfiled" bucket for documents without the field.
Backed by SQL GROUP BY over the documents table's indexed columns; the trash never shows,
and neither do documents that aren't ready yet — the counts here agree with the library
and the search suggestions, which only ever show filed documents (review 0.4.0 #4).
"""
import calendar
import sqlite3

from .library import Library

UNFILED = "__unfiled__"

# dim -> (query parameter, kind of value)
DIMS = {
    "category": ("category", "text"),
    "correspondent": ("correspondent_id", "id"),
    "year": ("year", "text"),
    "month": ("month", "text"),
    "type": ("document_type_id", "id"),
    "tag": ("tag_id", "id"),
    "topic": ("topic", "text"),
}
PATHS = {
    "category": ["category", "correspondent", "year"],
    "type": ["type", "correspondent", "year"],
    "tag": ["tag", "year"],
    "topic": ["topic", "correspondent", "year"],
    "year": ["year", "month"],
}
# where a document's value for a dim lives (scalar dims on the documents row itself)
_SCALAR = {
    "category": "d.category",
    "correspondent": "d.correspondent_id",
    "year": "substr(d.doc_date, 1, 4)",
    "month": "substr(d.doc_date, 6, 2)",
    "type": "d.document_type_id",
}
# many-to-many dims: (join table, its value column, label table, label name column)
_MANY = {
    "tag": ("document_tags", "tag_id", "tags"),
    "topic": ("document_topics", "topic", None),
}
_NAME_TABLE = {"correspondent": "correspondents", "type": "document_types", "tag": "tags"}


def resolve_path(by: str | None) -> list[str]:
    """The `by` query parameter: a start dim ("year") or a whole path ("category,correspondent,year")."""
    if not by:
        return list(PATHS["category"])
    dims = [d.strip() for d in str(by).split(",") if d.strip()]
    if not dims or any(d not in DIMS for d in dims) or len(set(dims)) != len(dims):
        raise ValueError(f"unknown browse path {by!r}")
    if len(dims) == 1 and dims[0] in PATHS:
        return list(PATHS[dims[0]])
    return dims


def _coerce(dim: str, value):
    """A selected value from the query string: UNFILED, or the dim's type. ValueError when malformed."""
    if value is None or value == "":
        return None
    if str(value) == UNFILED:
        return UNFILED
    if DIMS[dim][1] == "id":
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{DIMS[dim][0]} must be a number")
    return str(value)


def _filter(dim: str, value, where: list, params: list) -> None:
    """AND one chosen level into the WHERE clause."""
    if dim in _SCALAR:
        expr = _SCALAR[dim]
        if value == UNFILED:
            where.append(f"{expr} IS NULL")
        else:
            where.append(f"{expr} = ?")
            params.append(value)
    else:
        table, col, _ = _MANY[dim]
        if value == UNFILED:
            where.append(f"NOT EXISTS (SELECT 1 FROM {table} x WHERE x.document_id = d.id)")
        else:
            where.append(f"EXISTS (SELECT 1 FROM {table} x WHERE x.document_id = d.id AND x.{col} = ?)")
            params.append(value)


def _count(con: sqlite3.Connection, where: list, params: list) -> int:
    return con.execute(f"SELECT COUNT(*) FROM documents d WHERE {' AND '.join(where)}", params).fetchone()[0]


def _group(con: sqlite3.Connection, dim: str, where: list, params: list) -> list[dict]:
    """The buckets of one level: [{key, name, count}], Unfiled last."""
    sql_where = " AND ".join(where)
    items: list[dict] = []
    if dim in _SCALAR:
        expr = _SCALAR[dim]
        rows = con.execute(
            f"SELECT {expr} AS k, COUNT(*) AS c FROM documents d WHERE {sql_where} "
            f"GROUP BY {expr}", params).fetchall()
        names = {}
        if dim in _NAME_TABLE and any(r["k"] is not None for r in rows):
            table = _NAME_TABLE[dim]
            for r in con.execute(f"SELECT id, name FROM {table}"):
                names[r["id"]] = r["name"]
        for r in rows:
            if r["k"] is None:
                items.append({"key": UNFILED, "name": "Unfiled", "count": r["c"]})
            else:
                name = names.get(r["k"], str(r["k"]))
                if dim == "month":
                    name = calendar.month_name[int(r["k"])]
                items.append({"key": str(r["k"]), "name": name, "count": r["c"]})
    else:
        table, col, label_table = _MANY[dim]
        rows = con.execute(
            f"SELECT x.{col} AS k, COUNT(DISTINCT d.id) AS c FROM documents d "
            f"JOIN {table} x ON x.document_id = d.id WHERE {sql_where} GROUP BY x.{col}", params).fetchall()
        names = {}
        if label_table:
            for r in con.execute(f"SELECT id, name FROM {label_table}"):
                names[r["id"]] = r["name"]
        for r in rows:
            items.append({"key": str(r["k"]), "name": names.get(r["k"], str(r["k"])), "count": r["c"]})
        missing = _count(con, where + [f"NOT EXISTS (SELECT 1 FROM {table} x WHERE x.document_id = d.id)"],
                          params)
        if missing:
            items.append({"key": UNFILED, "name": "Unfiled", "count": missing})
    filed = [i for i in items if i["key"] != UNFILED]
    unfiled = [i for i in items if i["key"] == UNFILED]
    if dim == "year":
        filed.sort(key=lambda i: i["key"], reverse=True)
    elif dim == "month":
        filed.sort(key=lambda i: i["key"])
    else:
        filed.sort(key=lambda i: i["name"].lower())
    return filed + unfiled


def browse(con: sqlite3.Connection, path: list[str], selected: dict, limit: int = 50,
           offset: int = 0) -> dict:
    """Walk `path` as far as `selected` (keyed by query parameter name) goes; group by the
    next dim, or list the documents when every dim on the path has a chosen value."""
    if not path or any(d not in DIMS for d in path):
        raise ValueError(f"unknown browse path {path!r}")
    by_param = {param: dim for dim, (param, _) in DIMS.items()}
    for param, value in selected.items():
        if param in by_param and value not in (None, ""):
            _coerce(by_param[param], value)   # malformed values are an error even off the path
    where, params = ["d.deleted_at IS NULL", "d.status = 'ready'"], []
    depth = 0
    for dim in path:
        value = _coerce(dim, selected.get(DIMS[dim][0]))
        if value is None:
            break
        _filter(dim, value, where, params)
        depth += 1
    total = _count(con, where, params)
    if depth == len(path):
        sql_where = " AND ".join(where)
        rows = con.execute(
            f"SELECT d.id FROM documents d WHERE {sql_where} "
            "ORDER BY COALESCE(d.doc_date, substr(d.added_at, 1, 10)) DESC, d.id DESC LIMIT ? OFFSET ?",
            (*params, max(1, min(int(limit), 500)), max(0, int(offset)))).fetchall()
        docs = Library(con)._summaries([r[0] for r in rows])
        return {"level": "documents", "path": path, "items": [], "documents": docs, "total": total}
    dim = path[depth]
    return {"level": dim, "path": path, "items": _group(con, dim, where, params),
            "documents": [], "total": total}
