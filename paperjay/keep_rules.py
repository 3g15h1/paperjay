"""The default keep rules (see keeping.py). Apart from keeping.py so settings.py can use them without
importing the rest."""

# First match wins. years None = keep for good. from: "year_end" (end of the document's year) or
# "expires" (its expiry date, falling back to the year end when it has none).
DEFAULT_RULES = [
    {"name": "Contracts, IDs, deeds, investments and medical records", "years": None, "from": "year_end",
     "categories": [], "kinds": ["contract", "id", "medical"],
     "topics": ["vehicle purchase", "mortgage", "investment", "passport", "legal", "vaccination", "lab results"]},
    {"name": "Tax returns and the papers behind them", "years": 7, "from": "year_end",
     "categories": ["tax"], "kinds": ["tax"],
     "topics": ["w-2", "1099", "tax return", "tax payment", "donation", "childcare"]},
    {"name": "Insurance policies and cards", "years": 1, "from": "expires",
     "categories": ["insurance"], "kinds": ["insurance"], "topics": []},
    {"name": "Medical bills and receipts", "years": 3, "from": "year_end",
     "categories": ["medical"], "kinds": [], "topics": []},
    {"name": "Pay slips", "years": 1, "from": "year_end", "categories": [], "kinds": ["pay"],
     "topics": ["pay stub"]},
    {"name": "Bank and credit card statements", "years": 1, "from": "year_end", "categories": [], "kinds": [],
     "topics": ["bank statement", "credit card"]},
    {"name": "Utility and phone bills", "years": 1, "from": "year_end", "categories": ["utilities"],
     "kinds": [], "topics": []},
    {"name": "Shop receipts", "years": 1, "from": "year_end", "categories": ["shopping", "food"], "kinds": [],
     "topics": []},
]
