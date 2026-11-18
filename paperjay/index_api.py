"""HTTP API for finding by structure (spec section 6): the filing cabinet, the A–Z index
and search-as-you-type.

    GET /api/browse?by=category,correspondent,year&category=medical&correspondent_id=12
    GET /api/index?kind=company&letter=a&q=&limit=200     (numbers masked; reveal=… unmasks one)
    GET /api/suggest?q=xf                                  groups for the header search box
"""
from fastapi import APIRouter, HTTPException, Query

from . import browse as browse_mod
from . import entities, indexing
from .api import Con, User

router = APIRouter()


@router.get("/api/browse")
def browse_api(con: Con, by: str = "", category: str | None = None, correspondent_id: str | None = None,
               year: str | None = None, document_type_id: str | None = None, tag_id: str | None = None,
               topic: str | None = None, month: str | None = None,
               limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    selected = {"category": category, "correspondent_id": correspondent_id, "year": year,
                "document_type_id": document_type_id, "tag_id": tag_id, "topic": topic, "month": month}
    try:
        return browse_mod.browse(con, browse_mod.resolve_path(by), selected, limit=limit, offset=offset)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/index")
def index_api(con: Con, user: User, kind: str = "company", letter: str | None = None, q: str = "",
              limit: int = Query(200, ge=1, le=1000), reveal: str | None = None):
    """One A–Z section. Numbers come back masked; `reveal` (an entry's key) unmasks that
    one entry and is audit-logged. The match for q runs on the server, so typing part of
    a number finds it while the list still only shows the last 4."""
    try:
        return indexing.az_entries(con, kind, letter=letter, q=q, limit=limit,
                                   reveal_key=reveal, user_id=user)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/suggest")
def suggest_api(con: Con, user: User, q: str = ""):
    """Header search (spec C.3): the library's vocabulary that matches what's being
    typed — companies, numbers (masked), people, topics, types, each with a count."""
    return entities.suggest(con, q)
