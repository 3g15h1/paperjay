# Export format

`paperjay export <folder>` (or **Settings → Moving in and backing up → Download everything**) writes:

```text
README.txt
manifest.json
documents/2025/2025-08-14 Acme Energy bill [12].jpg              the original, byte for byte
documents/2025/2025-08-14 Acme Energy bill [12].searchable.pdf   the OCR'd PDF, if there is one
```

Files are named `<date> <title> [<id>]`, in a folder per year (the date is the document's own date, or
the day it was added). Running the export again into the same folder copies only new or changed files,
moves renamed ones, and with `--prune` deletes files of documents that no longer exist.

## manifest.json (format `paperjay-export`, version 1)

```json
{
  "format": "paperjay-export", "version": 1, "exported_at": "2026-10-18T09:00:00Z", "paperjay": "0.1.0",
  "tags": [{"name": "Bills", "color": "#f2c14e"}],
  "correspondents": ["Acme Energy"],
  "document_types": ["Bill"],
  "documents": [{
    "id": 12, "title": "Acme Energy bill", "date": "2025-08-14", "added": "2025-08-15T13:00:00Z",
    "correspondent": "Acme Energy", "document_type": "Bill", "tags": ["Bills", "Utilities"],
    "notes": [{"text": "paid online", "created": "2025-08-20T10:00:00Z", "by": "Alex"}],
    "original": "documents/2025/2025-08-14 Acme Energy bill [12].jpg", "original_name": "IMG_4012.jpg",
    "mime_type": "image/jpeg", "sha256": "…", "searchable_pdf": "documents/2025/…[12].searchable.pdf",
    "pages": 1, "text": "…the OCR text…",
    "facts": {"kind": "bill", "category": "utilities", "vendor": "Acme Energy", "total": 144.37,
              "dates": [{"label": "due", "date": "2025-09-05"}], "ids": [{"label": "account", "value": "7730 1184 52"}]}
  }]
}
```

`sha256` is the original file's checksum; `facts` is what the AI read (or `null`). Documents in the trash
are not exported. `paperjay import-export <folder>` reads this format back.
