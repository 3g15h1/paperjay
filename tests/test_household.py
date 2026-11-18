"""Household people (spec C.6): a settings list maps the words people actually use —
nicknames, other names — to a person, and a linked account makes "my" mean them."""
from datetime import date

import pytest

from paperjay import ask, entities, profile, settings

POLICY = """FastNet Insurance
Policy number: PL-8842-1199
Insured: Sam Ortiz
Your policy renews on June 1, 2026. Keep this document with your insurance papers."""

MUTUAL = """City Mutual
Insurance papers: your insurance papers, renewed. Insurance papers for the year ahead.
Insured: Jo Miller
Keep your insurance papers safe."""


def test_settings_round_trip_and_validation(con):
    settings.save(con, {"household_people": [
        {"names": [" Sam Ortiz ", "Sam Reed"], "words": [" Boppa ", ""], "user_ids": [7]}]})
    assert settings.get(con, "household_people") == [
        {"names": ["Sam Ortiz", "Sam Reed"], "words": ["Boppa"], "user_ids": [7]}]
    # A row with no names — the settings page's Add button creates one — is dropped
    # on save, not an error: one blank row mustn't fail the whole save
    # (review 0.4.0 #4).
    settings.save(con, {"household_people": [
        {"words": []}, {"names": []}, {"names": ["  "]}, {"names": ["Jo Miller"]}]})
    assert settings.get(con, "household_people") == [
        {"names": ["Jo Miller"], "words": [], "user_ids": []}]
    for bad in ([{"names": ["X"], "words": "Dad"}], [{"names": ["X"], "user_ids": ["7"]}],
                [{"names": "Jo Miller"}], "not a list"):
        with pytest.raises(ValueError):
            settings.save(con, {"household_people": bad})


@pytest.fixture
def seeded(lib, con):
    pol = lib.add(title="Insurance policy", checksum="a" * 64, original_name="p.pdf",
                  original_ext=".pdf", mime_type="application/pdf", size=10, content=POLICY,
                  status="ready", doc_date="2026-05-01")
    mut = lib.add(title="Insurance papers", checksum="b" * 64, original_name="m.pdf",
                  original_ext=".pdf", mime_type="application/pdf", size=10, content=MUTUAL,
                  status="ready", doc_date="2026-05-02")
    fastnet = lib.get_or_create_label("correspondent", "FastNet")[0]
    mutual = lib.get_or_create_label("correspondent", "City Mutual")[0]
    lib.update(pol, correspondent_id=fastnet)
    lib.update(mut, correspondent_id=mutual)
    profile.refile(con, pol)
    profile.refile(con, mut)
    settings.save(con, {"household_people": [
        {"names": ["Sam Ortiz", "Sam Reed"], "words": ["Boppa"], "user_ids": [7]}]})
    return {"pol": pol, "mut": mut, "con": con}


def test_words_and_my_resolve_to_the_person(seeded):
    con = seeded["con"]
    both = ["Sam Ortiz", "Sam Reed"]   # any name or word stands for all of a person's names
    assert entities.find_entities(con, "show boppa's papers")["people"] == both
    assert sorted(entities.find_entities(con, "sam's papers")["people"]) == both
    assert sorted(entities.find_entities(con, "papers for sam reed")["people"]) == both
    assert entities.find_entities(con, "my insurance papers", user_id=7)["people"] == both
    assert entities.find_entities(con, "my insurance papers", user_id=9)["people"] == []
    assert entities.find_entities(con, "my insurance papers")["people"] == []


def test_suggest_finds_a_household_person_by_their_word(seeded):
    groups = {g["kind"]: g["items"] for g in entities.suggest(seeded["con"], "bop")["groups"]}
    assert {i["name"] for i in groups["person"]} == {"Sam Ortiz", "Sam Reed"}


def test_my_narrows_ask_to_the_linked_person(seeded):
    con = seeded["con"]
    idx = ask.Index()
    idx.refresh(con)
    q = "my insurance papers"

    def second_score(user_id):
        asker = ask.Asker(con, index=idx, today=date(2026, 6, 15), user_id=user_id)
        sources = asker.retrieve(q)["sources"]
        return {s["id"]: s["score"] for s in sources}

    plain = second_score(None)
    mine = second_score(7)
    other = second_score(9)
    # the linked account's "my" damps the other person's policy; anyone else's doesn't
    assert mine[seeded["mut"]] < plain[seeded["mut"]]
    assert other[seeded["mut"]] == plain[seeded["mut"]]
    assert mine[seeded["pol"]] == plain[seeded["pol"]]  # Sam's own policy is untouched at the top
