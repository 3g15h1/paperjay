# Changelog

## 0.7.7

- **Bills that are written in the email, not attached.** Each mailbox (Settings → Email) now has **The
  email itself**: save it as a PDF **when nothing is attached**, or **always** (next to its attachments).
  The PDF shows the sender, date, subject and text of the email and is filed like any other document, so
  "Your November bill: $61.20 due" ends up in Coming up too. Pictures and links in the email are never
  loaded. The default stays as before: only the attachments are taken.
- The Docker image and the Proxmox install now include the DejaVu font so emails in any language show;
  updating a Proxmox install adds it.
- Changes the database (a new column for the mailbox choice); PaperJay backs it up first, as always.

## 0.7.6

- **The words you searched for are marked on the page.** Open a document from a Library search or from
  an answer's sources and the words show in yellow right on the page images, so you see at once where
  the policy number or the amount is. A line above the pages says how many there are and on which pages
  (tap a page number to jump there); **Clear** takes the marks away. The Text tab marks them too.
- Works on turned pages, and finds numbers however they're written (EM-4471-2290 or EM 4471 2290).

## 0.7.5

- **Check documents one after another.** Open a document from **Review → Needs review** and a bar at the
  top says "Checking 2 of 5", with **Looks right & next**, **Trash & next** (with Undo) and **Skip**. Fix
  anything on the right first; it's saved as you go. On a computer, press **A** for looks right and **S**
  to skip. After the last one you're back on the Review page.
- **Fixed: a street name made a bill a legal paper.** The "legal" topic was given to any document with
  "Court" in it (12 Maple Court) or the word "legal" in its small print, and so its paper was kept for
  good. It now needs real legal words (an attorney, a court date, a law firm). Every document is filed
  again once after updating, which takes a few minutes in the background.

## 0.7.4

- **Follow-up questions about something else.** After "How much was the electric bill?", asking "and the
  water bill?" now answers straight away with the water bill's amount. Before, PaperJay only knew what you
  wanted when the follow-up said it again. (Follow-ups that point back, like "and the other car?", still
  go to the AI, which can tell which one you mean.)
- **Instant answers pick the right document more often.** When several documents could answer, the one
  whose own words name what you asked about wins (the water bill for "water", not the electric bill
  filed under the same utility topics).

## 0.7.3

- **Fix pages after a document was added.** On a document's page, **⋯ → Fix pages** shows every page: turn
  a page that came out sideways or upside down, take out pages you don't want (a blank back, an advert),
  move pages, or tap ✂ between two pages to split it where a new document starts (two letters scanned
  as one).
- **Join documents.** Select documents in the Library, in the order their pages should go, and tap
  **Join into one** (the front and back of a form scanned separately, or a bill and its second page).
- PaperJay keeps every original exactly as it arrived, so these never change a file: they make new
  documents with the same title, date, company, type, tags, notes and paper number, which are read again
  in a minute or so. The old document goes to the Trash, where you can restore it for 30 days.
- Access keys need both "create" and "delete" to change pages.

## 0.7.2

- **Warranties.** PaperJay reads how long a purchase's warranty lasts from the receipt or invoice
  ("2-year limited warranty", "Warranty: 24 months", "90 day guarantee", "warranty valid until
  03/14/2027", "lifetime warranty") and counts it from the day you bought it. When a receipt mentions
  several (the maker's year and an extended plan), the longest counts.
- **Library → Warranties** lists every running warranty, the ones ending soonest first, with what it was
  read from, and the ones that have ended.
- **A warranty that ends soon shows under Coming up**, in your calendar feed and in Home Assistant
  (`label` is `warranty`).
- **Its receipt is kept until the warranty ends**, even when the keep rules would let it go sooner: it's
  your proof of purchase.
- **Ask knows them:** "When does the warranty on my dishwasher end?" gets an instant answer.
- **Fix one on the document's page:** pick a date, a lifetime warranty, no warranty, or go back to what the
  text says.
- Changes the database (a new column for your own warranty dates); PaperJay backs it up first, as always.

## 0.7.1

- **"Can I shred this?"** PaperJay now knows how long to keep each kind of paper: bank statements and
  utility bills a year, medical bills three years, tax papers seven years, insurance a year after it
  expires, and contracts, IDs and medical records for good. A paper no rule covers is kept for good.
- **Paper to shred** (Settings, and on the start page when there is some) lists the papers on your shelf
  that have been kept long enough, by their number. Tap **Shredded** once it's gone (with **Undo**), or
  **Keep it** to keep that one for good. Only the paper goes: the digital copy stays in PaperJay. A
  shredded paper's number is never given to a new paper.
- **Every document says how long to keep its paper**, and which rule says so. Change it for one document:
  keep it for good, until a date you choose, or back to the rules.
- **Settings → Keeping paper** lists the rules. Change the years, what each rule covers and their order,
  add your own, or go back to the defaults. The defaults follow common advice for US households; what you
  must keep differs by country, so check and adjust them.
- Changes the database (three new columns for keeping and shredding); PaperJay backs it up first, as
  always. Exports include the new fields, and importing an export brings them back.

## 0.7.0

- **A tax pack for your accountant.** Settings → Tax pack (or `/taxpack`) gathers everything for one tax
  year: pick the year, then tap the categories, topics and tags that belong in it (medical, income, tax,
  donations, child care… are chosen to start with). The page lists the documents and adds up the payments in
  each category. **Download the tax pack** gives one zip with the documents in a folder per category (the
  searchable PDF where there is one), a spreadsheet of every document's date, company, amount and paper
  number, and a short README with the totals.
- **Your tax year, wherever you live.** The tax year starts on January 1 unless you change it on the Tax pack
  page (April 6 for the UK, July 1 for Australia, or `TAX_YEAR_START=04-06`). Tax forms that arrive after the
  year ends (a W-2 in January) count for the year they name.
- Only payments are added up: copies, price lists, pay slips and tax forms are listed with their amount but
  not added, and the spreadsheet says why.
- **Download the Library list as a spreadsheet.** Under Filters, **Download list** saves every document that
  matches the search and filters as a CSV file; with documents selected, **Download list** saves just those.

## 0.6.4

- **The Update button always offers the newest version.** It used to trust which release GitHub marked as
  "latest", and when several releases were published at once GitHub could mark an older one (0.6.1 instead
  of 0.6.3). PaperJay now looks at all published releases and picks the highest version. New releases are
  also only marked "latest" on GitHub when they really are the newest.

## 0.6.3

- **The start page says hello.** It now opens with "Hi" and your first name, today's date under it, and on a
  phone a round button for Settings. The greeting steps aside while an answer is on the screen.

## 0.6.2

- **Settings is easier to find your way around.** It opens with a search box and a card for your account,
  then the options in groups: Documents, Adding documents, Reading and asking, People and reminders, and
  Connections and look. Each option has its own coloured icon and shows its current choice on the right
  (for example "English", "60 days ahead", "Off" or the version you have). Type in the search box to find
  an option by name or by what it does.
- Tags & companies, Trash and the camera scanner are in the list on a computer too.

## 0.6.1

- **A new look for the bar at the bottom of the phone screen.** The **+** is now a big round button that sits
  a little above the bar with a soft glow and says **Add** under it. The page you're on is shown in the accent
  colour, and the icons and labels are a bit bigger and easier to tap.

## 0.6.0

- **Connect Claude or another AI assistant.** PaperJay now has a built-in MCP server. In
  **Settings → AI assistants (MCP)**, make an access key and tick what the assistant may do: **Read**
  (search, read and ask), **Create** (add documents, notes and labels), **Update** (change filing, mark
  reviewed, bring back from the Trash) and **Delete** (move to the Trash, remove labels). PaperJay then shows
  the key once with ready-to-paste setup for Claude Code and Claude Desktop. See [docs/mcp.md](docs/mcp.md).
- A key can never do more than the account that made it, and an assistant is only offered what its key
  allows. No key can delete anything for good, empty the Trash, change settings or people, make other keys
  or download an export. Removing a key cuts the assistant off straight away.
- The same permissions work for any access key used with the API: the access key list now shows what each
  limited key may do. Keys made before keep working as they did.
- Changes the database (a new column for key permissions); PaperJay backs it up first, as always.

## 0.5.6

- **Swipe all the way to delete.** In the Library, a short swipe left still just shows the trash-can button.
  Keep swiping past it, most of the way across the document, and the button fills the space and turns a
  stronger red (phones that can buzz give a small buzz): let go there and the document goes to the Trash
  straight away, with **Undo**. Stop short and it simply shows the button.
- The trash-can button now uses the same soft red as the **Failed** tag, in both dark and light themes.

## 0.5.5

- **Swipe left to delete.** On a phone or tablet, swipe a document to the left in the Library to show a red
  trash-can button; tap it to move the document to the Trash. **Undo** brings it straight back, and the
  Trash keeps it for 30 days. Swipe back to the right, or tap anywhere else, to close the button
  without deleting. Swiping is off while you're selecting documents and for read-only accounts.

## 0.5.4

- **One box to find or ask.** As you type in the Ask box, it suggests matching companies, people, topics,
  numbers and documents; tap one to open it, or press Enter to ask your question. The matching documents
  show up straight away while the AI writes its answer, and **See every match in the Library** below them
  opens the full word search. On a phone this replaces the round search button; on a computer the search
  box in the sidebar still works on every other page, and `/` jumps to the Ask box on the start page.
- **The + button is in the middle of the bar on a phone.** The bar is now Ask, Library, +, Review and More.
- **Browse is part of the Library.** Open the Library and switch between **All documents** and **Browse**
  (the filing cabinet, types, tags, topics, years and A–Z) at the top. Old Browse links still work.

## 0.5.3

- **PaperJay tells you when a new version is out.** Every few hours it asks GitHub whether there's a newer
  release. If there is, people who can change settings see a note at the top of every page, with
  **Update** and **Not now** (and "New" next to Settings). "Not now" hides it until the next release. The
  automatic check can be switched off in Settings → Updates, or with `UPDATE_CHECK=false`.
- **"Check for a new version" shows that it's checking.** The button turns into a spinner while PaperJay asks,
  then says whether a new version is available or you already have the newest one (or why it couldn't
  check). Updates also shows when it last checked.
- **The Update button shows each step.** While it updates you see what's happening (backing up your
  documents' database, installing, restarting) and the page reloads by itself on the new version. If the
  update fails, the page says so and stays on the version you had.
- On a Proxmox install from a private repository, PaperJay now reads the GitHub token in
  `/etc/paperjay/github-token` to see new releases; before, the check (and so the Update button) never
  found one.
- A second Update can't be started while one is running.

## 0.5.2

- **Settings is a list now.** The Settings page shows each part (Your account, People, Smart filing,
  Email, Updates and the rest) as one line, like Tags & companies and Trash. Tap one to open it on its
  own page; **← Settings** at the top takes you back. Old links to a spot on the long page (such as
  "Add to calendar" on the start page) open the right part.
- Tags & companies, Trash and the camera scanner setup also have **← Settings** at the top (on a phone,
  where you reach them from Settings).
## 0.5.1

- "Check it works" accepts a server that lists its model under a variant name (for example
  `qwen3.5-9b-q4_k_m` for `qwen3.5-9b`); such servers answer to the base name.
- `paperjay fetch-models` (and the updater) no longer prints a line per downloaded file.

## 0.5.0

Search by meaning is built in: no AI program needed for it any more.

- **A built-in model for search by meaning.** PaperJay now runs `nomic-embed-text` v1.5 itself, on the
  CPU, so "grass cutting" finds the lawn service invoice even with the AI turned off. The model (about
  130 MB) is downloaded once during install or update (`paperjay fetch-models`), and is inside the Docker
  image. It is the default for new and existing installs; every document is re-indexed once in the
  background. Settings → Smart search can still use the AI program's model instead, or turn it off; a
  model name you had emptied before 0.5 (search by meaning off) stays off.
- **Better matches.** The built-in model gets nomic's `search_document:` / `search_query:` prefixes, which
  it was trained with.
- **"Answer without a thinking step"** (Settings → Smart filing): for reasoning models such as Qwen3 on
  llama.cpp, vLLM or Ollama, PaperJay asks for the answer without the thinking step, which makes filing
  and answers several times faster on those models.
- The AI connection and search by meaning are now independent: changing the AI program or model no longer
  touches search by meaning, and the connection check tests both.
## 0.4.4

- **Scanning several pages: one document or separate ones.** Once two or more pages are taken, the
  scan box asks how to save them: **One document** (as before) or **Separate documents**, one per
  page. The choice is remembered. With "Keeping the paper?" on, each separate document gets its own
  paper number.

## 0.4.3

Tidy up: the gaps and doubts in your filing, each fixed with one tap.

- **A "Tidy up" tab on the Review page** lists documents with no company, no date or no type,
  titles that are just the file name, possible copies, filing the text and the AI disagree
  on, documents that couldn't be read (with the reason), values filed from a single clue
  ("Low evidence"), and imported filing PaperJay doesn't learn from until you confirm it.
  The start page counts them.
- **One tap fixes a row.** Use the suggested company, type or title, or pick another; for a
  date, every labelled date the document carries is listed ("Statement Mar 1 · Due Mar 21")
  with the one PaperJay would pick starred. A fix is your own filing, so the next documents
  from that company are filed the same way. "Confirm all" and "All look right" do a whole list.
- **"Not a copy" sticks.** A document wrongly flagged as a copy is never paired with that
  document again, and Ask and totals count it as its own document.
- **Settings → Filing → Re-file everything** files every document again from its text and
  your own filing in seconds, and can also queue an AI re-read (with the time it will take).
- **Fixed: the Settings page was blank in 0.4.2.** A duplicate name in its script stopped the
  page from loading. The tests now load every page script the way a browser does.
- Fixed: "Mark all reviewed" forgot what had been reviewed, so a re-file could raise the same
  question again. It now behaves like each document's "Looks right".

## 0.4.2

Paper numbers: the shelf and the app agree on where the paper is.

- **Number the paper you keep.** With the new "Paper shelf" setting on, uploading and
  scanning offer "Keeping the paper? Paper no. …" with the next free number filled in.
  The number is claimed as the document is added, so two uploads at the same moment can
  never take the same one. Email and the consume folder get no number unless they pass
  one. A document's page shows a big "Paper no. N" badge and the number can be set or
  cleared there; a taken number says so plainly instead of failing silently.
- **Number several at once.** "Number in order" in the Library gives the selected
  documents that lack a number the next free numbers, top to bottom.
- **Find it by number.** `#124` or `paper 124` in the Library search, paper numbers in
  the search suggestions, and Ask answers "Where's the paper copy of my W-2?" with
  "Paper no. 57" — the number is one of a document's answerable facts.
- **Labels you can print.** Avery 5160 and L7160-style sheets, or single 62 mm labels,
  each with the big number, title, date and a code that opens the document. Blank
  labels can be printed ahead of time; printing them reserves those numbers, so the
  next upload steps past them and each reserved number can be claimed exactly once.
- **Imports keep their numbers.** A Paperless archive serial number becomes the paper
  number (no longer a line in a note), and documents imported before this release get
  theirs back from the old note when it is unambiguous. Exports carry the number too,
  so an export imported elsewhere restores it.
- One new dependency: `segno` (pure Python) draws the QR codes; the PDFs use `fpdf2`,
  which was already installed with OCR.

## 0.4.1

Fixes from a review of 0.4.0 — one of them important.

- **A search of all zeros no longer takes the app down.** Typing a search whose digits are
  all zeros (`00`, `0.`) sent the suggestions into a loop that ate memory until PaperJay
  died. It now answers instantly, and a number typed with a leading zero no longer offers
  paper numbers it can't mean.
- **Ask is less easily fooled by ordinary words.** A first name on its own (Will, May, Bill)
  used to count as naming that person, so "Will my car insurance renew in May?" quietly
  favoured one person's papers over everyone else's. A lone first name now has to look
  like a mention — a possessive, or the capitalised word in the middle of a sentence —
  before it counts.
- **Index keys are no longer guessable.** The short keys that link to companies, people and
  numbers in the A–Z index were a plain hash of the value, which could be worked backwards
  offline. They are now keyed with a secret kept on the server.
- Smaller things: startup no longer re-files documents that have nothing to index, saving
  Household settings no longer fails because of a blank row, and Browse counts only
  documents that have finished processing, like everywhere else.
- Numbering note: this fix release takes 0.4.1, so the paper-numbers release planned as
  0.4.1 becomes 0.4.2, and the Tidy up release planned as 0.4.2 becomes 0.4.3.

## 0.4.0

Finding things, four ways.

- **Browse: walk the filing cabinet.** A new Browse page drills down Category → Company → Year,
  or starts from Type, Tag, Topic or Year → Month, with a count at every level and an "Unfiled"
  tray for anything not yet sorted.
- **The A–Z index.** Inside Browse sits the index at the back of the book: Companies (their other
  names appear as "see" entries), Numbers, People and Topics. Numbers show only their last 4 —
  tap Reveal to see one in full (the reveal is written to the history), and typing part of a
  number finds it without unmasking the list.
- **Search that knows your library.** The search box (in the sidebar; the magnifier button on the
  phone, or press /) suggests companies, numbers, people, topics, types, tags, paper numbers,
  years and matching documents as you type, each with its document count. Choosing one opens the
  library already filtered to it; ↑ and ↓ move through the list, and a question-shaped search
  offers to ask PaperJay instead.
- **Ask understands names.** When a question names a company, a person, a topic or a kind of
  document — or a number by its last digits ("the account ending 4821") — those documents count
  for more and the rest for less. Nothing is ever hidden by it.
- **Household people.** Settings → Household lists the people your documents are about, the other
  names they go by ("Dad", a name older papers use), and optionally which PaperJay account is
  theirs — after which "my insurance papers" asked from that account means their papers.
- **More library filters, and a place on the shelf.** Category and "on the paper shelf" join the
  filter panel. A document can carry a paper number — where its physical copy sits — unique
  whenever one is set.
- Under the hood: a new index table (migration 0008) is kept current at every refile, and the
  suggestions were re-measured and sped up on a 20,000-document library.

## 0.3.2

Fixes from the first weeks of 0.3 on a real library.

- **Companies are only filed by names that really are the sender.** A tax agency addressed in a
  lawyer's fax, or the Secretary of State in an insurance card's fine print, no longer becomes the
  document's company. Agencies, card networks and carriers count only in the letterhead, and the
  company a document is already filed under is kept when its name is in the text. A well-known
  company PaperJay doesn't have yet is now only suggested, never created on its own; with the AI on,
  it is filed once the AI agrees. A text pattern alone never replaces a company or type a document
  already has: it is suggested for review instead.
- **Fewer review flags, and "reviewed" sticks.** Only a disagreement or a missing answer about the
  company, the kind of document or its date asks for review; a shaky category or total is kept as a
  suggestion. Once you mark a document reviewed, it is flagged again only for a reason you haven't
  seen (reviews done in 0.3.0/0.3.1 are read back from the history).
- **AI re-reads happen once, at night, in your time zone.** A document is re-read at most once per
  reading version, only when its company, kind or date is missing or disputed, and a read that failed
  waits a week. The installer gives the container the host's time zone (`TZ`), so quiet hours and
  mailbox checks follow your clock; Docker users set `TZ` in docker-compose.yml.
- **The AI-filing switch works again, and the history shows what the AI set.** With AI filing off,
  values only the AI found are not filed; changes that include the AI's value are recorded as the AI's.
- **Titles.** A title that is only a file name ("Receipt48213377", "ABCInsuranceIDCardAuto") never wins
  over a real one, and generated titles name the document's company, not a "Bill to" line.
- Series keys keep the kind of document; an invoice number no longer lumps a company's invoices and
  receipts into one series. The AI's reply has more room (it was sometimes cut off mid-answer).
- Names learned as aliases never include agencies or well-known companies.

## 0.3.1

- **Your library now helps file new documents.** When several documents you filed yourself agree on
  a company — because they share an account or policy number, came from the same sender, or simply
  read alike — a new document that matches them is filed the same way, with the reason written in
  plain words ("filed like 3 earlier documents from the same account"). Only filing you confirmed
  yourself teaches the library: companies an import or the AI suggested never vote until you've
  confirmed one by hand. Changing a document's company by hand teaches the alternative name as an
  alias, so next time it's recognised on sight. (Settings: whether an import's filing may ever be
  trusted; labels pages list and remove aliases.)
- **Copies are spotted and set aside.** A near-duplicate of an earlier document is flagged for
  review as "looks like a copy", is left out of totals, and is never the document Ask reads from.
  Real series — monthly bills, yearly insurance cards — are not copies and are never flagged: a
  document whose dates or total contradict the earlier one is a new member of the series, not a
  copy. Ask also treats a series as one thing for "current/latest" questions: one source card for
  the newest member, with "+N earlier" noting the rest.
- **The AI reads with context.** Its prompt now includes what the text patterns read and how
  similar documents were filed, it files topics from your vocabulary (at most three) and people
  exactly as labelled in the text ("Patient: ..."), and its older cards stay valid — only
  documents whose profile still has a real gap (a disagreement, a low-evidence field, no company,
  no topics) are re-read, in bulk, inside the quiet hours (`ai_batch_hours`, default 01:00–06:00).
  An admin can re-read everything from the API after seeing the time estimate (~40 s a document).

## 0.3.0

- **Your documents get filed and answered even with the AI off.** A new rules layer reads the text
  itself — company names, "amount due", labelled dates, account and policy numbers — and files from
  it in well under a second per document, no model involved. Ask answers fact and total questions
  from these patterns exactly as it does from the AI.
- **Every document now has one profile** that merges what the rules, your own filing and the AI each
  say, keeping the source and confidence of every field. Your filing always wins; when the rules and
  the AI disagree, the disagreement is flagged for review instead of silently picked; a value only
  one source supports below its bar becomes a suggestion, not a filing.
- **Documents are findable by what they're about.** A statement from a dental office is filed under a
  "dental" topic, so "what did the dentist charge?" finds it even though its text never says the word
  dentist. Around sixty built-in topics (bills, prescriptions, warranties, travel…) plus your own,
  in Settings.
- **File-name titles and scan dates are given back.** Titles an import carried over that are really
  file names ("IMG_4021", "Scan0042") no longer block a proper title, and a document date that is
  just the day the page was scanned is released when the text carries a labelled date of its own.
  Each release is written in the document's history and can be undone by renaming it yourself.
- New settings: rules filing on/off, whether a glimpsed company name may create a company, and
  custom topics. Filing/eval tooling: `scripts/filing_eval.py --layers` and `scripts/ask_eval.py`
  score a library copy against a truth file and a questions file.

## 0.2.5

- **"How much have I spent on medical bills so far?"** now gets an answer. Filler words like "so far", "yet"
  and "overall" no longer count as words a document must contain, and "bills", "invoices", "statements" and
  "receipts" aren't mistaken for a company's name in totals.
- **Totals only count what the document itself supports.** A document filed under a category by the AI is
  left out of that category's total when its text doesn't back it up (a car registration filed as medical),
  and price lists, fee schedules and estimates are set aside as "not a payment", with the reason shown.
- Short abbreviations ("MD", "DDS", "lab") only count as evidence of a category as whole words, and not as a
  state in an address ("Springfield, MD 21000").

## 0.2.4

- **New logo and app icon.** A P and a J hooked into each other, slanted, with a blue jay's crest over the P
  (its round top is the jay's head). New app icons for iPhone, Android (round masks included) and the browser
  tab (a bolder version for tiny sizes), and a "PaperJay" wordmark in the sidebar and on the sign-in and setup
  pages that follows the light and dark themes.
- Brand files in `docs/brand/` (the mark for dark and light backgrounds, one-colour versions, lockups) with
  a short usage note; `scripts/make_icons.py` renders the PNG icons from the SVG artwork.

## 0.2.3

- **Adding a mailbox takes three steps**: type your email address (the provider and server are worked out
  for you), make an app password (the steps for your provider and a button to the right page), and tick the
  senders PaperJay found sending you PDFs and photos in the last four months. It then collects their
  documents straight away. Outlook/Microsoft 365 and Proton say up front what works instead.
- Clearer message when a mail server refuses the password.

## 0.2.2

- **Settings in plain words.** Every section starts with a sentence on what it's for, and every option says
  what it does without technical terms ("Can add and edit" instead of "member", "access keys" instead of
  "API tokens", language names instead of codes, "Smart search model" instead of "embedding model").
  Technical options (processor cores, access keys, nightly export command) fold away under **Advanced**.
  The AI check explains what to do when something's missing. On phones the People list and password form
  no longer squeeze.

## 0.2.1

- **Library on phones**: the header (title, count, grid/list, Select) stays on one line on small phones;
  filters fold away behind a **Filters** button (showing how many are in use) and open as an even
  two-column grid with labelled From/To dates; cards show two-line titles with the company and date, and
  their tags line up across a row; the list view's Review chip no longer squeezes the text; the select bar
  is an even grid.
- Camera scanner: the shutter button stays centred before the first photo.

## 0.2.0

- **Live camera scanner.** The page's outline follows the page while you aim, and the photo is taken by
  itself when you hold still (or with the button: **Auto** / **Manual**), page after page without leaving
  the camera. Tap a page afterwards to adjust its corners or look. Pages are sent while you take the next.
- **PaperJay's own HTTPS** (`HTTPS_PORT`, default 8443; 443 in the Proxmox container), because phones only
  allow the live camera over https. It makes a certificate authority of its own that can only vouch for
  home-network addresses; **Settings → Secure address** walks each phone through trusting it once
  (a profile for iPhone, a certificate for Android and computers). The certificate renews itself and
  covers the addresses PaperJay is opened at. Without it, the camera button works as before.
- **Documents from email.** Settings → Email: add mailboxes (IMAP with an app password), filter by sender
  and subject, and PaperJay checks them every night (02:30 by default) and adds the attached PDFs and photos;
  afterwards it leaves the email, marks it read or moves it. Each email is taken once. Guide: docs/email.md.
- Back gestures that stay on the same page (closing the camera) no longer reload it.

## 0.1.2

- **Document scanner** for the camera: the page is found in the photo automatically (corners can be
  dragged), cut out and straightened, shadows and uneven light are evened out, and it's saved in black &
  white (the fax/scanner look, tiny files), grayscale or color. Several pages make one PDF.

- Installer: the container gets DNS servers it can actually reach. A host using Tailscale's MagicDNS
  or a local resolver (127.x) left new containers unable to resolve names, so the
  install waited forever; such addresses are now skipped (falling back to 1.1.1.1 and 9.9.9.9),
  `--nameserver` sets them explicitly, and the network wait gives up with a clear message after a minute.

## 0.1.1

Beta 2: first version installed for real.

- Guides: installing, moving from Paperless-ngx, phones, Home Assistant, backups, export format, API;
  a new README with screenshots and a comparison; SECURITY.md and issue templates.
- `/api/upcoming` links are full addresses (for Home Assistant notifications).
- Instant answers no longer repeat a word ("Example Mutual Insurance insurance"); sources only show a
  category the text backs up; the installer silences locale warnings.

## 0.1.0

First private beta.

### Added
- Storage core: SQLite schema v1 (documents, tags, correspondents, document types, notes, users,
  audit log, job queue, embeddings, settings) with numbered migrations and automatic backups.
- File store named by sha256, with atomic, fsynced writes.
- Library: add, edit (tracking AI-set fields), notes, trash/restore/purge, label rename/merge/delete,
  full-text search with highlighting and filters.
- Background job queue with retries, backoff and crash recovery.
- Embedding storage and search by meaning.
- `paperjay serve | migrate | check`.
- Intake: upload API, Paperless-ngx compatible `post_document` + `tasks` endpoints, consume folder;
  file type checked from content; size and page limits; every attempt recorded (schema v2: uploads).
- OCR pipeline: OCRmyPDF + Tesseract (rotate, deskew, clean, PDF/A with plain-PDF fallback), photos and
  HEIC/WebP/TIFF converted to PDF, text with page breaks, thumbnails, page count, date and title found
  in the text. Tools run in their own process group with a time limit and lower priority.
- AI, optional: Ollama or any OpenAI-compatible server, settings in the database (`/api/settings`) with a
  connection test; one request at a time, and questions go before background work.
- AI filing and fact cards in one call per document, grounded in the text: never overrules a person,
  only existing tags (and only when their word is in the document), never sets "paid", new companies and
  tag suggestions go to Needs review (schema v3: facts, suggestions, feedback, document revisions).
- Answers ported from Paperless Ask: ranking with synonyms and typo matching, cited and checked answers,
  instant answers, totals, follow-ups, search by meaning, "ask about this document", feedback,
  coming-up dates and calendar feed. `POST /api/ask` keeps Paperless Ask's request and reply shape.
- Web app (plain ES modules, no build step): Ask start page, library with filters, saved searches and bulk
  actions, document page with server-rendered page images, editing, notes and history, upload with
  multi-page camera scans, Needs review, Trash, label management, settings with an AI connection test,
  dark and light themes, installable PWA with an Android share target (schema v4: saved searches).
- `scripts/demo.py` fills a data folder with made-up documents (sign in as demo / demo-password).
- Sign-in and household accounts (admin, member, read-only), sessions and API tokens (`Token` header
  like Paperless-ngx, calendar-only tokens), first-run setup wizard (schema v5).
- Import from Paperless-ngx (API, ids/labels/notes/text/searchable PDFs kept, safe to re-run) and from a
  PaperJay export; plain-folder export (incremental, `paperjay export DIR`) and a zip download.
- Docker image (amd64 + arm64, common OCR languages, runs as PUID/PGID) and docker-compose.yml;
  Proxmox installer `install.sh` (Debian 13 LXC, re-run to upgrade); Update button for the Proxmox install
  with a database backup first and automatic rollback; release workflow (GHCR image, vulnerability scan,
  GitHub release); `paperjay backup`.
- `paperjay add-admin`, `scripts/ocr_compare.py` (compares PaperJay's OCR with imported text; numbers only).
