"""Topics (spec A.2): search words that aren't in the document.

A topic is a small vocabulary: patterns that mean a document is *about* something (triggers) and
the words people actually type when looking for it (`words`, which go into the search index, so
"dentist" finds a statement that only ever says "DDS"). A topic applies on one strong trigger hit
or two weak ones; at most MAX_TOPICS per document.

The list ships with the app and is generic English. Households add their own in the `custom_topics`
setting — plain words there are turned into safe word-boundary patterns in code, never raw regex.
"""
import re

MAX_TOPICS = 5


class Topic:
    __slots__ = ("name", "category", "strong", "weak", "words")

    def __init__(self, name: str, category: str, strong: str = "", weak: str = "", words: str = ""):
        self.name, self.category = name, category
        self.strong = re.compile(strong, re.I) if strong else None
        self.weak = re.compile(weak, re.I) if weak else None
        self.words = words

    def as_dict(self) -> dict:
        return {"name": self.name, "category": self.category, "words": self.words}


# (name, category, strong triggers, weak triggers, search words)
_DEFS = [
 ("dental", "medical", r"\bdds\b|dental|dentist|orthodont|periodont|endodont",
  r"\bteeth\b|\btooth\b|hygien|crown|filling|x-?rays?", "dentist dental teeth tooth cleaning"),
 ("medical visit", "medical", r"patient (?:name|id|portal)|clinic visit|office visit|examination",
  r"\bpatient\b|clinic|physician|doctor", "doctor visit medical clinic checkup"),
 ("pharmacy", "medical", r"pharmacy|prescription (?:refill|number|no)|rx #",
  r"prescription|refill|pharmacist", "pharmacy prescription medicine refill drugstore"),
 ("lab results", "medical", r"lab(?:oratory)? (?:results|report|test)|blood (?:test|work)|specimen",
  r"\blab\b|blood|urinalysis|cholesterol", "lab results blood test labs"),
 ("vision", "medical", r"optometr|ophthalmol|eye exam|vision (?:exam|test)",
  r"glasses|eyeglass|contact lens|\bvision\b", "eye glasses vision optometrist"),
 ("mental health", "medical", r"psycholog|psychiatr|therapy session|counsel(?:ing|or)",
  r"therapist|behavio(?:u)?ral health", "therapy psychologist counseling mental health"),
 ("vaccination", "medical", r"vaccin|immunization record|immunisation",
  r"\bshot record\b|booster", "vaccine vaccination shots immunization"),
 ("health insurance", "insurance", r"health (?:insurance|plan)|medical insurance|ppo\b|hmo\b|explanation of benefits",
  r"member id|subscriber|group (?:no|number)|claims?\b", "health insurance eob benefits member card"),
 ("auto insurance", "insurance", r"auto (?:insurance|policy)|vehicle coverage|insurance identification card|no-?fault insurance",
  r"policy (?:no|number)|vin\b|vehicle", "car insurance auto policy id card"),
 ("home insurance", "insurance", r"homeowners? (?:insurance|policy)|home insurance|dwelling coverage",
  r"policy (?:no|number)|premium", "home insurance homeowners policy house"),
 ("life insurance", "insurance", r"life insurance|term life|whole life",
  r"beneficiar|policy (?:no|number)", "life insurance policy beneficiary"),
 ("insurance payment", "insurance", r"premium (?:due|notice|statement)|insurance bill",
  r"premium|policy", "insurance premium bill payment"),
 ("w-2", "income", r"\bw-?2\b|wage and tax statement",
  r"employer identification|federal income tax withheld", "w2 w-2 wages salary income tax form"),
 ("pay stub", "income", r"pay ?stub|pay ?check|earnings statement|gross pay",
  r"net pay|pay period|direct deposit", "pay stub paycheck paystub salary earnings"),
 ("1099", "income", r"\b1099\b|nonemployee compensation|miscellaneous income",
  r"payer|recipient", "1099 freelance contractor income tax"),
 ("tax return", "tax", r"tax return|form 1040|internal revenue service|\birs\b",
  r"refund|taxpayer|tax year", "tax return irs 1040 taxes refund"),
 ("tax payment", "tax", r"tax (?:bill|payment|assessment)|property tax (?:bill|statement)",
  r"treasurer|assessor|parcel", "tax bill property tax payment"),
 ("mortgage", "finance", r"mortgage|escrow (?:analysis|statement)",
  r"principal and interest|loan (?:no|number)", "mortgage home loan escrow house payment"),
 ("bank statement", "finance", r"bank statement|account statement|checking account|savings account",
  r"\bbank\b|account (?:no|number)|routing", "bank statement checking savings account"),
 ("credit card", "finance", r"credit card|card statement|minimum payment due",
  r"credit limit|apr\b|cardmember", "credit card statement bill payment"),
 ("loan", "finance", r"loan (?:statement|agreement|account)|installment loan",
  r"principal|interest rate|loan (?:no|number)", "loan payment statement balance"),
 ("investment", "finance", r"investment (?:account|statement)|brokerage|retirement account|\bira\b|401\(?k\)?",
  r"portfolio|dividend|shares", "investment retirement 401k ira brokerage stocks"),
 ("car loan", "vehicle", r"auto loan|vehicle (?:loan|financing)|car payment",
  r"\bvin\b|lessee|lease", "car loan auto financing lease payment"),
 ("vehicle registration", "vehicle", r"vehicle registration|registration renewal|license plate",
  r"\bplate\b|\bvin\b|registration", "car registration plates renewal dmv"),
 ("vehicle purchase", "vehicle", r"vehicle (?:purchase|sale)|bill of sale|dealer (?:invoice|transaction)|title transfer",
  r"dealer|odometer|\bvin\b", "car purchase dealer receipt title"),
 ("car repair", "vehicle", r"auto (?:repair|service)|vehicle (?:repair|service)|oil change",
  r"mechanic|brakes?|tires?|mileage", "car repair mechanic oil change service"),
 ("electricity", "utilities", r"electric (?:bill|service|utility)|\bkwh\b|meter (?:reading|number)",
  r"electric|energy|power company", "electric bill electricity power utility"),
 ("gas bill", "utilities", r"natural gas|gas (?:bill|service|utility)|\btherms?\b",
  r"gas company|heating", "gas bill natural gas utility heating"),
 ("water bill", "utilities", r"water (?:bill|service|utility)|sewer|water company",
  r"water|sewerage", "water bill sewer utility"),
 ("internet", "utilities", r"internet|broadband|wifi|fiber (?:internet|optic)",
  r"modem|router|data plan", "internet wifi broadband cable"),
 ("phone bill", "utilities", r"mobile (?:bill|plan|phone)|cell ?phone|wireless (?:bill|plan|account)",
  r"wireless|data (?:usage|plan)", "phone bill mobile cell wireless"),
 ("tv service", "utilities", r"cable (?:tv|bill|service)|satellite tv|streaming (?:service|subscription)",
  r"cable|channel", "cable tv satellite television"),
 ("rent", "home", r"\brent\b|landlord|lease agreement|rental (?:agreement|payment)",
  r"tenant|apartment|monthly rent", "rent lease landlord apartment"),
 ("home repair", "home", r"plumb|roofing|hvac|furnace|air conditioning|handyman",
  r"repair|contractor|installation", "plumbing roofing hvac repair contractor home"),
 ("flooring", "home", r"flooring|hardwood floor|laminate|tile installation",
  r"\bfloor\b|carpet|shoemoulding", "flooring floor hardwood carpet"),
 ("lawn care", "home", r"lawn|landscaping|mowing|yard (?:service|care)",
  r"property services|grass", "lawn mowing yard landscaping"),
 ("appliance", "home", r"appliance|refrigerator|washing machine|dishwasher|water heater",
  r"warranty|installation", "appliance refrigerator washer dryer warranty"),
 ("moving", "home", r"moving (?:company|service)|relocation|storage unit",
  r"movers|packing", "moving movers relocation storage"),
 ("hotel", "travel", r"hotel|motel|inn\b|resort|lodging",
  r"room charge|check-?in|folio", "hotel room stay travel receipt"),
 ("flight", "travel", r"airline|flight|boarding pass|airfare|itinerary",
  r"airport|departure|arrival", "flight airline ticket plane travel"),
 ("car rental", "travel", r"rental car|car rental|rent-?a-?car",
  r"pick-?up|drop-?off", "rental car hire travel"),
 ("passport", "travel", r"passport",
  r"travel document", "passport id travel"),
 ("receipt", "shopping", r"\breceipt\b|cashier|thank you for (?:shopping|your purchase)",
  r"change due|subtotal|store", "receipt purchase store proof"),
 ("warranty", "shopping", r"warrant|guarantee|protection plan",
  r"coverage period|serial (?:no|number)", "warranty guarantee protection plan"),
 ("coupon", "shopping", r"coupon|reward certificate|gift card|promo(?:tion)? code",
  r"discount|savings", "coupon reward certificate gift card discount"),
 ("order confirmation", "shopping", r"order (?:confirmation|details|number)|your order|order #",
  r"shipped|delivery|tracking", "order confirmation shipping delivery tracking"),
 ("return", "shopping", r"return (?:label|receipt|authori[sz]ation)|refund (?:notice|receipt)",
  r"refund|exchange", "return refund exchange"),
 ("electronics", "shopping", r"iphone|android|smartphone|tablet|laptop|computer",
  r"serial (?:no|number)|imei|device", "phone iphone computer electronics device"),
 ("clothing", "shopping", r"clothing|apparel|shoes|sneakers",
  r"size|fashion", "clothes clothing shoes shopping"),
 ("groceries", "food", r"grocer|supermarket|market (?:receipt|basket)",
  r"grocery|food items", "groceries grocery supermarket food"),
 ("restaurant", "food", r"restaurant|caf[eé]|diner|bakery",
  r"server|tip|dine", "restaurant food dining receipt meal"),
 ("school", "other", r"school|university|college|tuition",
  r"student|enrol(?:l)?ment|semester", "school tuition student education"),
 ("legal", "other", r"attorney|law (?:firm|office)|legal|court|notary",
  r"counsel|esquire|\bp\.?c\.?\b", "lawyer attorney legal law firm"),
 ("government", "other", r"department of|secretary of state|county clerk|municipal|social security (?:administration)?",
  r"government|official notice", "government official notice county state"),
 ("pet", "other", r"veterinar|pet (?:clinic|care|insurance)|\bvet\b",
  r"dog|cat|rabies", "vet pet dog cat veterinary"),
 ("gym", "other", r"gym|fitness (?:center|centre|club)|workout",
  r"membership|trainer", "gym fitness membership workout"),
 ("subscription", "other", r"subscription|membership (?:renewal|fee)|auto-?renew",
  r"plan|monthly fee", "subscription membership renewal plan"),
 ("childcare", "other", r"daycare|child ?care|babysit|nursery school",
  r"child|toddler", "daycare childcare kids"),
 ("donation", "other", r"donation|charitable|donor",
  r"contribution|nonprofit|501\(c\)", "donation charity gift nonprofit"),
 ("utility account", "utilities", r"account (?:number|no\.?)|customer (?:number|account)",
  r"service address|account holder", "account number customer service"),
 ("bill due", "finance", r"payment due|amount due|balance due|please pay",
  r"due date|pay by", "bill due payment amount owe"),
 ("estimate", "finance", r"estimate|quotation|quote for|proposal for services",
  r"valid for|approximate cost", "estimate quote proposal price"),
]

TOPICS: list[Topic] = []
for _name, _cat, _strong, _weak, _words in _DEFS:
    TOPICS.append(Topic(_name, _cat, _strong, _weak, _words))


def _safe_words_pattern(words: str) -> re.Pattern | None:
    """Plain words from a household -> one word-boundary regex of the literal words (never raw regex)."""
    parts = [re.escape(w) for w in re.split(r"[,\s]+", (words or "").lower()) if w]
    if not parts:
        return None
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I)


def custom_topics(setting_value) -> list[Topic]:
    """Topics from the `custom_topics` setting: [{name, category, triggers (plain words), words}]."""
    out = []
    for entry in setting_value or []:
        try:
            name = str(entry["name"]).strip()
            category = str(entry.get("category") or "other").strip()
            words = str(entry.get("words") or "")
            trig = entry.get("triggers") or []
            if isinstance(trig, str):
                trig = [trig]
            pieces = []
            for t in trig:
                pieces += [w for w in re.split(r"[,\s]+", str(t).lower()) if w]
            strong = _safe_words_pattern(" ".join(pieces))
            if not name or strong is None:
                continue
            topic = Topic(name, category, "", "", words)
            topic.strong = strong
            out.append(topic)
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def all_topics(custom=None, disabled=()) -> list[dict]:
    """The vocabulary as plain dicts (built-ins minus the disabled ones, plus household topics)."""
    off = {str(d).lower() for d in disabled or []}
    out = [t.as_dict() for t in TOPICS if t.name.lower() not in off]
    for t in custom or []:
        if t.name.lower() not in off:
            out.append(t.as_dict())
    return out


def match(text: str, custom=None, disabled=()) -> list[dict]:
    """Topics that apply to `text`: one strong hit or two weak hits each, strongest first, max 5.
    `custom` is a list of Topic objects from custom_topics(); returns plain dicts."""
    off = {str(d).lower() for d in disabled or []}
    scored = []
    pool = [t for t in TOPICS if t.name.lower() not in off]
    extra = []
    for t in custom or []:  # Topic objects, or raw `custom_topics` setting entries
        extra += custom_topics([t]) if isinstance(t, dict) else [t]
    pool += [t for t in extra if t.name.lower() not in off]
    flat = " " + " ".join((text or "").split()) + " "
    for t in pool:
        strong = len(t.strong.findall(flat)) if t.strong else 0
        weak = len(t.weak.findall(flat)) if t.weak else 0
        if strong >= 1 or weak >= 2:
            scored.append((strong * 2 + weak, t))
    scored.sort(key=lambda x: (-x[0], x[1].name))
    return [t.as_dict() for _, t in scored[:MAX_TOPICS]]
