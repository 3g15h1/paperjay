"""The tax pack and spreadsheet downloads.

    GET /api/taxpack?year=2025              the documents of that tax year, to choose from (see taxpack.py)
    GET /api/taxpack.zip?year=&categories=&topics=&tags=   the chosen ones as a zip with summary.csv
    GET /api/documents.csv?<Library filters>&ids=          the Library's list as a spreadsheet

Everyone signed in may download these (they can open every document anyway); limited access keys can't,
the same as for the full export.
"""
import time
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from . import settings, taxpack
from .api import Con
from .library import Library
from .library_api import _ids

router = APIRouter()


def _words(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


@router.get("/api/taxpack")
def pack(con: Con, year: int | None = None):
    start = settings.get(con, "tax_year_start")
    year = year if year is not None else taxpack.current_year(date.today(), start)
    if not 1900 <= year <= 2200:
        raise HTTPException(400, "year must be a year like 2025")
    data = taxpack.gather(con, year, start)
    years = sorted({int(r[0]) for r in con.execute(
        "SELECT DISTINCT substr(COALESCE(doc_date, added_at), 1, 4) FROM documents WHERE deleted_at IS NULL")
        if r[0] and r[0].isdigit()} | {year}, reverse=True)
    return {**data, "documents": [taxpack.public(d) for d in data["documents"]], "years": years,
            "tax_year_start": start,
            "defaults": {"categories": taxpack.DEFAULT_CATEGORIES, "topics": taxpack.DEFAULT_TOPICS}}


@router.get("/api/taxpack.zip")
def pack_zip(request: Request, con: Con, year: int, categories: str = "", topics: str = "", tags: str = ""):
    store = request.app.state.paperjay["store"]
    out = store.root / "tmp" / f"taxpack-{year}-{taxpack.stamp()}.zip"
    try:
        made = taxpack.build_zip(con, store, out, year, _words(categories), _words(topics), _ids(tags))
    except ValueError as e:
        out.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    name = taxpack.safe_name(f"PaperJay tax pack {made['label']}") + ".zip"
    return FileResponse(out, media_type="application/zip", filename=name,
                        background=BackgroundTask(lambda: Path(out).unlink(missing_ok=True)))


@router.get("/api/documents.csv")
def documents_csv(con: Con, q: str = "", tags: str | None = None, correspondent_id: int | None = None,
                  document_type_id: int | None = None, date_from: str | None = None, date_to: str | None = None,
                  needs_review: bool | None = None, sort: str = "date_desc", category: str | None = None,
                  topic: str | None = None, series: str | None = None, people: str | None = None,
                  has_paper: bool | None = None, paper_no: int | None = None, term: str | None = None,
                  ids: str | None = None):
    lib = Library(con)
    if ids:
        wanted = _ids(ids)[:10000]
        docs = [d for d in lib._summaries([r[0] for r in con.execute(
            f"SELECT id FROM documents WHERE deleted_at IS NULL AND id IN ({', '.join('?' * len(wanted))})",
            wanted)])] if wanted else []
        docs.sort(key=lambda d: (d["doc_date"] or d["added_at"][:10], d["id"]), reverse=True)
    else:
        docs, offset = [], 0
        while True:
            try:
                page = lib.search(q, tag_ids=_ids(tags), correspondent_id=correspondent_id,
                                  document_type_id=document_type_id, date_from=date_from or None,
                                  date_to=date_to or None, needs_review=needs_review, sort=sort, limit=500,
                                  offset=offset, category=category or None, topic=topic or None,
                                  series=series or None, people=tuple(_words(people)), has_paper=has_paper,
                                  paper_no=paper_no, term=term or None)
            except ValueError as e:
                raise HTTPException(400, str(e))
            docs += page["results"]
            offset += 500
            if offset >= page["total"] or not page["results"] or offset >= 10000:
                break
    name = f"paperjay-documents-{time.strftime('%Y-%m-%d')}.csv"
    return Response(taxpack.list_csv(docs), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
