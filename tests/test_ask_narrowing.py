"""Ask entity narrowing (spec C.4): naming a company (by an alias), or a number by its
last digits, re-ranks — matching documents ×1.5, the rest ×0.6 — but never filters."""
from datetime import date

import pytest

from paperjay import ask, profile

POLICY = """FastNet Insurance
Policy number: PL-8842-1199
Insured: Sam Ortiz
Your policy renews on June 1, 2026. Keep this document with your insurance papers."""

TOURS = """Harbor Tours
Fast net rental: rent a fast net for the fishing trip.
Fast net fast net fast net — the fastest net on the harbour."""

CAFE = """Harbor Cafe
Account summary enclosed.
Lunch total $12.00. Thank you for dining with us."""

WATER = """City Water
Account papers: keep these account papers with your account file.
Your water account papers are renewed every spring."""


def _add(lib, text, title, checksum):
    return lib.add(title=title, checksum=checksum, original_name="doc.pdf", original_ext=".pdf",
                   mime_type="application/pdf", size=10, content=text, status="ready",
                   doc_date="2026-05-01")


def _asker(con):
    idx = ask.Index()
    idx.refresh(con)
    return ask.Asker(con, index=idx, today=date(2026, 6, 15))


def _order(r):
    return [s["id"] for s in r["sources"]]


def test_alias_mention_narrows_to_the_company(lib, con):
    pol = _add(lib, POLICY, "Insurance policy", "a" * 64)
    tours = _add(lib, TOURS, "Net rental", "b" * 64)
    fastnet = lib.get_or_create_label("correspondent", "FastNet")[0]
    harbor = lib.get_or_create_label("correspondent", "Harbor Tours")[0]
    lib.update(pol, correspondent_id=fastnet)
    lib.update(tours, correspondent_id=harbor)
    profile.refile(con, pol)
    profile.refile(con, tours)
    asker = _asker(con)
    q = "fast net co policy papers"
    # word matches alone put the rental leaflet first: the policy never says "fast net"
    assert _order(asker.retrieve(q)) == [tours, pol]
    lib.add_alias(fastnet, "Fast Net Co")
    r = asker.retrieve(q)
    # naming the company by its other name narrows to it — softly: both still answer
    assert _order(r) == [pol, tours]


def test_number_ending_narrows(lib, con):
    cafe = _add(lib, CAFE, "Cafe receipt", "c" * 64)
    water = _add(lib, WATER, "Water account papers", "d" * 64)
    asker = _asker(con)
    q = "papers for the account ending 9012"
    assert _order(asker.retrieve(q)) == [water, cafe]
    # a fact card (here: an AI-read number) ties the cafe receipt to that account
    con.execute("INSERT INTO index_terms(kind, norm, term, label, document_id) VALUES "
                "('number', '55512349012', '555 1234 9012', 'account number', ?)", (cafe,))
    assert _order(asker.retrieve(q)) == [cafe, water]


def test_question_naming_nothing_keeps_word_order(lib, con):
    pol = _add(lib, POLICY, "Insurance policy", "e" * 64)
    tours = _add(lib, TOURS, "Net rental", "f" * 64)
    asker = _asker(con)
    r = asker.retrieve("fast net rental papers")
    assert _order(r)[0] == tours  # no entity is named, so nothing is boosted or damped
    assert asker.retrieve("zzzz qqqq")["refused"] in (True, False)  # and it never crashes
