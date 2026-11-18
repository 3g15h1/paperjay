"""Build a synthetic PaperJay library of made-up documents, for measuring the duplicate
gate (spec B.3) and scale behaviour without touching a real library.

    python scripts/synth_library.py --data-dir /tmp/synth [--docs 240] [--seed 7]

Creates the data directory's database, fills it with monthly bills from made-up companies
(each account's bills share a template, an account number, and a series — but every month
has its own total and dates), a few receipts and letters, and a handful of true copies
(the same bill again with a word or two changed). Nothing here is real data.

A manifest lands next to the database (synth-manifest.json):
    {"duplicates": [[copy_id, original_id], ...], "bills": [ids...]}
The gate: none of `bills` may be flagged as a copy; the listed copies should be.
Documents are refiled before the script exits, so series keys and simhashes are stored.
"""
import argparse
import io
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperjay import db, profile  # noqa: E402
from paperjay.library import Library  # noqa: E402
from paperjay.store import FileStore  # noqa: E402

VENDORS = ["Acme Energy", "City Water", "Northwind Insurance", "Example Telecom",
           "Maple Dental", "Union Bank"]
MONTHS = [("January", 1), ("February", 2), ("March", 3), ("April", 4), ("May", 5), ("June", 6),
          ("July", 7), ("August", 8), ("September", 9), ("October", 10), ("November", 11),
          ("December", 12)]

BILL = """{vendor}
Account No: {account}
Statement date: {month_name} 1, {year}
Amount due ${total}
Due date: {month_name} 21, {year}
Service for the house on Maple Street. Your usage this period was typical for the
season and the meter was read on the last day of the month. Please pay the amount
due by the due date shown above. Questions about your bill: call the office during
business hours. A late fee may apply to balances carried past the due date. Thank
you for being a customer of {vendor}."""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--docs", type=int, default=240, help="roughly how many documents to make")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    rng = random.Random(a.seed)
    data = Path(a.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    db.migrate(data / "paperjay.db", data / "backups")
    con = db.connect(data / "paperjay.db")
    store = FileStore(data / "media")
    lib = Library(con)

    def add(text: str, title: str, vendor: str) -> int:
        staged = store.stage(io.BytesIO(f"synthetic {title} {rng.random()}".encode()))
        store.commit(staged, ".pdf")
        doc_id = lib.add(title=title, checksum=staged.checksum, original_name=f"{title}.pdf",
                         original_ext=".pdf", mime_type="application/pdf", size=staged.size,
                         content=text, status="ready")
        cid = lib.get_or_create_label("correspondent", vendor)[0]
        lib.update(doc_id, by="system", correspondent_id=cid)
        return doc_id

    bills: list[int] = []
    duplicates: list[list[int]] = []
    accounts = max(1, a.docs // 12)
    made = 0
    for acct in range(accounts):
        vendor = VENDORS[acct % len(VENDORS)]
        account = f"{1000 + acct} {2000 + acct} {3000 + acct}"
        base = 40.0 + (acct % 9) * 7.5
        for mi, (month_name, month_no) in enumerate(MONTHS):
            total = round(base + ((mi * 13 + acct * 7) % 17) - 8, 2)
            text = BILL.format(vendor=vendor, account=account, month_name=month_name,
                               year=2025, total=f"{total:.2f}")
            doc_id = add(text, f"{vendor} bill {month_name} 2025", vendor)
            bills.append(doc_id)
            made += 1
            if acct % 5 == 0 and mi == 5:
                # a true copy: the same bill again, two words changed
                copy_text = text.replace("typical", "normal").replace("Questions", "Enquiries")
                copy_id = add(copy_text, f"{vendor} bill {month_name} 2025 copy", vendor)
                duplicates.append([copy_id, doc_id])
                made += 1
    for doc_id in [r[0] for r in con.execute("SELECT id FROM documents ORDER BY id")]:
        profile.refile(con, doc_id)
    manifest = {"duplicates": duplicates, "bills": bills, "documents": made}
    (data / "synth-manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest | {"data_dir": str(data)}, indent=1))


if __name__ == "__main__":
    main()
