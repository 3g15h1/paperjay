"""Library: documents, their labels (tags, correspondents, document types), notes, trash and search.

Every change goes through here so the search index and the audit log always match the data. Each
method that writes runs in its own transaction. Pass `user_id` so the audit log records who did it.
Files on disk are not touched here: purge() returns what to delete, and the caller deletes it after
the transaction has committed (a crash in between leaves an orphan file, which `paperjay check` reports).
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from . import papers, vectors
from .db import transaction

LABEL_TABLES = {"tag": "tags", "correspondent": "correspondents", "document_type": "document_types"}
LABEL_COLUMNS = {"correspondent": "correspondent_id", "document_type": "document_type_id"}
# Fields a person or the AI files a document by. ai_fields tracks which of these the AI set last.
# paper_no and category joined in 0.4.0 (spec section 5): both are a person's filing too.
FILING_FIELDS = {"title", "doc_date", "correspondent_id", "document_type_id", "tags", "paper_no", "category"}
# Fields the processing pipeline sets.
PIPELINE_FIELDS = {"content", "status", "error", "page_count", "archive_checksum", "needs_review", "owner_id",
                   "suggestions", "category", "series_key", "simhash"}
CHANGES = 0   # counts document changes in this process (see _audit)
# How long to keep the paper, and whether it was shredded (keeping.py).
KEEPING_FIELDS = {"keep_until", "shredded_at", "shredded_paper_no", "warranty_until"}
# Who made a change: a person, the AI, or the system (OCR's date and title guesses). The AI never
# overrules a person; anyone overrules the system.
SOURCES = {"person": "update", "ai": "ai_update", "system": "auto_update"}
SORTS = {
    "date_desc": "COALESCE(d.doc_date, substr(d.added_at, 1, 10)) DESC, d.id DESC",
    "date_asc": "COALESCE(d.doc_date, substr(d.added_at, 1, 10)) ASC, d.id ASC",
    "added_desc": "d.added_at DESC, d.id DESC",
    "title": "d.title COLLATE NOCASE ASC, d.id ASC",
}
# Snippet highlight markers: control characters can't occur in OCR text, so the UI can HTML-escape the
# snippet first and then turn these into <mark> tags safely.
MARK_START, MARK_END = "\x02", "\x03"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_PAPER_QUERY = re.compile(r"^(?:#|paper(?:\s+(?:no\.?|number))?\s*#?)\s*(\d{1,9})$", re.I)


class NotFound(LookupError):
    pass


class Duplicate(ValueError):
    def __init__(self, existing_id: int):
        super().__init__(f"this file is already document {existing_id}")
        self.existing_id = existing_id


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_date(value) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError(f"dates must be YYYY-MM-DD, got {value!r}")
    datetime.strptime(value, "%Y-%m-%d")


def clean_label_name(name: str) -> str:
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("name can't be empty")
    if len(name) > 128:
        raise ValueError("name is longer than 128 characters")
    return name


def fts_query(text: str, any_word: bool = False) -> str | None:
    """User text -> a safe FTS5 query. Every word is quoted (so FTS syntax in the input does nothing) and
    the last one also matches as a prefix, for search-as-you-type. Words are ANDed unless any_word."""
    words = _WORD_RE.findall(text)
    if not words:
        return None
    parts = [f'"{w}"' for w in words]
    parts[-1] += "*"
    return (" OR " if any_word else " ").join(parts)


class Library:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    # ---- documents ----

    def add(self, *, title: str, checksum: str, original_name: str, original_ext: str, mime_type: str,
            size: int, owner_id: int | None = None, doc_date: str | None = None, content: str = "",
            status: str = "pending", doc_id: int | None = None, added_at: str | None = None,
            user_id: int | None = None, given=(), paper_no: int | None = None,
            claim_paper: bool = False) -> int:
        """Add a document whose original is already in the file store. doc_id/added_at keep the
        values from another system when importing. `given`: filing fields a person supplied (e.g. the
        title typed at upload), which the AI must then leave alone. Raises Duplicate if the file is already here."""
        check_date(doc_date)
        row = {"title": title.strip() or original_name, "checksum": checksum, "original_name": original_name,
               "original_ext": original_ext, "mime_type": mime_type, "size": size, "owner_id": owner_id,
               "doc_date": doc_date, "content": content, "status": status}
        if doc_id is not None:
            row["id"] = doc_id
        if added_at is not None:
            row["added_at"] = row["modified_at"] = added_at
        with transaction(self.con):
            existing = self.con.execute("SELECT id FROM documents WHERE checksum = ?", (checksum,)).fetchone()
            if existing:
                raise Duplicate(existing[0])
            if claim_paper:  # Phase D: the next free number, claimed inside this transaction
                paper_no = papers.next_free(self.con)
            if paper_no is not None:
                papers.validate(paper_no)
                papers.check_free(self.con, paper_no)
                row["paper_no"] = paper_no
            cols = ", ".join(row)
            cur = self.con.execute(f"INSERT INTO documents ({cols}) VALUES ({', '.join('?' * len(row))})",
                                   tuple(row.values()))
            new_id = cur.lastrowid
            self._reindex(new_id)
            self._audit(user_id, "add", new_id, {"name": original_name, "given": sorted(set(given) & FILING_FIELDS)})
        return new_id

    def get(self, doc_id: int) -> dict:
        row = self.con.execute(
            "SELECT d.*, c.name AS correspondent, t.name AS document_type FROM documents d "
            "LEFT JOIN correspondents c ON c.id = d.correspondent_id "
            "LEFT JOIN document_types t ON t.id = d.document_type_id WHERE d.id = ?", (doc_id,)).fetchone()
        if not row:
            raise NotFound(f"no document {doc_id}")
        doc = dict(row)
        doc["ai_fields"] = json.loads(doc["ai_fields"])
        doc["suggestions"] = json.loads(doc["suggestions"])
        doc["needs_review"] = bool(doc["needs_review"])
        doc["tags"] = self._tags_of([doc_id]).get(doc_id, [])
        return doc

    def update(self, doc_id: int, *, user_id: int | None = None, by: str = "person", **fields) -> dict:
        """Change a document. `tags` is the complete list of tag ids. `by` is person, ai or system (see
        SOURCES). Filing fields the AI changes are remembered in ai_fields until someone else changes them."""
        if by not in SOURCES:
            raise ValueError(f"unknown change source {by!r}")
        unknown = set(fields) - FILING_FIELDS - PIPELINE_FIELDS - KEEPING_FIELDS
        if unknown:
            raise ValueError(f"can't change {', '.join(sorted(unknown))}")
        if "doc_date" in fields:
            check_date(fields["doc_date"])
        if "title" in fields:
            fields["title"] = str(fields["title"]).strip()
            if not fields["title"]:
                raise ValueError("title can't be empty")
        if "paper_no" in fields:
            v = fields["paper_no"]
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
                raise ValueError("paper number must be a whole number, 1 or more")
        if fields.get("keep_until") is not None:
            check_date(fields["keep_until"])
        if fields.get("warranty_until") not in (None, "none"):
            check_date(fields["warranty_until"])
        if "category" in fields:
            v = fields["category"]
            if v is not None and (not isinstance(v, str) or not v.strip() or len(v) > 40):
                raise ValueError("category must be a short name")
            fields["category"] = v.strip() if isinstance(v, str) else None
        with transaction(self.con):
            old = self.get(doc_id)
            if fields.get("paper_no") is not None:
                clash = self.con.execute("SELECT id FROM documents WHERE paper_no = ? AND id != ?",
                                         (fields["paper_no"], doc_id)).fetchone()
                if clash:
                    raise ValueError(f"paper number {fields['paper_no']} already belongs to "
                                     f"document {clash[0]}")
            changes = {}
            if "tags" in fields:
                new_tags = sorted({int(t) for t in fields.pop("tags")})
                if new_tags != sorted(t["id"] for t in old["tags"]):
                    self.con.execute("DELETE FROM document_tags WHERE document_id = ?", (doc_id,))
                    self.con.executemany("INSERT INTO document_tags(document_id, tag_id) VALUES (?, ?)",
                                         [(doc_id, t) for t in new_tags])
                    changes["tags"] = [sorted(t["id"] for t in old["tags"]), new_tags]
            for k, v in fields.items():
                if k == "needs_review":
                    v = int(bool(v))
                    if v != int(old[k]):
                        changes[k] = [bool(old[k]), bool(v)]
                elif v != old[k]:
                    changes[k] = [old[k], v]
            if not changes:
                return old
            ai = set(old["ai_fields"])
            touched = set(changes) & FILING_FIELDS
            ai = (ai | touched) if by == "ai" else (ai - touched)
            sets = {k: (int(bool(v)) if k == "needs_review" else json.dumps(v) if k == "suggestions" else v)
                    for k, v in fields.items() if k in changes}
            sets["ai_fields"] = json.dumps(sorted(ai))
            sets["modified_at"] = now_iso()
            self.con.execute(f"UPDATE documents SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                             (*sets.values(), doc_id))
            if changes.keys() & {"title", "content", "correspondent_id", "document_type_id", "tags"}:
                self._reindex(doc_id)
            elif "warranty_until" in changes:   # what Coming up and Ask read: let their index reload it
                self.con.execute("UPDATE documents SET rev = rev + 1 WHERE id = ?", (doc_id,))
            if "correspondent_id" in changes:
                self._sync_company_term(doc_id)
            if "content" in changes:  # too long for the log; record the size instead
                changes["content"] = [len(old["content"] or ""), len(fields["content"] or "")]
            self._audit(user_id, SOURCES[by], doc_id, changes)
            if by == "person" and changes.get("correspondent_id", [None, None])[1]:
                # corrections teach (spec B.2): the name the document was read as becomes an
                # alias of the company the person chose
                self._teach_alias(doc_id, changes["correspondent_id"][1])
        return self.get(doc_id)

    def add_note(self, doc_id: int, body: str, user_id: int | None = None) -> int:
        body = body.strip()
        if not body:
            raise ValueError("note can't be empty")
        with transaction(self.con):
            self.get(doc_id)
            cur = self.con.execute("INSERT INTO notes(document_id, user_id, body) VALUES (?, ?, ?)",
                                   (doc_id, user_id, body))
            self._reindex(doc_id)
            self._audit(user_id, "note", doc_id, {"note_id": cur.lastrowid})
        return cur.lastrowid

    def notes(self, doc_id: int) -> list[dict]:
        return [dict(r) for r in self.con.execute(
            "SELECT n.id, n.body, n.created_at, n.user_id, u.display_name AS user FROM notes n "
            "LEFT JOIN users u ON u.id = n.user_id WHERE n.document_id = ? ORDER BY n.id", (doc_id,))]

    def history(self, doc_id: int) -> list[dict]:
        out = []
        for r in self.con.execute("SELECT a.*, u.display_name AS user FROM audit_log a "
                                  "LEFT JOIN users u ON u.id = a.user_id WHERE a.document_id = ? ORDER BY a.id",
                                  (doc_id,)):
            out.append({**dict(r), "detail": json.loads(r["detail"])})
        return out

    def person_fields(self, doc_id: int) -> set[str]:
        """Filing fields a person has set on this document: the AI must leave these alone.
        Audit entries are read in order; a 'release' entry (spec A.3) gives a field back."""
        out = set()
        for r in self.con.execute("SELECT action, detail FROM audit_log WHERE document_id = ? ORDER BY id",
                                  (doc_id,)):
            detail = json.loads(r["detail"])
            if r["action"] == "add":
                out |= set(detail.get("given", [])) & FILING_FIELDS
            elif r["action"] == "update":
                out |= set(detail) & FILING_FIELDS
            elif r["action"] == "release":
                out -= set(detail.get("fields", [])) & FILING_FIELDS
        return out

    def confirm_filing(self, doc_id: int, user_id: int | None = None) -> None:
        """A person vouches for this document's filing as it stands (spec B.1): from now on the
        library layer (neighbours) may learn from it, even if it was imported or carries an
        import-AI tag. Recorded in the audit log; fields the AI changes afterwards stop being
        trusted again (they land in ai_fields, which the trust check subtracts)."""
        with transaction(self.con):
            self.get(doc_id)
            self._audit(user_id, "confirm_filing", doc_id, {})

    def confirm_fields(self, doc_id: int, fields, user_id: int | None = None) -> dict:
        """A person says these filing fields are right as they stand (Tidy up, spec E). Recorded
        as that person's own change of the field to its current value, so it is theirs from
        now on: the AI and the rules leave it alone and the library layer learns from it.
        `fields` uses the document's column names (correspondent_id, document_type_id, doc_date…)."""
        fields = sorted(set(fields) & FILING_FIELDS - {"tags"})
        if not fields:
            raise ValueError("nothing to confirm")
        with transaction(self.con):
            doc = self.get(doc_id)
            detail = {f: [doc[f], doc[f]] for f in fields}
            ai = sorted(set(doc["ai_fields"]) - set(fields))
            self.con.execute("UPDATE documents SET ai_fields = ? WHERE id = ?", (json.dumps(ai), doc_id))
            self._audit(user_id, "update", doc_id, detail)
        return self.get(doc_id)

    def not_a_copy(self, doc_id: int, user_id: int | None = None) -> dict:
        """A person says the document flagged as a copy isn't one (Tidy up, spec E): the flag
        goes, and duplicate detection never pairs it with that document again."""
        with transaction(self.con):
            doc = self.get(doc_id)
            sug = dict(doc["suggestions"])
            original = sug.pop("duplicate_of", None)
            if original is None:
                raise ValueError(f"document {doc_id} isn't marked as a copy")
            sug["not_copy_of"] = sorted(set(sug.get("not_copy_of") or []) | {original})
            sug["review"] = [r for r in sug.get("review") or [] if "looks like a copy of" not in r]
            fresh = [r for r in sug["review"] if r not in set(sug.get("reviewed") or [])]
            return self.update(doc_id, user_id=user_id, by="system", suggestions=sug,
                               needs_review=bool(fresh) and doc["needs_review"])

    def _given_only_fields(self, doc_id: int) -> set[str]:
        """Fields whose only 'person' source is the import's `given` (never set by a person since,
        not already released) — the ones a release may give back (spec A.3)."""
        given, released, touched = set(), set(), set()
        for r in self.con.execute("SELECT action, detail FROM audit_log WHERE document_id = ? ORDER BY id",
                                  (doc_id,)):
            detail = json.loads(r["detail"])
            if r["action"] == "add":
                given |= set(detail.get("given", [])) & FILING_FIELDS
            elif r["action"] == "update":
                touched |= set(detail) & FILING_FIELDS
            elif r["action"] == "release":
                released |= set(detail.get("fields", [])) & FILING_FIELDS
        return given - touched - released

    def release_import_filing(self) -> int:
        """One-time upgrade step (spec A.3/A.3b): give back import-`given` fields that aren't really
        a person's filing — file-name titles, and doc dates that are just the day the document was
        scanned into the old system while its text carries a labelled date of its own. Each release
        is an audit entry, so it is visible and reversible. Returns how many documents changed."""
        from . import rules
        changed = 0
        ids = [r[0] for r in self.con.execute("SELECT id FROM documents WHERE deleted_at IS NULL ORDER BY id")]
        for doc_id in ids:
            only = self._given_only_fields(doc_id)
            if not only:
                continue
            doc = self.get(doc_id)
            fields = []
            if "title" in only and rules.looks_like_filename(doc["title"], doc["original_name"]):
                fields.append("title")
            if "doc_date" in only and doc["doc_date"] and doc["doc_date"] == (doc["added_at"] or "")[:10]:
                card = rules.extract(doc["content"] or "", doc["title"], doc["original_name"])
                labelled = [d["date"] for d in card["dates"] if d["label"] in rules.DOC_DATE_LABELS]
                if labelled and labelled[0] != doc["doc_date"]:
                    fields.append("doc_date")
            if fields:
                with transaction(self.con):
                    why = {"title": "title is a file name", "doc_date": "doc date is the scan date"}
                    self._audit(None, "release", doc_id,
                                {"fields": fields, "why": "; ".join(why[f] for f in fields)})
                changed += 1
        return changed

    # ---- trash ----

    def assign_paper_numbers(self, ids, user_id: int | None = None) -> dict[int, int]:
        """Phase D bulk action 'number these in order': give the selected documents that
        lack a paper number the next free numbers, in the order selected, in one
        transaction. Returns {document_id: paper_no} for the ones numbered."""
        assigned = {}
        with transaction(self.con):
            nxt = papers.next_free(self.con)
            for doc_id in ids:
                row = self.con.execute("SELECT paper_no, deleted_at FROM documents WHERE id = ?",
                                         (doc_id,)).fetchone()
                if row is None or row[0] is not None or row[1] is not None:
                    continue
                self.update(doc_id, user_id=user_id, paper_no=nxt)
                assigned[doc_id] = nxt
                nxt += 1
        return assigned

    def trash(self, doc_id: int, user_id: int | None = None) -> None:
        with transaction(self.con):
            if self.get(doc_id)["deleted_at"]:
                return
            self.con.execute("UPDATE documents SET deleted_at = ? WHERE id = ?", (now_iso(), doc_id))
            self._reindex(doc_id)
            vectors.bump(self.con)
            self._audit(user_id, "trash", doc_id, {})

    def restore(self, doc_id: int, user_id: int | None = None) -> None:
        with transaction(self.con):
            if not self.get(doc_id)["deleted_at"]:
                return
            self.con.execute("UPDATE documents SET deleted_at = NULL WHERE id = ?", (doc_id,))
            self._reindex(doc_id)
            vectors.bump(self.con)
            self._audit(user_id, "restore", doc_id, {})

    def purge(self, doc_id: int, user_id: int | None = None) -> tuple[str, str]:
        """Delete a trashed document for good. Returns (checksum, ext) of the files the caller must delete."""
        with transaction(self.con):
            doc = self.get(doc_id)
            if not doc["deleted_at"]:
                raise ValueError(f"document {doc_id} isn't in the trash; move it there first")
            self.con.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self.con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
            self.con.execute("DELETE FROM index_terms WHERE document_id = ?", (doc_id,))
            vectors.bump(self.con)
            self._audit(user_id, "purge", doc_id, {"title": doc["title"], "checksum": doc["checksum"]})
        return doc["checksum"], doc["original_ext"]

    def expired_trash(self, days: int = 30) -> list[int]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [r[0] for r in self.con.execute(
            "SELECT id FROM documents WHERE deleted_at IS NOT NULL AND deleted_at < ? ORDER BY id", (cutoff,))]

    # ---- labels: tags, correspondents, document types ----

    def labels(self, kind: str) -> list[dict]:
        table = LABEL_TABLES[kind]
        if kind == "tag":
            count = "(SELECT COUNT(*) FROM document_tags dt JOIN documents d ON d.id = dt.document_id " \
                    "WHERE dt.tag_id = l.id AND d.deleted_at IS NULL)"
        else:
            count = f"(SELECT COUNT(*) FROM documents d WHERE d.{LABEL_COLUMNS[kind]} = l.id AND d.deleted_at IS NULL)"
        return [dict(r) for r in self.con.execute(
            f"SELECT l.*, {count} AS document_count FROM {table} l ORDER BY l.name COLLATE NOCASE")]

    def find_label(self, kind: str, name: str) -> int | None:
        row = self.con.execute(f"SELECT id FROM {LABEL_TABLES[kind]} WHERE name = ?",
                               (clean_label_name(name),)).fetchone()
        return row[0] if row else None

    def create_label(self, kind: str, name: str, color: str | None = None, user_id: int | None = None) -> int:
        name = clean_label_name(name)
        with transaction(self.con):
            if kind == "tag" and color:
                cur = self.con.execute("INSERT INTO tags(name, color) VALUES (?, ?)", (name, color))
            else:
                cur = self.con.execute(f"INSERT INTO {LABEL_TABLES[kind]}(name) VALUES (?)", (name,))
            self._audit(user_id, f"create_{kind}", None, {"id": cur.lastrowid, "name": name})
        return cur.lastrowid

    def get_or_create_label(self, kind: str, name: str, user_id: int | None = None) -> tuple[int, bool]:
        existing = self.find_label(kind, name)
        if existing is not None:
            return existing, False
        try:
            return self.create_label(kind, name, user_id=user_id), True
        except sqlite3.IntegrityError:  # created by someone else in the meantime
            return self.find_label(kind, name), False

    def rename_label(self, kind: str, label_id: int, name: str, user_id: int | None = None) -> None:
        name = clean_label_name(name)
        with transaction(self.con):
            old = self._label(kind, label_id)
            self.con.execute(f"UPDATE {LABEL_TABLES[kind]} SET name = ? WHERE id = ?", (name, label_id))
            for doc_id in self._docs_with(kind, [label_id]):
                self._reindex(doc_id)
                if kind == "correspondent":
                    self._sync_company_term(doc_id)
            self._audit(user_id, f"rename_{kind}", None, {"id": label_id, "name": [old["name"], name]})

    def set_tag_color(self, tag_id: int, color: str) -> None:
        if not re.match(r"^#[0-9a-fA-F]{6}$", color):
            raise ValueError("color must look like #a1b2c3")
        with transaction(self.con):
            self._label("tag", tag_id)
            self.con.execute("UPDATE tags SET color = ? WHERE id = ?", (color.lower(), tag_id))

    def merge_labels(self, kind: str, source_ids: list[int], target_id: int, user_id: int | None = None) -> None:
        """Move every document from the source labels to the target, then delete the sources."""
        sources = sorted({int(s) for s in source_ids} - {target_id})
        if not sources:
            return
        with transaction(self.con):
            self._label(kind, target_id)
            names = [self._label(kind, s)["name"] for s in sources]
            affected = self._docs_with(kind, sources)
            marks = ", ".join("?" * len(sources))
            if kind == "tag":
                self.con.execute(f"INSERT OR IGNORE INTO document_tags(document_id, tag_id) "
                                 f"SELECT document_id, ? FROM document_tags WHERE tag_id IN ({marks})",
                                 (target_id, *sources))
            else:
                self.con.execute(f"UPDATE documents SET {LABEL_COLUMNS[kind]} = ? "
                                 f"WHERE {LABEL_COLUMNS[kind]} IN ({marks})", (target_id, *sources))
            self.con.execute(f"DELETE FROM {LABEL_TABLES[kind]} WHERE id IN ({marks})", sources)
            for doc_id in affected:
                self._reindex(doc_id)
                if kind == "correspondent":
                    self._sync_company_term(doc_id)
            if kind == "correspondent":
                # the merged-away names stay as aliases of the survivor (spec B.2), so documents
                # still reading the old name keep filing under the merged company
                for name in names:
                    self.add_alias(target_id, name, source="merge")
            self._audit(user_id, f"merge_{kind}", None, {"into": target_id, "merged": names})

    # ---- correspondent aliases (spec B.2): other names a company is read as ----

    def aliases(self) -> dict[str, str]:
        """{alias: company name} — what the rule extractor matches on top of company names."""
        return {r["alias"]: r["name"] for r in self.con.execute(
            "SELECT a.alias, c.name AS name FROM correspondent_aliases a "
            "JOIN correspondents c ON c.id = a.correspondent_id")}

    def list_aliases(self, correspondent_id: int) -> list[dict]:
        self._label("correspondent", correspondent_id)
        return [dict(r) for r in self.con.execute(
            "SELECT id, alias, source, created_at FROM correspondent_aliases "
            "WHERE correspondent_id = ? ORDER BY alias COLLATE NOCASE", (correspondent_id,))]

    def add_alias(self, correspondent_id: int, alias: str, source: str = "person") -> int | None:
        """Teach that `alias` — a name read from a document — means this correspondent. Returns
        the alias row id, or None when it teaches nothing: the alias is the company's own name,
        or it is exactly another company's name (the text naming a real company must not be
        quietly re-pointed at this one)."""
        alias = clean_label_name(alias)
        target = self._label("correspondent", correspondent_id)
        from .rules import norm_name
        if norm_name(alias) == norm_name(target["name"]):
            return None
        for r in self.con.execute("SELECT id, name FROM correspondents"):
            if r["id"] != correspondent_id and norm_name(r["name"]) == norm_name(alias):
                return None
        with transaction(self.con):
            cur = self.con.execute(
                "INSERT OR IGNORE INTO correspondent_aliases(correspondent_id, alias, source) "
                "VALUES (?, ?, ?)", (correspondent_id, alias, source))
            if cur.rowcount:
                return cur.lastrowid
            row = self.con.execute("SELECT id FROM correspondent_aliases WHERE alias = ?",
                                   (alias,)).fetchone()
            return row[0] if row else None

    def delete_alias(self, correspondent_id: int, alias_id: int) -> None:
        with transaction(self.con):
            self._label("correspondent", correspondent_id)
            cur = self.con.execute(
                "DELETE FROM correspondent_aliases WHERE id = ? AND correspondent_id = ?",
                (alias_id, correspondent_id))
            if not cur.rowcount:
                raise NotFound(f"no alias {alias_id} for correspondent {correspondent_id}")

    def _sync_company_term(self, doc_id: int) -> None:
        """Keep the document's A–Z company term (spec C.2) in step with its filed company,
        for the label changes that happen outside a refile (a person editing, a rename,
        a merge). The full term rebuild stays with profile.refile / indexing.backfill."""
        try:
            row = self.con.execute(
                "SELECT c.name AS name FROM documents d LEFT JOIN correspondents c "
                "ON c.id = d.correspondent_id WHERE d.id = ?", (doc_id,)).fetchone()
            self.con.execute("DELETE FROM index_terms WHERE document_id = ? AND kind = 'company'",
                             (doc_id,))
            if row and row["name"]:
                from . import indexing
                norm = indexing.squash(row["name"])
                if norm:
                    self.con.execute(
                        "INSERT OR IGNORE INTO index_terms(kind, norm, term, label, document_id) "
                        "VALUES ('company', ?, ?, '', ?)", (norm, row["name"], doc_id))
        except sqlite3.OperationalError:
            pass  # a database from before migration 0008 has no index to keep in step

    def _teach_alias(self, doc_id: int, correspondent_id: int) -> None:
        """A person filed this document under a company whose name differs from the vendor text
        its cards read from the page: remember the reading as an alias (spec B.2)."""
        from . import profile as profile_mod
        from .rules import is_mentioned_name
        cards = profile_mod.get_cards(self.con, doc_id)
        for source in ("ai", "rules"):
            vendor = (cards.get(source) or {}).get("vendor")
            # agencies, card networks and well-known companies are mostly *mentioned* in other
            # people's letters ("Secretary of State" on an insurance card): never teach those
            if vendor and not is_mentioned_name(vendor):
                self.add_alias(correspondent_id, vendor, source="person")
                return

    def delete_label(self, kind: str, label_id: int, user_id: int | None = None) -> None:
        with transaction(self.con):
            old = self._label(kind, label_id)
            affected = self._docs_with(kind, [label_id])
            self.con.execute(f"DELETE FROM {LABEL_TABLES[kind]} WHERE id = ?", (label_id,))
            for doc_id in affected:
                self._reindex(doc_id)
                if kind == "correspondent":
                    self._sync_company_term(doc_id)
            self._audit(user_id, f"delete_{kind}", None, {"id": label_id, "name": old["name"]})

    # ---- search and listing ----

    def search(self, query: str = "", *, tag_ids=(), correspondent_id: int | None = None,
               document_type_id: int | None = None, date_from: str | None = None, date_to: str | None = None,
               needs_review: bool | None = None, in_trash: bool = False, sort: str = "relevance",
               status: str | None = None, limit: int = 50, offset: int = 0,
               category: str | None = None, topic: str | None = None, series: str | None = None,
               people: tuple = (), has_paper: bool | None = None, paper_no: int | None = None,
               term: str | None = None) -> dict:
        """Find documents. With a query, full-text search ranked by relevance (title counts most), with a
        highlighted snippet; if no document has all the words, documents with any of them are returned and
        `matched_all` is False. Without a query, a filtered list. The trash is searched by title only."""
        check_date(date_from)
        check_date(date_to)
        # Phase D: "#124" and "paper 124" in the search box mean the paper shelf, not text
        if query and paper_no is None:
            m = _PAPER_QUERY.match(query.strip())
            if m:
                paper_no, query = int(m.group(1)), ""
        limit = max(1, min(int(limit), 500))
        where, params = ["d.deleted_at IS NOT NULL" if in_trash else "d.deleted_at IS NULL"], []
        tag_ids = sorted({int(t) for t in tag_ids})
        if tag_ids:
            where.append(f"d.id IN (SELECT document_id FROM document_tags WHERE tag_id IN "
                         f"({', '.join('?' * len(tag_ids))}) GROUP BY document_id HAVING COUNT(*) = ?)")
            params += [*tag_ids, len(tag_ids)]
        for col, val in (("correspondent_id", correspondent_id), ("document_type_id", document_type_id)):
            if val is not None:
                where.append(f"d.{col} = ?")
                params.append(int(val))
        if date_from:
            where.append("COALESCE(d.doc_date, substr(d.added_at, 1, 10)) >= ?")
            params.append(date_from)
        if date_to:
            where.append("COALESCE(d.doc_date, substr(d.added_at, 1, 10)) <= ?")
            params.append(date_to)
        if needs_review is not None:
            where.append("d.needs_review = ?")
            params.append(int(needs_review))
        if status is not None:
            if status not in ("pending", "processing", "ready", "failed"):
                raise ValueError(f"unknown status {status!r}")
            where.append("d.status = ?")
            params.append(status)
        # the Phase C filters (spec C.5): where a document sits, what it is about, who it
        # concerns, and its place on the paper shelf. They compose with everything above.
        if category is not None:
            where.append("d.category = ?")
            params.append(category)
        if topic is not None:
            where.append("d.id IN (SELECT document_id FROM document_topics WHERE topic = ?)")
            params.append(topic)
        if series is not None:
            where.append("d.series_key = ?")
            params.append(series)
        if people:
            from . import indexing as indexing_mod
            norms = sorted({indexing_mod.squash(p) for p in people} - {""})
            if norms:
                marks = ", ".join("?" * len(norms))
                where.append("d.id IN (SELECT document_id FROM index_terms "
                             f"WHERE kind = 'person' AND norm IN ({marks}))")
                params += norms
        if has_paper is not None:
            where.append("d.paper_no IS NOT NULL" if has_paper else "d.paper_no IS NULL")
        if paper_no is not None:
            if isinstance(paper_no, bool) or int(paper_no) < 1:
                raise ValueError("paper_no must be a positive whole number")
            where.append("d.paper_no = ?")
            params.append(int(paper_no))
        if term is not None:
            from . import indexing as indexing_mod
            kind, _, key = term.partition(":")
            if kind not in indexing_mod.KINDS or not key:
                raise ValueError("term must look like company:<key> (from the A–Z index)")
            norm = indexing_mod.resolve_key(self.con, kind, key)
            if norm is None:
                where.append("1 = 0")   # an unknown term matches nothing, not everything
            else:
                where.append("d.id IN (SELECT document_id FROM index_terms "
                             "WHERE kind = ? AND norm = ?)")
                params += [kind, norm]

        query = query.strip()
        if query and in_trash:
            where.append("d.title LIKE ? ESCAPE '\\'")
            params.append("%" + re.sub(r"([\\%_])", r"\\\1", query) + "%")
        elif query and fts_query(query) is not None:
            result = self._fts(fts_query(query), where, params, sort, limit, offset)
            if result["total"]:
                return {**result, "matched_all": True}
            return {**self._fts(fts_query(query, any_word=True), where, params, sort, limit, offset),
                    "matched_all": False}

        order = SORTS.get(sort, SORTS["date_desc"])
        sql_where = " AND ".join(where)
        total = self.con.execute(f"SELECT COUNT(*) FROM documents d WHERE {sql_where}", params).fetchone()[0]
        rows = self.con.execute(f"SELECT d.id FROM documents d WHERE {sql_where} ORDER BY {order} "
                                f"LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return {"total": total, "results": self._summaries([r[0] for r in rows]), "matched_all": True}

    def _fts(self, match: str, where: list, params: list, sort: str, limit: int, offset: int) -> dict:
        sql_where = " AND ".join(["documents_fts MATCH ?", *where])
        base = "FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid"
        total = self.con.execute(f"SELECT COUNT(*) {base} WHERE {sql_where}", (match, *params)).fetchone()[0]
        order = "rank" if sort == "relevance" else SORTS.get(sort, SORTS["date_desc"])
        rows = self.con.execute(
            f"SELECT d.id, bm25(documents_fts, 10.0, 4.0, 1.0) AS rank, "
            f"snippet(documents_fts, 2, '{MARK_START}', '{MARK_END}', '…', 16) AS snippet "
            f"{base} WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
            (match, *params, limit, offset)).fetchall()
        summaries = self._summaries([r["id"] for r in rows])
        for s, r in zip(summaries, rows):
            s["snippet"], s["score"] = r["snippet"], -r["rank"]
        return {"total": total, "results": summaries}

    def _summaries(self, ids: list[int]) -> list[dict]:
        """List entries for these documents, in the given order."""
        if not ids:
            return []
        marks = ", ".join("?" * len(ids))
        rows = {r["id"]: dict(r) for r in self.con.execute(
            "SELECT d.id, d.title, d.doc_date, d.added_at, d.status, d.page_count, d.needs_review, d.deleted_at, "
            "d.checksum, d.mime_type, d.correspondent_id, c.name AS correspondent, d.document_type_id, "
            "d.category, d.series_key, d.paper_no, "
            "t.name AS document_type FROM documents d "
            "LEFT JOIN correspondents c ON c.id = d.correspondent_id "
            f"LEFT JOIN document_types t ON t.id = d.document_type_id WHERE d.id IN ({marks})", ids)}
        tags = self._tags_of(ids)
        out = []
        for i in ids:
            r = rows[i]
            r["needs_review"] = bool(r["needs_review"])
            r["tags"] = tags.get(i, [])
            out.append(r)
        return out

    def counts(self) -> dict:
        r = self.con.execute(
            "SELECT COUNT(*) FILTER (WHERE deleted_at IS NULL) AS documents, "
            "COUNT(*) FILTER (WHERE deleted_at IS NOT NULL) AS trash, "
            "COUNT(*) FILTER (WHERE deleted_at IS NULL AND needs_review) AS needs_review, "
            "COUNT(*) FILTER (WHERE deleted_at IS NULL AND status = 'failed') AS failed FROM documents").fetchone()
        return dict(r)

    # ---- internals ----

    def _tags_of(self, ids: list[int]) -> dict[int, list[dict]]:
        if not ids:
            return {}
        out: dict[int, list[dict]] = {}
        for r in self.con.execute(
                "SELECT dt.document_id, t.id, t.name, t.color FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
                f"WHERE dt.document_id IN ({', '.join('?' * len(ids))}) ORDER BY t.name COLLATE NOCASE", ids):
            out.setdefault(r["document_id"], []).append({"id": r["id"], "name": r["name"], "color": r["color"]})
        return out

    def _label(self, kind: str, label_id: int) -> sqlite3.Row:
        row = self.con.execute(f"SELECT * FROM {LABEL_TABLES[kind]} WHERE id = ?", (label_id,)).fetchone()
        if not row:
            raise NotFound(f"no {kind.replace('_', ' ')} {label_id}")
        return row

    def _docs_with(self, kind: str, label_ids: list[int]) -> list[int]:
        marks = ", ".join("?" * len(label_ids))
        if kind == "tag":
            sql = f"SELECT DISTINCT document_id FROM document_tags WHERE tag_id IN ({marks})"
        else:
            sql = f"SELECT id FROM documents WHERE {LABEL_COLUMNS[kind]} IN ({marks})"
        return [r[0] for r in self.con.execute(sql, label_ids)]

    def _reindex(self, doc_id: int) -> None:
        """Rewrite one document's search entry (and bump its rev). Trashed and purged documents get none."""
        self.con.execute("UPDATE documents SET rev = rev + 1 WHERE id = ?", (doc_id,))
        self.con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
        row = self.con.execute(
            "SELECT d.title, d.content, d.deleted_at, c.name AS correspondent, t.name AS document_type "
            "FROM documents d LEFT JOIN correspondents c ON c.id = d.correspondent_id "
            "LEFT JOIN document_types t ON t.id = d.document_type_id WHERE d.id = ?", (doc_id,)).fetchone()
        if not row or row["deleted_at"]:
            return
        meta = [row["correspondent"] or "", row["document_type"] or ""]
        meta += [r[0] for r in self.con.execute(
            "SELECT t.name FROM document_tags dt JOIN tags t ON t.id = dt.tag_id WHERE dt.document_id = ?", (doc_id,))]
        meta += [r[0] for r in self.con.execute("SELECT body FROM notes WHERE document_id = ?", (doc_id,))]
        meta += [f"{r[0]} {r[1]}" for r in self.con.execute(   # the household's own fields (fields.py)
            "SELECT f.name, v.value FROM document_fields v JOIN custom_fields f ON f.id = v.field_id "
            "WHERE v.document_id = ? AND v.value != ''", (doc_id,))]
        self.con.execute("INSERT INTO documents_fts(rowid, title, meta, content) VALUES (?, ?, ?, ?)",
                         (doc_id, row["title"], "\n".join(m for m in meta if m), row["content"] or ""))

    def _audit(self, user_id: int | None, action: str, doc_id: int | None, detail: dict) -> None:
        global CHANGES
        CHANGES += 1   # every change to a document passes here: cheap "has anything changed?" for caches
        self.con.execute("INSERT INTO audit_log(user_id, action, document_id, detail) VALUES (?, ?, ?, ?)",
                         (user_id, action, doc_id, json.dumps(detail, ensure_ascii=False, default=str)))
