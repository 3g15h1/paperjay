"""Totals across documents ("How much did I spend on medical in 2025?").

The numbers come from fact cards and are added up here, never by the model, and every item is
listed and cited, so the result can be checked.
"""
import re
from datetime import date

from .textutil import money

TOTAL_Q = re.compile(
    r"\b(spen[dt]|spending)\b|\b(in total|altogether|all together|combined)\b"
    r"|\btotal (?:spen[dt]|spending|paid|cost of|of)\b"
    r"|\bhow much (?:did|have) (?:i|we) (?:pa(?:y|id))\b.*\b(?:in|during|since|for) "
    r"(?:(?:19|20)\d\d|this year|last year|[a-z]+ (?:19|20)\d\d)\b", re.I)

CATEGORY_WORDS = {
    "medical": "medical doctor doctors health healthcare hospital clinic pharmacy prescription prescriptions dental "
               "dentist lab labs patient therapy",
    "insurance": "insurance premium premiums",
    "utilities": "utility utilities electric electricity power gas water sewer internet cable",
    "shopping": "shopping clothes clothing electronics purchases gadgets",
    "home": "home house lawn landscaping repairs flooring furniture renovation",
    "vehicle": "car cars auto vehicle vehicles registration",
    "tax": "tax taxes irs",
    "travel": "travel hotel hotels flight flights trip trips vacation",
    "food": "food groceries grocery restaurant restaurants dining",
    "finance": "loan loans bank banking credit",
    "income": "income wages salary",
}
_WORD_TO_CAT = {w: cat for cat, words in CATEGORY_WORDS.items() for w in words.split()}
SKIP_KINDS = {"insurance", "id", "tax", "pay"}  # their numbers aren't spending (coverage limits, wages…)
KIND_CATEGORY = {"medical": "medical"}  # a medical bill is medical spending, whatever category the model chose
FILLER = set("spend spent spending total totals altogether together combined much money pay paid cost costs "
             "year this last on at for in during since all so far yet already overall "
             # the kind of paper or money, not a company: "medical bills", "doctor invoices"
             "bill bills invoice invoices statement statements receipt receipts expense expenses payment payments "
             "charge charges fee fees amount amounts visit visits".split())


# a list of prices or an estimate shows what something would cost, not money that was spent
NOT_SPENDING = re.compile(r"\b(?:prices?|fees?|rates?|charges?) (?:are |is )?subject to change\b|\bfee schedule\b|"
                          r"\bprice ?list\b|\bpricing guide\b|\bcost estimate\b|\bgood faith estimate\b|\bquotation\b",
                          re.I)


def is_total_question(question: str) -> bool:
    return bool(TOTAL_Q.search(question))


def period(question: str, years: list[int], months: list[int], today: date) -> tuple[list[int], list[int]]:
    q = question.lower()
    years = list(years)
    if "this year" in q:
        years.append(today.year)
    if "last year" in q:
        years.append(today.year - 1)
    return sorted(set(years)), months


def _item_date(doc, card) -> date | None:
    for label in ("paid", "issued", "service"):
        for d in card.get("dates", []):
            if d["label"] == label:
                return date.fromisoformat(d["date"])
    return doc.created_date


def compute(question: str, words: list[str], docs, cards, years: list[int], months: list[int], today: date,
            supports=None) -> dict:
    """words: the question's content words (lower case, stop words removed).
    supports(category, doc): does the document's own text back up that category? A category the AI chose is
    only trusted when it does (a car registration filed under "medical" isn't medical spending)."""
    years, months = period(question, years, months, today)
    cats = sorted({_WORD_TO_CAT[w] for w in words if w in _WORD_TO_CAT})
    vendor_words = [w for w in words if w not in _WORD_TO_CAT and w not in FILLER and not w.isdigit()]

    def in_period(d: date | None) -> bool:
        if not d:
            return not (years or months)
        return (not years or d.year in years) and (not months or d.month in months)

    def vendor_match(doc, card) -> bool:
        hay = " ".join(filter(None, [card.get("vendor") if card else "", doc.correspondent or "", doc.title])).lower()
        return all(re.search(rf"\b{re.escape(w)}", hay) for w in vendor_words)

    items, pending, set_aside = [], 0, []
    for doc in docs:
        card = cards.get(doc.id)
        if not card:
            if in_period(doc.created_date) and (not vendor_words or vendor_match(doc, None)):
                pending += 1
            continue
        if card.get("total") is None or card.get("kind") in SKIP_KINDS:
            continue
        when = _item_date(doc, card)
        cat = card.get("category") if card.get("category") in cats else KIND_CATEGORY.get(card.get("kind"))
        in_cats = cat in cats
        if not in_period(when) or (cats and not in_cats) or not vendor_match(doc, card):
            continue
        if cats and supports and not supports(cat, doc):
            continue
        if card.get("duplicate_of"):
            # a copy of another document (spec B.3): never counted twice; other series
            # members (last month's bill) are not copies and still count
            set_aside.append({"doc": doc, "amount": card["total"], "date": when,
                              "vendor": card.get("vendor") or doc.title,
                              "reason": "a copy of another document"})
            continue
        if NOT_SPENDING.search(getattr(doc, "content", "") or ""):
            set_aside.append({"doc": doc, "amount": card["total"], "date": when, "vendor": card.get("vendor") or doc.title,
                              "reason": "a price list or estimate, not a payment"})
            continue
        items.append({"doc": doc, "amount": card["total"], "date": when, "vendor": card.get("vendor") or doc.title,
                      "kind": card.get("kind"), "category": card.get("category"),
                      "ids": {re.sub(r"\W", "", i["value"]).lower() for i in card.get("ids", [])}})

    items.sort(key=lambda i: (i["date"] or date.min, i["doc"].id))
    kept, excluded = [], list(set_aside)
    for i in items:
        dup = next((k for k in kept if _same_charge(k, i)), None)
        if dup:
            excluded.append({**i, "reason": f"same amount and vendor as “{dup['doc'].title}” (likely the same charge)"})
        else:
            kept.append(i)
    return {"total": round(sum(i["amount"] for i in kept), 2), "items": kept, "excluded": excluded,
            "pending": pending, "categories": cats, "vendors": vendor_words, "years": years, "months": months}


def _same_charge(a: dict, b: dict) -> bool:
    """A receipt and a statement (or two copies) of one payment: same amount and vendor within 60 days,
    and either different kinds of document or a shared ID number. Two receipts with different order
    numbers are two purchases."""
    if a["amount"] != b["amount"] or re.sub(r"\W", "", a["vendor"]).lower() != re.sub(r"\W", "", b["vendor"]).lower():
        return False
    if a["date"] and b["date"] and abs((a["date"] - b["date"]).days) > 60:
        return False
    return a["kind"] != b["kind"] or bool(a["ids"] & b["ids"])


def describe(result: dict, month_names: list[str], cites: str = "") -> str:
    """The answer card's text; `cites` ("[1][2]") goes after the total."""
    scope = []
    if result["categories"]:
        scope.append("on " + " and ".join(result["categories"]))
    if result["vendors"]:
        scope.append("matching " + " ".join(f"“{v}”" for v in result["vendors"]))
    if result["months"] or result["years"]:
        when = ", ".join(month_names[m].title() for m in result["months"])
        when = (when + " " if when else "") + ", ".join(str(y) for y in result["years"])
        scope.append("in " + when.strip())
    scope = (" " + " ".join(scope)) if scope else ""
    n = len(result["items"])
    if not n:
        text = f"I couldn't find any amounts{scope}."
    else:
        text = f"Total{scope}: {money(result['total'])} across {n} document{'s' if n != 1 else ''} {cites}".rstrip() + "."
    if result["pending"]:
        p = result["pending"]
        text += (f" {p} document{'s' if p != 1 else ''}{' from that period' if result['years'] or result['months'] else ''}"
                 f" {'haven' if p != 1 else 'hasn'}'t been analysed yet, so the total may be incomplete.")
    return text
