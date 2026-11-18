# Moving from Paperless-ngx

PaperJay copies everything from Paperless-ngx over its API. Nothing changes in Paperless, so you can run
both side by side for as long as you like, and run the import again to pick up new documents: anything
already in PaperJay is skipped.

## What comes across

| Paperless-ngx | PaperJay |
|---|---|
| original file | the same file, byte for byte |
| archive (searchable) PDF | kept as the searchable PDF, no OCR needed |
| content (OCR text) | kept as the text |
| id | kept, if that id is free |
| title, created date, added date | kept |
| correspondent, document type, tags (with colours) | kept (nested tags become plain tags) |
| notes | kept |
| custom fields, archive serial number | written into a note on the document |
| owner, permissions, storage paths, workflows, mail rules, saved views | not copied |

Fields that had a value count as set by a person, so PaperJay's AI never changes them; it only fills in
what's empty and reads each document's facts (totals, due dates, numbers) in the background.

## Steps

1. In Paperless, open your profile and copy your **API Auth Token** (or create one).
2. In PaperJay, go to **Settings → Moving in and backing up**, enter the Paperless address
   (e.g. `http://paperless.local:8000`) and the token, and press **Check**. It shows how many documents,
   tags and companies it found, and how many are already here.
3. Press **Import**. Progress shows on the page. A few thousand documents take a while (each file is
   downloaded); you can leave the page and come back.

On the command line instead: `paperjay import-paperless http://paperless.local:8000 --token-file token.txt`
(add `--dry-run` to only see the numbers).

## Then

- **Phone shortcuts and scripts** that post to Paperless's `/api/documents/post_document/` keep working:
  change only the address and the token (make one in **Settings → Your account → Advanced: access keys**). PaperJay
  answers `post_document` and `/api/tasks/?task_id=` the same way.
- Point any scanner or sync folder at PaperJay's consume folder.
- Run both for a week or two, then retire Paperless when you're happy.
