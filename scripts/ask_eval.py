"""How well does Ask find and answer? Scores a questions file against a PaperJay data directory.

    python scripts/ask_eval.py --data-dir /path/to/data --questions questions.jsonl [--ai off]

The data directory is copied first and never written to; the copy is migrated and re-profiled
(the pipeline a real upgrade runs), then every question is asked with the retrieval pipeline —
no model call unless --ai on and the copy's AI settings point at a live server.
Questions file: one JSON object per line:
    {"q": "...", "expect_docs": [ids], "expect_value": "..." or null, "type": "lookup"|"not_found"}
Report: hit@1 / hit@3 over questions with expected documents, value accuracy where an expected
value is given, correct refusals for not_found questions, and p50/p95 latency. No document text.
"""
import argparse
import json
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from paperjay import ask, db, profile, settings
from paperjay.library import Library


def _norm_value(v) -> str:
    return re.sub(r"[^a-z0-9.]", "", str(v).lower())


def _value_in(expected: str, text: str) -> bool:
    want, have = _norm_value(expected), _norm_value(text)
    if want and want in have:
        return True
    digits = re.sub(r"\D", "", str(expected))
    if len(digits) >= 4:  # an ID or a long number may be quoted by its last digits
        return digits[-4:] in re.sub(r"\D", "", text)
    return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--questions", required=True)
    p.add_argument("--ai", choices=("off", "on"), default="off")
    p.add_argument("--no-refile", action="store_true", help="score the library exactly as stored")
    a = p.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="pj-ask-eval-"))
    shutil.copy(Path(a.data_dir) / "paperjay.db", tmp / "paperjay.db")
    db.migrate(tmp / "paperjay.db", tmp / "backups")
    con = db.connect(tmp / "paperjay.db")
    if a.ai == "off":
        settings.save(con, {"ai_provider": "off"})
    lib = Library(con)
    if not a.no_refile:
        lib.release_import_filing()
        ask.INDEX.refresh(con)  # the library layer's similarity signal reads this index
        for (doc_id,) in con.execute("SELECT id FROM documents WHERE deleted_at IS NULL "
                                     "AND status = 'ready' AND length(content) > 0 ORDER BY id"):
            profile.refile(con, doc_id)
    index = ask.Index()
    index.refresh(con)
    asker = ask.Asker(con, index=index)
    questions = [json.loads(line) for line in Path(a.questions).read_text().splitlines() if line.strip()]
    rows, lat = [], []
    hits = {1: 0, 3: 0}
    scored = valued = values_ok = refused_ok = refused_n = 0
    for q in questions:
        t0 = time.monotonic()
        r, mode, _ = asker.prepare(q["q"], None, None, None, False)
        ms = (time.monotonic() - t0) * 1000
        lat.append(ms)
        top = [s["id"] for s in r["sources"]]
        text = (r.get("extractive") or {}).get("text") or ""
        if not text and r.get("total_answer"):
            text = str(r["total_answer"])
        row = {"q": q["q"], "top": top[:3], "mode": mode, "refused": bool(r["refused"]), "ms": round(ms)}
        expect = q.get("expect_docs") or []
        if q.get("type") == "not_found" or not expect:
            refused_n += 1
            ok = bool(r["refused"]) or "couldn't find" in text or "could not find" in text
            refused_ok += bool(ok)
            row["refusal_ok"] = ok
        else:
            scored += 1
            hits[1] += bool(top[:1] == expect[:1])
            hits[3] += bool(set(expect) & set(top[:3]))
            row["hit1"] = top[:1] == expect[:1]
            row["hit3"] = bool(set(expect) & set(top[:3]))
            if q.get("expect_value"):
                valued += 1
                ok = _value_in(q["expect_value"], text + " " + " ".join(s["snippet"] for s in r["sources"]))
                values_ok += bool(ok)
                row["value_ok"] = ok
        rows.append(row)
    lat.sort()
    summary = {"ai": a.ai, "questions": len(questions),
               "hit@1": round(hits[1] / scored, 3) if scored else None,
               "hit@3": round(hits[3] / scored, 3) if scored else None,
               "value_accuracy": round(values_ok / valued, 3) if valued else None,
               "correct_refusals": f"{refused_ok}/{refused_n}",
               "p50_ms": round(lat[len(lat) // 2]) if lat else None,
               "p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))]) if lat else None}
    print(json.dumps({"summary": summary, "questions": rows}, indent=1))


if __name__ == "__main__":
    main()
