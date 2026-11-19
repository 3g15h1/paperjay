# API

Everything the app does goes through a JSON API under `/api`. Send an API token
(**Settings → Your account → Advanced: access keys**) as `Authorization: Token pj_…` (or `Bearer pj_…`). A token acts
as the account that made it; read-only accounts can only read and ask. A key made under **Settings → AI
assistants (MCP)** (or with `POST /api/auth/tokens {"name", "permissions": ["read", "create", "update", "delete"]}`)
is limited to those: it gets 403 for anything else, and never reaches settings, people, other keys, exports or
deleting for good. The same keys work for the [MCP server](mcp.md) at `/mcp`.

## Adding documents

```sh
curl -H "Authorization: Token $T" -F file=@bill.pdf http://paperjay:8000/api/documents
# several photos as one document:
curl -H "Authorization: Token $T" -F file=@p1.jpg -F file=@p2.jpg -F combine=true http://paperjay:8000/api/documents
```

Optional form fields: `title`, `doc_date` (YYYY-MM-DD), `tags` (ids, repeat the field), `correspondent_id`,
`document_type_id`. The reply lists each file's outcome: `accepted` (201), `duplicate` (409) or `rejected`
(415, with the reason). With the `paper_numbers` setting on, uploads also accept `paper_no`
(number this document) or `paper_next=true` (claim the next free number); `GET /api/paper/next`
says which number is next. Paperless-ngx compatible: `POST /api/documents/post_document/` (field `document`;
its `archive_serial_number` becomes the document's paper number)
returns a task id; `GET /api/tasks/?task_id=…` reports `PENDING`, `STARTED`, `SUCCESS` or `FAILURE`.

## Asking

```sh
curl -H "Authorization: Token $T" -H 'Content-Type: application/json' \
     -d '{"q": "When does my car insurance expire?"}' http://paperjay:8000/api/ask
```

Reply: `answer` (with `[n]` citations), `mode` (`facts`, `total`, `llm`, `list`, `extractive`, `none`),
`sources` (`n`, `id`, `title`, `url`, `snippet`, …), `unverified` (numbers the answer has that the sources
don't). Options: `"no_llm": true`, `"full": true` (skip instant answers), `"prev": {"q", "a"}` for a
follow-up, `"doc": <id>` to ask about one document. `GET /api/ask/stream?q=…` streams the same as
Server-Sent Events.

## Other endpoints

| | |
|---|---|
| `GET /api/documents?q=&tags=&correspondent_id=&document_type_id=&date_from=&date_to=&sort=` | search and list |
| `GET /api/documents/{id}`, `PATCH /api/documents/{id}` | one document; change title, date, labels |
| `GET /api/documents/{id}/original` · `/archive` · `/thumb` · `/pages/{n}` | its files |
| `GET /api/fields` · `POST` · `PATCH`/`DELETE /api/fields/{id}` | the household's own fields `{name, kind: text\|number\|money\|date, fill: ""\|total\|id:<words>\|date:<label>}` (changing them: admins) |
| `PUT /api/documents/{id}/fields/{field}` | `{value}`: a document's value (`""` none, `null` fill itself); `GET /api/documents/{id}` lists them in `fields` |
| `GET /api/documents/{id}/find?q=` | where the words are on its pages: boxes as fractions of each page |
| `POST /api/documents/{id}/notes` · `/trash` · `/restore` · `/reprocess` · `/analyze` | actions |
| `POST /api/documents/{id}/edit-pages` | `{pages: [{n, rotate, keep}], split_after: [n]}`: turn, take out, reorder, split; returns the new `documents` (the old one goes to the trash) |
| `POST /api/documents/merge` | `{ids}` in page order: join into one new document |
| `POST /api/documents/bulk` | `{ids, action, value}`: add_tags, remove_tags, set_correspondent, set_type, trash, … |
| `GET/POST /api/labels/{tag,correspondent,document_type}` | labels; `PATCH`, `DELETE`, `POST …/merge` |
| `GET /api/upcoming` · `GET /calendar.ics` | coming-up dates and warranty ends ([home-assistant.md](home-assistant.md)) |
| `GET /api/warranties` | every document with a warranty; `PATCH` a document's `warranty_until` (a date, `9999-12-31` lifetime, `none`, or `null` = read from the text) |
| `GET /api/documents.csv?<the same filters>` or `?ids=1,2,3` | the list as a spreadsheet (CSV) |
| `GET /api/taxpack?year=` · `GET /api/taxpack.zip?year=&categories=&topics=&tags=` | the tax pack ([tax-pack.md](tax-pack.md)) |
| `GET /api/shred?all=` · `POST /api/documents/{id}/shredded` · `…/unshred` · `POST /api/shred/bulk {ids}` | paper to shred ([keeping-paper.md](keeping-paper.md)); `PATCH` a document's `keep_until` (`9999-12-31` = for good, `null` = the rules) |
| `GET /api/summary` · `GET /api/ai/status` · `GET /healthz` | counts and health |
