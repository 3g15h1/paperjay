"""How well does the AI file documents? Compares its filing with how an imported library is filed now.

    DATA_DIR=/path/to/paperjay-data python scripts/filing_eval.py [--limit N] > report.json

Uses the AI settings saved in that PaperJay. For each imported document it makes the same AI call as the
'analyze' job (nothing is saved or changed), grounds the reply the same way, and compares:

    company   the AI's company matches the document's correspondent (same rule as filing uses)
    type      the AI's kind maps to the document's type name
    tags      precision / recall of the AI's tags against the document's tags
    dropped   how much the grounding threw away (made-up values caught)
The report holds names of companies, types and tags (the household's own labels) but no document text.
"""
import argparse
import json
import statistics
import sys
import time

from paperjay import ai, analyze, db, settings
from paperjay.config import Config
from paperjay.library import Library
from paperjay.textutil import normalize


def _same_name(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    from paperjay.analyze import _norm_name
    x, y = _norm_name(a), _norm_name(b)
    return bool(x and y and (x == y or x in y or y in x))


def offline(a) -> None:
    """Score filing against a truth file, without the AI service: copy the database, migrate the
    copy, release import filing, run the profile pipeline over it, and compare the layer asked for.
    The original data directory is never written to."""
    import shutil
    import tempfile
    from pathlib import Path

    from paperjay import profile
    src = Path(a.data_dir) / "paperjay.db"
    tmp = Path(tempfile.mkdtemp(prefix="pj-eval-"))
    shutil.copy(src, tmp / "paperjay.db")
    db.migrate(tmp / "paperjay.db", tmp / "backups")
    con = db.connect(tmp / "paperjay.db")
    lib = Library(con)
    lib.release_import_filing()
    from paperjay import ask as ask_mod
    ask_mod.INDEX.refresh(con)  # the library layer's similarity signal reads this index
    ids = [r[0] for r in con.execute("SELECT id FROM documents WHERE deleted_at IS NULL "
                                     "AND status = 'ready' AND length(content) > 0 ORDER BY id")]
    t0 = time.monotonic()
    for doc_id in ids:
        profile.refile(con, doc_id)
    secs = time.monotonic() - t0
    truth = {d["id"]: d for d in json.loads(Path(a.truth).read_text())["documents"]}
    fields = ("company", "type", "doc_date", "total", "ids")
    score = {f: [0, 0] for f in fields}
    review = 0
    rows = []
    for doc_id in ids:
        t = truth.get(doc_id)
        if t is None:
            continue
        doc = lib.get(doc_id)
        cards = profile.get_cards(con, doc_id)
        if a.layers == "rules":
            card = cards.get("rules") or {}
            got = {"company": card.get("vendor") or None, "type": analyze.TYPE_NAMES.get(card.get("kind") or ""),
                   "doc_date": card.get("doc_date"), "total": card.get("total"), "ids": card.get("ids") or []}
            prof = card
        else:
            use = dict(cards)
            if a.layers == "rules+library":
                use.pop("ai", None)
            prof = profile.merge(use, correspondent_id=doc["correspondent_id"])
            got = {"company": doc["correspondent"], "type": doc["document_type"],
                   "doc_date": doc["doc_date"], "total": prof.get("total"), "ids": prof.get("ids") or []}
        if prof.get("review") or doc["needs_review"]:
            review += 1
        row = {"id": doc_id}
        for f in ("company", "type", "doc_date"):
            if t.get(f):
                ok = _same_name(got[f], t[f]) if f != "doc_date" else got[f] == t[f]
                score[f][0] += bool(ok)
                score[f][1] += 1
                row[f] = {"got": got[f], "want": t[f], "ok": ok}
        if t.get("total") is not None:
            ok = got["total"] is not None and abs(float(got["total"]) - float(t["total"])) < 0.005
            score["total"][0] += bool(ok)
            score["total"][1] += 1
            row["total"] = {"got": got["total"], "want": t["total"], "ok": ok}
        if t.get("ids"):
            have = {str(i["value"])[-4:] for i in got["ids"]}
            ok = all(i["last4"] in have for i in t["ids"])
            score["ids"][0] += bool(ok)
            score["ids"][1] += 1
            row["ids"] = {"got": sorted(have), "want": [i["last4"] for i in t["ids"]], "ok": ok}
        rows.append(row)
    summary = {"layer": a.layers, "documents": len(rows), "seconds": round(secs, 1),
               "review_rate": round(review / len(rows), 2) if rows else None,
               "fields": {f: {"ok": ok, "of": of, "pct": round(100 * ok / of, 1) if of else None}
                          for f, (ok, of) in score.items()}}
    print(json.dumps({"summary": summary, "documents": rows}, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--truth", help="offline mode: a filing truth JSON file to score against")
    p.add_argument("--data-dir", help="offline mode: the PaperJay data directory (copied, never written)")
    p.add_argument("--layers", choices=("rules", "rules+library", "all"), default="all",
                   help="offline mode: which filing layer to score")
    a = p.parse_args()
    if a.truth:
        if not a.data_dir:
            sys.exit("--truth needs --data-dir")
        offline(a)
        return
    con = db.connect(Config.from_env().db_path)
    cfg = ai.Config.load(con)
    if not cfg.on:
        sys.exit("turn the AI on in this PaperJay first (Settings → AI)")
    lib = Library(con)
    done = {t.lower() for t in settings.get(con, "done_tags")}
    tag_rows = [t for t in lib.labels("tag") if t["name"].lower() not in done]
    tag_names = [t["name"] for t in tag_rows]
    ids = [r[0] for r in con.execute("SELECT id FROM documents WHERE deleted_at IS NULL AND length(content) > 0 ORDER BY id")]
    if a.limit:
        ids = ids[:a.limit]
    rows = []
    for doc_id in ids:
        d = lib.get(doc_id)
        t0 = time.monotonic()
        try:
            raw = ai.chat(cfg, analyze.messages(d["title"], d["content"], tag_names), schema=analyze.schema(tag_names),
                          max_tokens=600)
        except Exception as e:
            rows.append({"id": doc_id, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        secs = time.monotonic() - t0
        card = analyze.ground(raw, d["content"], d["title"], tag_names)
        norm = normalize(f"{d['title']}\n{d['content']}")
        ai_tags = {t for t in card["tags"]} | {t["name"] for t in tag_rows if t["id"] in analyze._category_tags(tag_rows, card["category"], d["content"])}
        have_tags = {t["name"] for t in d["tags"] if t["name"].lower() not in done}
        cid = analyze.match_correspondent(con, card["vendor"]) if card["vendor"] else None
        type_ok = card["kind"] in analyze.KIND_EVIDENCE and analyze.kind_supported(card["kind"], norm)
        rows.append({
            "id": doc_id, "seconds": round(secs, 1),
            "company_now": d["correspondent"], "company_ai": card["vendor"] or None,
            "company_match": (cid == d["correspondent_id"]) if d["correspondent_id"] else None,
            "type_now": d["document_type"], "type_ai": analyze.TYPE_NAMES.get(card["kind"]) if type_ok else None,
            "tags_now": sorted(have_tags), "tags_ai": sorted(ai_tags),
            "tag_precision": round(len(ai_tags & have_tags) / len(ai_tags), 2) if ai_tags else None,
            "tag_recall": round(len(ai_tags & have_tags) / len(have_tags), 2) if have_tags else None,
            "total_found": card["total"] is not None, "dates_found": len(card["dates"]), "ids_found": len(card["ids"]),
            "dropped": len(card["dropped"]), "title_ai": card["title"] or None,
        })
        print(f"{doc_id}: {secs:.0f}s company {rows[-1]['company_match']}, tags {rows[-1]['tags_ai']} vs {rows[-1]['tags_now']}",
              file=sys.stderr, flush=True)
    ok = [r for r in rows if "error" not in r]

    def rate(key):
        vals = [r[key] for r in ok if r[key] is not None]
        return {"yes": sum(1 for v in vals if v), "of": len(vals)}

    def med(key):
        vals = [r[key] for r in ok if r[key] is not None]
        return round(statistics.median(vals), 2) if vals else None
    summary = {"documents": len(rows), "errors": len(rows) - len(ok), "company_match": rate("company_match"),
               "median_tag_precision": med("tag_precision"), "median_tag_recall": med("tag_recall"),
               "documents_given_no_tags": sum(1 for r in ok if not r["tags_ai"]),
               "median_seconds": med("seconds"), "totals_found": rate("total_found")}
    print(json.dumps({"summary": summary, "documents": rows}, indent=1))


if __name__ == "__main__":
    main()
