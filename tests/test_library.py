import pytest

from paperjay.library import MARK_END, MARK_START, Duplicate, NotFound, fts_query


def test_add_get_and_duplicate(lib, add_doc, store):
    d = add_doc(b"same bytes", name="bill.pdf", doc_date="2025-08-14", content="DTE Energy bill")
    doc = lib.get(d)
    assert doc["title"] == "bill.pdf" and doc["doc_date"] == "2025-08-14" and doc["tags"] == []
    with pytest.raises(Duplicate) as e:
        add_doc(b"same bytes", name="again.pdf")
    assert e.value.existing_id == d
    with pytest.raises(NotFound):
        lib.get(999)


def test_import_keeps_id_and_date(add_doc, lib):
    d = add_doc(doc_id=37, added_at="2026-09-30T10:00:00Z")
    assert d == 37 and lib.get(37)["added_at"] == "2026-09-30T10:00:00Z"


def test_bad_date_rejected(add_doc):
    with pytest.raises(ValueError):
        add_doc(doc_date="2025-13-01")


def test_update_tracks_ai_fields_and_audits(lib, add_doc):
    d = add_doc(content="water bill")
    bills = lib.create_label("tag", "Bills")
    lib.update(d, by="ai", title="Water bill August", tags=[bills])
    assert lib.get(d)["ai_fields"] == ["tags", "title"]
    doc = lib.update(d, user_id=None, title="Water bill Aug 2025")
    assert doc["ai_fields"] == ["tags"] and [t["name"] for t in doc["tags"]] == ["Bills"]
    actions = [h["action"] for h in lib.history(d)]
    assert actions == ["add", "ai_update", "update"]
    assert lib.history(d)[-1]["detail"]["title"] == ["Water bill August", "Water bill Aug 2025"]


def test_update_without_changes_is_not_logged(lib, add_doc):
    d = add_doc(title="Same")
    lib.update(d, title="Same")
    assert [h["action"] for h in lib.history(d)] == ["add"]


def test_update_rejects_unknown_fields(lib, add_doc):
    with pytest.raises(ValueError):
        lib.update(add_doc(), checksum="x")


def test_search_ranks_title_and_highlights(lib, add_doc):
    a = add_doc(title="Car insurance policy", content="Progressive policy number 123, expires 2026-10-22")
    b = add_doc(title="Groceries", content="milk eggs; insurance mentioned once in small print")
    r = lib.search("insurance")
    assert [x["id"] for x in r["results"]] == [a, b] and r["matched_all"]
    assert MARK_START in r["results"][1]["snippet"] and MARK_END in r["results"][1]["snippet"]


def test_search_prefix_stemming_and_fallback(lib, add_doc):
    a = add_doc(title="Lawn service", content="mowing invoices for the summer")
    assert [x["id"] for x in lib.search("invoice")["results"]] == [a]   # stemming
    assert [x["id"] for x in lib.search("mow")["results"]] == [a]       # prefix on last word
    r = lib.search("mowing dentist")
    assert [x["id"] for x in r["results"]] == [a] and not r["matched_all"]


def test_search_input_is_never_fts_syntax(lib, add_doc):
    add_doc(content="hello world")
    for q in ['"', "NEAR(", "a AND", "*", "title:x", "-"]:
        lib.search(q)  # must not raise
    assert fts_query("!!!") is None


def test_labels_are_searchable_and_filterable(lib, add_doc):
    dte = lib.create_label("correspondent", "DTE Energy")
    bills = lib.create_label("tag", "Bills")
    paid = lib.create_label("tag", "Paid")
    a = add_doc(content="statement")
    b = add_doc(content="statement two")
    lib.update(a, correspondent_id=dte, tags=[bills, paid])
    lib.update(b, tags=[bills])
    assert [x["id"] for x in lib.search("dte")["results"]] == [a]
    assert {x["id"] for x in lib.search(tag_ids=[bills])["results"]} == {a, b}
    assert [x["id"] for x in lib.search(tag_ids=[bills, paid])["results"]] == [a]
    assert [x["id"] for x in lib.search(correspondent_id=dte)["results"]] == [a]
    lib.rename_label("correspondent", dte, "DTE")
    assert lib.get(a)["correspondent"] == "DTE"
    assert lib.search("energy")["total"] == 0


def test_merge_and_delete_labels(lib, add_doc):
    a, b = add_doc(), add_doc(name="b.pdf")
    t1, t2, t3 = (lib.create_label("tag", n) for n in ("Bill", "Bills", "bils"))
    lib.update(a, tags=[t1, t2])
    lib.update(b, tags=[t3])
    lib.merge_labels("tag", [t1, t3], t2)
    assert [t["name"] for t in lib.get(a)["tags"]] == ["Bills"]
    assert [t["name"] for t in lib.get(b)["tags"]] == ["Bills"]
    assert [l["name"] for l in lib.labels("tag")] == ["Bills"] and lib.labels("tag")[0]["document_count"] == 2
    lib.delete_label("tag", t2)
    assert lib.get(a)["tags"] == []
    assert lib.search("bills")["total"] == 0


def test_label_names_unique_ignoring_case(lib):
    a, created = lib.get_or_create_label("tag", "  Medical ")
    assert created
    assert lib.get_or_create_label("tag", "medical") == (a, False)


def test_trash_restore_purge(lib, add_doc, store):
    d = add_doc(content="tax return 2025")
    with pytest.raises(ValueError):
        lib.purge(d)
    lib.trash(d)
    assert lib.search("tax")["total"] == 0
    assert [x["id"] for x in lib.search("tax", in_trash=True)["results"]] == []  # trash matches titles only
    assert [x["id"] for x in lib.search(in_trash=True)["results"]] == [d]
    assert lib.counts()["trash"] == 1
    lib.restore(d)
    assert lib.search("tax")["total"] == 1
    lib.trash(d)
    checksum, ext = lib.purge(d)
    with pytest.raises(NotFound):
        lib.get(d)
    assert [h["action"] for h in lib.history(d)][-1] == "purge"  # history outlives the document
    assert checksum and ext == ".pdf"


def test_notes_are_searchable(lib, add_doc):
    d = add_doc(content="receipt")
    lib.add_note(d, "warranty until 2027")
    assert [x["id"] for x in lib.search("warranty")["results"]] == [d]
    assert lib.notes(d)[0]["body"] == "warranty until 2027"


def test_list_sorting_and_dates(lib, add_doc):
    old = add_doc(name="old.pdf", doc_date="2024-01-05")
    new = add_doc(name="new.pdf", doc_date="2025-06-01")
    assert [x["id"] for x in lib.search(sort="date_desc")["results"]] == [new, old]
    assert [x["id"] for x in lib.search(sort="date_asc")["results"]] == [old, new]
    assert [x["id"] for x in lib.search(date_from="2025-01-01")["results"]] == [new]
    assert [x["id"] for x in lib.search(date_to="2024-12-31")["results"]] == [old]
