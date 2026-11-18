import threading
import time

from paperjay import db, jobs


def test_priority_order_and_dedupe(con):
    a = jobs.enqueue(con, "ocr", payload={"n": 1})
    b = jobs.enqueue(con, "embed", priority=10)
    con.execute("INSERT INTO documents(id, title, checksum, original_name, original_ext, mime_type, size) "
                "VALUES (5, 't', 'c', 'n', '.pdf', 'application/pdf', 1)")
    c = jobs.enqueue(con, "ocr", document_id=5)
    assert jobs.enqueue(con, "ocr", document_id=5) == c
    assert [jobs.claim(con, "w")["id"] for _ in range(3)] == [b, a, c]
    assert jobs.claim(con, "w") is None


def test_retry_with_backoff_then_fail(con):
    j = jobs.enqueue(con, "ocr", max_attempts=2)
    job = jobs.claim(con, "w")
    assert job["attempts"] == 1 and job["payload"] == {}
    assert jobs.fail(con, j, "timeout") is True
    assert jobs.claim(con, "w") is None  # waiting for its backoff
    con.execute("UPDATE jobs SET run_after = 0 WHERE id = ?", (j,))
    assert jobs.claim(con, "w")["attempts"] == 2
    assert jobs.fail(con, j, "timeout again") is False
    assert jobs.counts(con)["failed"] == 1
    jobs.retry_failed(con, j)
    assert jobs.claim(con, "w")["attempts"] == 1


def test_recover_interrupted(con):
    jobs.enqueue(con, "ocr")
    jobs.claim(con, "w")
    assert jobs.recover(con) == 1
    assert jobs.claim(con, "w2") is not None


def test_worker_runs_each_job_once_across_threads(tmp_path):
    path = tmp_path / "p.db"
    db.migrate(path, tmp_path / "b")
    con = db.connect(path)
    for i in range(40):
        jobs.enqueue(con, "count", payload={"i": i})
    jobs.enqueue(con, "boom", max_attempts=1)
    jobs.enqueue(con, "perm", max_attempts=5)
    seen, lock = [], threading.Lock()

    def count(c, job):
        with lock:
            seen.append(job["payload"]["i"])

    def boom(c, job):
        raise RuntimeError("nope")

    def perm(c, job):
        raise jobs.Permanent("not a document")

    w = jobs.Worker(path, {"count": count, "boom": boom, "perm": perm}, threads=3, poll=0.05)
    w.start()
    deadline = time.time() + 10
    while time.time() < deadline and jobs.counts(con)["queued"] + jobs.counts(con)["running"]:
        time.sleep(0.05)
    w.stop()
    assert sorted(seen) == list(range(40))
    assert jobs.counts(con) == {"queued": 0, "running": 0, "done": 40, "failed": 2}
    perm_row = con.execute("SELECT attempts, error FROM jobs WHERE kind = 'perm'").fetchone()
    assert perm_row["attempts"] == 1 and "not a document" in perm_row["error"]
