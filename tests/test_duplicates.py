"""Series and near-duplicates (spec B.3): a copy needs the same series, a near-identical
SimHash, a near-equal length, and the same key facts. Monthly bills fail the facts test."""
from datetime import date

from paperjay import ask, duplicates, profile, settings, totals

BILL = ("City Water\nAccount No: 4455 6677\nStatement date: {when}\nAmount due ${total}\n"
        "Due date: {due}\nWater and sewer service for the house on Maple Street. Your usage "
        "this period was average for the season and the meter was read on the last day of "
        "the month. Please pay the amount due by the due date shown above. Questions about "
        "your bill: call the office during business hours any weekday morning or afternoon. "
        "A late fee may apply to balances that remain unpaid after the due date, and service "
        "may be interrupted for accounts that stay past due for two full billing periods. "
        "You can pay by mail, by phone, in person at the office on Main Street, or online "
        "through the customer portal at any hour of the day or night. Please write your "
        "account number on your cheque so the payment is credited to the right household "
        "without delay. If you believe this bill is wrong, contact the office within thirty "
        "days and the reading will be checked again while the amount stays in dispute.")


def bill(when="August 1, 2025", total="40.00", due="August 21, 2025"):
    return BILL.format(when=when, total=total, due=due)


def test_simhash_identity_and_distance():
    text = bill()
    assert duplicates.hamming(duplicates.simhash(text), duplicates.simhash(text)) == 0
    near = text.replace("weekday", "workday")
    assert duplicates.hamming(duplicates.simhash(text), duplicates.simhash(near)) <= 3
    far = "Passport application form. Name, date of birth, nationality and signature."
    assert duplicates.hamming(duplicates.simhash(text), duplicates.simhash(far)) > 10


def test_simhash_signed_roundtrip():
    h = duplicates.simhash(bill())
    assert duplicates.from_signed(duplicates.to_signed(h)) == h


def test_facts_match():
    a = {"total": 40.0, "dates": [{"label": "due", "date": "2025-08-21"}]}
    same_total = {"total": 40.0, "dates": [{"label": "due", "date": "2025-08-21"}]}
    different = {"total": 55.0, "dates": [{"label": "due", "date": "2025-09-21"}]}
    empty = {"total": None, "dates": []}
    assert duplicates.facts_match(a, same_total)
    assert not duplicates.facts_match(a, different)  # different totals: not the same document
    assert duplicates.facts_match(empty, {"total": None, "dates": []})
    assert not duplicates.facts_match(a, empty)


def test_facts_match_renewals_are_not_copies():
    # yearly insurance cards: no total, no issued/due/service dates — but the expiry differs,
    # so they are two documents in a series, not two scans of one document
    y2025 = {"total": None, "dates": [{"label": "expires", "date": "2025-10-22"},
                                      {"label": "effective", "date": "2025-04-22"}]}
    y2026 = {"total": None, "dates": [{"label": "expires", "date": "2026-10-22"},
                                      {"label": "effective", "date": "2026-04-22"}]}
    assert not duplicates.facts_match(y2025, y2026)
    assert duplicates.facts_match(y2025, dict(y2025))  # a true re-scan agrees on everything


def _meta(doc_id, text, series="acct:44556677", vendor="City Water", when="2025-08-01"):
    return {"simhash": duplicates.simhash(text), "length": len(text),
            "oldest_key": (when, doc_id), "series": series, "vendor": vendor}


def _prof(text, total, due):
    from paperjay import rules
    card = rules.extract(text, "")
    return {"total": total, "dates": [{"label": "due", "date": due}], "series": "acct:44556677",
            "vendor": "City Water", "duplicate_of": None}


def test_monthly_bills_are_not_duplicates():
    texts = {1: bill("July 1, 2025", "38.00", "July 21, 2025"),
             2: bill("August 1, 2025", "40.00", "August 21, 2025"),
             3: bill("September 1, 2025", "42.00", "September 21, 2025")}
    profiles = {i: _prof(t, float(t.split("$")[1].split("\n")[0]), "2025-0" + str(i) + "-21")
                for i, t in texts.items()}
    meta = {i: _meta(i, t, when=f"2025-0{i}-01") for i, t in texts.items()}
    assert duplicates.find_duplicates(profiles, meta) == {}


def test_a_copy_points_at_the_oldest():
    original = bill()
    copy = original.replace("weekday", "workday")
    profiles = {1: _prof(original, 40.0, "2025-08-21"), 2: _prof(copy, 40.0, "2025-08-21")}
    meta = {1: _meta(1, original), 2: _meta(2, copy)}
    assert duplicates.find_duplicates(profiles, meta) == {2: 1}


def test_different_series_never_compared():
    text = bill()
    profiles = {1: _prof(text, 40.0, "2025-08-21"), 2: _prof(text, 40.0, "2025-08-21")}
    meta = {1: _meta(1, text), 2: _meta(2, text, series="acct:9999", vendor="Other Co")}
    assert duplicates.find_duplicates(profiles, meta) == {}


def _two_copies(lib, add_doc):
    cid = lib.get_or_create_label("correspondent", "City Water")[0]
    ids = []
    for i in range(2):
        d = add_doc(data=f"copy-{i}".encode(), name=f"copy{i}.pdf", content=bill(), status="ready")
        lib.update(d, correspondent_id=cid)
        ids.append(d)
    return ids


def test_refile_flags_copy_log_mode_when_configured(con, lib, add_doc):
    settings.save(con, {"duplicate_detection": "log"})
    older, newer = _two_copies(lib, add_doc)
    profile.refile(con, older)
    profile.refile(con, newer)
    doc = lib.get(newer)
    assert doc["suggestions"].get("duplicate_of") == older
    # log mode: recorded in the suggestions, but no copy review note is raised
    assert not any("looks like a copy of" in r for r in doc["suggestions"].get("review") or [])
    assert lib.get(older)["suggestions"].get("duplicate_of") is None
    profiles = profile.current_profiles(con)
    assert profiles[newer]["duplicate_of"] == older
    assert profiles[older]["duplicate_of"] is None
    assert lib.get(newer)["simhash"] is not None


def test_default_duplicate_mode_is_review(con):
    # the B.3 gate passed on the 0.3.1 acceptance evidence (zero false flags on the
    # snapshot and on the synthetic library), so copies flag for review out of the box
    assert settings.get(con, "duplicate_detection") == "review"


def test_refile_flags_copy_for_review_in_review_mode(con, lib, add_doc):
    settings.save(con, {"duplicate_detection": "review"})
    older, newer = _two_copies(lib, add_doc)
    profile.refile(con, older)
    profile.refile(con, newer)
    doc = lib.get(newer)
    assert doc["needs_review"]
    assert any(f"looks like a copy of #{older}" in r for r in doc["suggestions"]["review"])


def test_totals_leave_copies_out():
    def doc(doc_id, when):
        row = {"id": doc_id, "rev": 1, "title": f"Bill {doc_id}", "doc_date": when,
               "added_at": when, "content": "text", "correspondent": "City Water",
               "document_type": "Bill"}
        return ask.Doc(row, [])
    docs = [doc(1, "2025-08-01"), doc(2, "2025-08-01"), doc(3, "2025-09-01")]
    cards = {1: {"total": 40.0, "kind": "bill", "category": "utilities", "vendor": "City Water",
                 "dates": [{"label": "issued", "date": "2025-08-01"}], "ids": []},
             2: {"total": 40.0, "kind": "bill", "category": "utilities", "vendor": "City Water",
                 "dates": [{"label": "issued", "date": "2025-08-01"}], "ids": [], "duplicate_of": 1},
             3: {"total": 42.0, "kind": "bill", "category": "utilities", "vendor": "City Water",
                 "dates": [{"label": "issued", "date": "2025-09-01"}], "ids": []}}
    res = totals.compute("how much in total", ["total"], docs, cards, [], [], date(2026, 1, 1))
    assert res["total"] == 82.0  # the copy is out; the next month's bill still counts
    assert any(e["doc"].id == 2 for e in res["excluded"])


CARD_2025 = ("Progressive Insurance\nAuto Insurance Identification Card\nPolicy Number: P-4471\n"
             "Effective: January 1, 2025\nExpires: January 1, 2026\nInsured: Sam Reed\nVehicle: Ford F-150.")
CARD_2026 = ("Progressive Insurance\nAuto Insurance Identification Card\nPolicy Number: P-4471\n"
             "Effective: January 1, 2026\nExpires: January 1, 2027\nInsured: Sam Reed\nVehicle: Ford F-150.")


def test_series_grouping_only_for_current_questions(con, lib, add_doc):
    from paperjay import profile
    old = add_doc(content=CARD_2025, title="Insurance card 2025", status="ready")
    new = add_doc(content=CARD_2026, title="Insurance card 2026", status="ready")
    for d in (old, new):
        profile.refile(con, d)
    assert profile.current_profiles(con)[old]["series"] == \
        profile.current_profiles(con)[new]["series"]  # one series
    idx = ask.Index()
    idx.refresh(con)
    asker = ask.Asker(con, idx)
    # a "current" question: one source, the newest, with the earlier count attached
    r = asker.retrieve("what is my current insurance card?")
    ids = [s["id"] for s in r["sources"]]
    assert new in ids and old not in ids
    rep = next(s for s in r["sources"] if s["id"] == new)
    assert rep.get("earlier") == 1
    # a question that may mean a specific older card: every member competes on its own
    r2 = asker.retrieve("insurance card policy number")
    ids2 = [s["id"] for s in r2["sources"]]
    assert old in ids2 and new in ids2


def test_group_series_shows_newest_with_earlier_count(con):
    idx = ask.Index()

    def doc(doc_id, when):
        row = {"id": doc_id, "rev": 1, "title": f"Card {doc_id}", "doc_date": when,
               "added_at": when, "content": "insurance card text", "correspondent": "Ins Co",
               "document_type": "Insurance"}
        return ask.Doc(row, [])
    idx.docs = {1: doc(1, "2024-01-01"), 2: doc(2, "2025-01-01"), 3: doc(3, "2025-06-01")}
    idx.cards = {1: {"series": "policy:abc"}, 2: {"series": "policy:abc"}, 3: {"series": None}}
    asker = ask.Asker(con, idx)
    grouped = asker._group_series([(1, 0.9), (2, 0.8), (3, 0.7)])
    by_id = {g[0]: g for g in grouped}
    assert by_id[2][2] == "policy:abc" and by_id[2][3] == 1  # newest stands, one earlier
    assert by_id[3][2] is None and by_id[3][3] == 0
    assert 1 not in by_id
