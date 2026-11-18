"""Compare PaperJay's OCR with the text another system (e.g. Paperless-ngx) found, on an imported library.

    DATA_DIR=/path/to/paperjay-data python scripts/ocr_compare.py [--limit N] > report.json

For every imported document that has its earlier text, the original file is OCR'd again with PaperJay's
pipeline (in a scratch folder; the library is not changed) and the two texts are compared word by word.
The report holds numbers only (no document text), so it can be shared:

    recall     share of the earlier text's words that PaperJay also found   (did we miss anything?)
    precision  share of PaperJay's words that the earlier text also has     (did we add noise?)
    seconds    PaperJay's OCR time for the document
"""
import argparse
import json
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from paperjay import db, filetypes, ocr, settings
from paperjay.config import Config
from paperjay.store import FileStore


def words(text: str) -> Counter:
    return Counter(w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 or w.isdigit())


def overlap(a: Counter, b: Counter) -> int:
    return sum((a & b).values())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args()
    config = Config.from_env()
    con = db.connect(config.db_path)
    store = FileStore(config.media_dir)
    lang = settings.get(con, "ocr_language")
    rows = con.execute("SELECT d.id, d.checksum, d.original_ext, d.mime_type, d.content, d.page_count FROM documents d "
                       "WHERE d.deleted_at IS NULL AND length(d.content) > 0 AND EXISTS (SELECT 1 FROM audit_log a "
                       "WHERE a.document_id = d.id AND a.action = 'import') ORDER BY d.id").fetchall()
    if a.limit:
        rows = rows[:a.limit]
    results = []
    for r in rows:
        ftype = filetypes.BY_MIME.get(r["mime_type"])
        entry = {"id": r["id"], "type": r["mime_type"], "pages": r["page_count"]}
        if ftype is None:
            results.append({**entry, "skipped": "file type PaperJay doesn't OCR"})
            continue
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.monotonic()
            try:
                res = ocr.process(store.path("originals", r["checksum"], r["original_ext"]), ftype, Path(tmp),
                                  ocr.Options(language=lang, jobs=config.ocr_jobs, timeout=config.ocr_timeout))
            except Exception as e:
                results.append({**entry, "error": f"{type(e).__name__}: {e}"[:200]})
                continue
            secs = time.monotonic() - t0
        before, now = words(r["content"]), words(res.text)
        hit = overlap(before, now)
        results.append({**entry, "seconds": round(secs, 1), "words_before": sum(before.values()),
                        "words_now": sum(now.values()), "recall": round(hit / max(sum(before.values()), 1), 3),
                        "precision": round(hit / max(sum(now.values()), 1), 3), "warnings": len(res.warnings)})
        print(f"{r['id']}: recall {results[-1]['recall']:.2f}, precision {results[-1]['precision']:.2f}, "
              f"{secs:.1f}s", file=sys.stderr, flush=True)
    done = [x for x in results if "recall" in x]
    summary = {"documents": len(results), "compared": len(done),
               "errors": sum(1 for x in results if "error" in x), "skipped": sum(1 for x in results if "skipped" in x)}
    if done:
        summary.update({k: round(statistics.median(x[k] for x in done), 3) for k in ("recall", "precision", "seconds")})
        summary["worst_recall"] = sorted(({"id": x["id"], "recall": x["recall"]} for x in done), key=lambda x: x["recall"])[:5]
    print(json.dumps({"summary": summary, "documents": results}, indent=1))


if __name__ == "__main__":
    main()
