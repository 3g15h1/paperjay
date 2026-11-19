# The tax pack

Everything for one tax year in one zip: for your accountant, or for filling in the return yourself.

**Settings → Tax pack** (or open `/taxpack`):

1. **Pick the tax year.** PaperJay starts with the last one that has ended.
2. **Pick what goes in.** Tap categories (Medical, Income, Tax…), topics (donation, child care, mortgage,
   W-2…) and tags (for example your own "Taxes" tag). Each shows how many documents of that year it has. A
   document is in the pack if it matches any of them. Your choice is remembered on this device.
3. **Check the list and the totals**, then **Download the tax pack**.

The zip holds:

```
PaperJay tax pack 2025/
  README.txt                   the tax year, and the totals per category
  summary.csv                  one row per document (opens in any spreadsheet)
  Medical/2025-03-02 Corner Pharmacy prescription receipt [14].pdf
  Income/2026-01-24 W-2 2025 Northwind Logistics [11].pdf
  …
```

Each document is its searchable PDF (or the original file when there isn't one). `summary.csv` has the date,
title, company, type, category, amount, whether the amount was added to the total (and why not), the paper
number on the shelf, and the file's place in the zip.

## Which documents belong to the year

- A document belongs to the tax year its date falls in.
- Tax forms usually arrive after the year ends (a W-2 or 1099 in January, a P60 in May). A tax or income
  document dated up to four months after the year counts for the year if its text names that year.
- Your tax year starts on **January 1** unless you change it. Admins can change it on the Tax pack page
  (April 6 for the UK, July 1 for Australia, April 1, October 1), or set `TAX_YEAR_START=MM-DD` for a new
  install. A year that doesn't start on January 1 is written like "2025–26".

## The totals

Only payments are added up, the same as totals in Ask. Copies of another document, price lists and
estimates, pay slips, tax forms and insurance papers are listed with their amount but not added. The amounts
come from what PaperJay read in each document: check them before you rely on them.

Anyone who can sign in can make a tax pack (they can open every document anyway). Access keys for AI
assistants can't, the same as for the full export.

## A spreadsheet of any list

In the **Library**, open **Filters** and tap **Download list** to save every document that matches the
search and filters as a CSV file. With documents selected, **Download list** in the bar saves just those.
