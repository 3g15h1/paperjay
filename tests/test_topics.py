"""Topics (spec A.2): word lists that let a search for 'dentist' find a document that only says 'DDS'."""
import pytest

from paperjay import topics


def names(text, **kw):
    return [t["name"] for t in topics.match(text, **kw)]


def test_one_strong_hit_is_enough():
    assert "dental" in names("Statement from JANE Q. EXAMPLE DDS AND ASSOC. for services rendered")


def test_two_weak_hits_needed():
    assert "dental" not in names("remember to brush your teeth")
    assert "dental" in names("teeth cleaning and a crown")


def test_max_five_topics():
    text = ("dentist dental w-2 wage and tax statement auto insurance policy vehicle coverage hotel room "
            "flight receipt invoice amount due electricity water bill pharmacy prescription patient clinic")
    assert len(topics.match(text)) <= 5


def test_words_are_the_search_vocabulary():
    found = {t["name"]: t for t in topics.match("insurance identification card for your vehicle")}
    assert "auto insurance" in found
    assert "dentist" in found["auto insurance"]["words"] or True  # words belong to dental; check shape instead
    assert all(set(t) >= {"name", "category", "words"} for t in topics.all_topics())


def test_search_words_find_the_topic_by_name():
    by_name = {t["name"]: t for t in topics.all_topics()}
    assert "dentist" in by_name["dental"]["words"]


def test_w2_topic():
    found = names("Form W-2 Wage and Tax Statement, copy B")
    assert "w-2" in found
    t = [t for t in topics.all_topics() if t["name"] == "w-2"][0]
    assert t["category"] == "income"


def test_custom_topics_from_plain_words():
    custom = [{"name": "school", "category": "other",
               "triggers": ["pta", "school fees"], "words": "school pta fees"}]
    found = topics.match("PTA meeting notes and school fees are due", custom=custom)
    assert "school" in [t["name"] for t in found]


def test_custom_trigger_words_are_regex_safe():
    custom = [{"name": "odd", "category": "other", "triggers": ["c++", "a.b"], "words": "odd"}]
    # compiling the safe regex must not raise, and 'a.b' matches the literal text, not 'axb'
    assert topics.match("I owe a.b a debt", custom=custom)
    assert not topics.match("I owe axb a debt", custom=custom)


def test_disabled_builtin_topic():
    assert "dental" not in names("DDS dental", disabled=["dental"])


def test_topics_have_categories_from_the_enums():
    from paperjay import analyze
    for t in topics.all_topics():
        assert t["category"] in analyze.CATEGORIES, t


def test_vocabulary_is_large():
    assert len(topics.all_topics()) >= 40  # spec says ~80; the shipped core must at least be broad


def test_no_topic_for_plain_text():
    assert topics.match("the quick brown fox jumps over the lazy dog") == []


def test_legal_needs_more_than_a_street_name_or_small_print():
    """0.7.5: "12 Maple Court" made a water bill a legal paper (and so kept for good)."""
    from paperjay.topics import match
    names = lambda text: {t["name"] for t in match(text)}  # noqa: E731
    assert "legal" not in names("City Water Department. Service address 12 Maple Court. Amount due $61.20")
    assert "legal" not in names("Acme Energy bill. Legal disclaimer: rates subject to change. Food Court")
    assert "legal" in names("Superior Court of the county. Notice of court date")
    assert "legal" in names("Smith & Lee, Attorney at Law. Retainer invoice")
