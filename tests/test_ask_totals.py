"""Totals: detection, filters, de-duplication, and the sentence."""
from datetime import date

from paperjay.textutil import MONTH_NAMES
from paperjay.totals import compute, describe, is_total_question


class D:
    def __init__(self, id, created, title="Doc", correspondent=None):
        self.id, self.title, self.correspondent = id, title, correspondent
        self.created_date = date.fromisoformat(created)


def c(category, total, vendor, kind="receipt", dates=()):
    return {"category": category, "total": total, "vendor": vendor, "kind": kind,
            "dates": [{"label": label, "date": d} for label, d in dates]}


DOCS = [D(1, "2025-03-10"), D(2, "2025-03-20"), D(3, "2024-06-01"), D(4, "2025-08-15"), D(5, "2025-05-01"),
        D(6, "2025-07-01", title="Apple receipt"), D(7, "2025-09-09")]
CARDS = {1: c("medical", 100.65, "Riverside Clinic", dates=[("service", "2025-03-10")]),
         2: c("medical", 100.65, "Riverside Clinic", kind="statement", dates=[("paid", "2025-03-12")]),
         3: c("medical", 50.0, "Corner Pharmacy"),
         4: c("utilities", 142.37, "AcmePower", kind="bill"),
         5: c("insurance", 1200.0, "Example Mutual", kind="insurance"),
         6: c("shopping", 999.0, "Apple Store")}
TODAY = date(2026, 10, 10)


def test_detection():
    for q in ["How much did I spend on medical in 2025?", "What did I spend at Apple?", "Total spent on utilities",
              "How much have I paid in 2025?", "What did the flooring cost in total?"]:
        assert is_total_question(q), q
    for q in ["How much did my iPhone cost?", "When does my insurance expire?", "What was my electric bill?"]:
        assert not is_total_question(q), q


def test_category_and_year_with_duplicate_removed():
    r = compute("how much did i spend on medical in 2025", ["spend", "medical", "2025"], DOCS, CARDS, [2025], [], TODAY)
    assert r["total"] == 100.65 and [i["doc"].id for i in r["items"]] == [1]
    assert [i["doc"].id for i in r["excluded"]] == [2] and "same charge" in r["excluded"][0]["reason"]
    assert r["pending"] == 1  # doc 7 (2025) has no card yet
    text = describe(r, MONTH_NAMES, "[1]")
    assert text.startswith("Total on medical in 2025: $100.65 across 1 document [1].")
    assert "1 document from that period hasn't been analysed yet" in text


def test_vendor_filter_and_relative_year():
    r = compute("what did i spend at apple this year", ["spend", "apple", "year"], DOCS, CARDS, [], [], date(2025, 12, 1))
    assert [i["doc"].id for i in r["items"]] == [6] and r["years"] == [2025]


def test_insurance_amounts_are_not_spending_and_empty_result():
    r = compute("total spent on insurance", ["total", "spent", "insurance"], DOCS, CARDS, [], [], TODAY)
    assert r["items"] == [] and describe(r, MONTH_NAMES).startswith("I couldn't find any amounts on insurance.")


def _card(total, vendor, kind, ids=(), category="other", created="2025-05-01"):
    return D(0, created), {"category": category, "total": total, "vendor": vendor, "kind": kind,
                                                "dates": [], "ids": [{"label": "n", "value": v} for v in ids]}


def _run(*pairs, words=("spend", "medical")):
    docs = [d for d, _ in pairs]
    for i, d in enumerate(docs):
        d.id = 200 + i
    return compute(" ".join(words), list(words), docs, {d.id: c for d, c in pairs}, [], [], TODAY)


def test_medical_kind_counts_even_if_categorised_elsewhere():
    r = _run(_card(100.65, "Clinic", "medical", category="insurance"))
    assert r["total"] == 100.65


def test_duplicates_need_different_kinds_or_a_shared_id():
    # payment summary + service request for one $130 visit; vendor punctuation differs
    r = _run(_card(130.0, "JAMES WEXLER, MD", "invoice", category="medical"),
             _card(130.0, "JAMES WEXLER MD", "medical", category="medical"))
    assert r["total"] == 130.0 and len(r["excluded"]) == 1
    # two receipts for the same price, different order numbers: two purchases
    r = _run(_card(292.94, "Apple", "invoice", ["W041010"], "shopping"), _card(292.94, "Apple", "invoice", ["W013043"], "shopping"),
             words=("spend", "apple"))
    assert r["total"] == 585.88 and not r["excluded"]
    # the same receipt filed twice
    r = _run(_card(292.94, "Apple", "invoice", ["W041010"], "shopping"), _card(292.94, "Apple", "invoice", ["W-041010"], "shopping"),
             words=("spend", "apple"))
    assert r["total"] == 292.94 and len(r["excluded"]) == 1


def test_so_far_and_medical_bills():
    """“How much in medical bills have I spent so far”: “far” isn't a keyword, “bills” isn't a company."""
    from paperjay.ask import query_terms
    assert "far" not in query_terms("how much in medical bills have I spent so far")[0]
    r = compute("how much in medical bills have i spent so far", ["medical", "bills", "spent"], DOCS, CARDS, [], [], TODAY)
    assert r["vendors"] == [] and r["total"] == 150.65


class T(D):
    def __init__(self, id, created, title, content):
        super().__init__(id, created, title)
        self.content = content


def test_category_must_be_in_the_text_and_price_lists_are_not_spending():
    from paperjay import analyze
    from paperjay.textutil import normalize
    docs = [T(1, "2025-02-03", "IMG_4021", "Title and registration. Dealer transfer fee. Springfield MD 21000"),
            T(2, "2025-04-01", "Clinic visit", "Patient receipt. Office visit. Amount paid $80.00"),
            T(3, "2025-05-01", "Test price list", "Diagnostic lab testing fees. All prices are subject to change without notice. X-ray")]
    cards = {1: c("medical", 491.0, "HARBOR LEASING"), 2: c("medical", 80.0, "Riverside Clinic"),
             3: c("medical", 1200.0, "", kind="medical")}
    supports = lambda cat, doc: analyze.category_supported(cat, normalize(f"{doc.title}\n{doc.content}"))
    r = compute("how much have i spent on medical", ["medical", "spent"], docs, cards, [], [], TODAY, supports=supports)
    assert r["total"] == 80.0 and [i["doc"].id for i in r["items"]] == [2]
    assert any(e["doc"].id == 3 and "price list" in e["reason"] for e in r["excluded"])


def test_short_abbreviations_need_a_whole_word_not_a_state():
    from paperjay.analyze import category_supported
    assert not category_supported("medical", "springfield md 21000 transfer fee")
    assert not category_supported("medical", "shipping label attached")
    assert category_supported("medical", "james wexler md internal medicine")
    assert category_supported("medical", "lab results attached")
