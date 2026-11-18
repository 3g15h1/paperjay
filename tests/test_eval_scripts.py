"""The offline eval scripts (spec section 9): they score a data directory on a copy, never writing
to it, and print JSON a release can be gated on."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

BILL = ("Acme Energy\nStatement date: March 1, 2026\nAccount number: 1234 5678\n"
        "Electric service. Usage: 812 kWh. Meter reading 45210.\n"
        "Amount due: $142.37\nDue date: March 21, 2026\n")


def _data_dir(tmp_path):
    from paperjay import db
    from paperjay.library import Library
    data = tmp_path / "data"
    data.mkdir()
    db.migrate(data / "paperjay.db", tmp_path / "backups")
    con = db.connect(data / "paperjay.db")
    Library(con).add(checksum="x1", original_name="scan.pdf", original_ext=".pdf",
                     mime_type="application/pdf", size=10, content=BILL, title="scan",
                     status="ready", doc_id=7)
    con.close()
    return data


def _run(script, *args):
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def test_ask_eval_scores_questions(tmp_path):
    data = _data_dir(tmp_path)
    qs = tmp_path / "q.jsonl"
    qs.write_text(json.dumps({"q": "How much is my energy bill?", "expect_docs": [7],
                              "expect_value": "142.37", "type": "lookup"}) + "\n" +
                  json.dumps({"q": "Where is my passport?", "expect_docs": [],
                              "expect_value": "not found", "type": "not_found"}) + "\n")
    report = _run("ask_eval.py", "--data-dir", str(data), "--questions", str(qs))
    s = report["summary"]
    assert s["hit@1"] == 1.0 and s["correct_refusals"] == "1/1", s


def test_filing_eval_offline_layers(tmp_path):
    data = _data_dir(tmp_path)
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"documents": [
        {"id": 7, "company": "Acme Energy", "type": "Bill", "doc_date": "2026-03-01",
         "total": 142.37, "ids": [{"label": "account", "last4": "5678"}]}]}))
    before = (data / "paperjay.db").read_bytes()
    report = _run("filing_eval.py", "--truth", str(truth), "--data-dir", str(data), "--layers", "rules")
    s = report["summary"]
    assert s["fields"]["company"]["pct"] == 100.0, s
    assert s["fields"]["total"]["pct"] == 100.0, s
    assert (data / "paperjay.db").read_bytes() == before  # the original was never written to
