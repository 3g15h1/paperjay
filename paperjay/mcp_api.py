"""MCP server (0.6): lets an AI assistant such as Claude find, read, add, change and delete documents.

    POST /mcp      Model Context Protocol, Streamable HTTP transport, JSON replies (no server-sent stream)

Sign in with an access key in the `Authorization: Bearer pj_…` header; Settings → AI assistants (MCP) makes
one with the permissions you choose (read, create, update, delete). Every tool is carried out as an
ordinary request to PaperJay's own API with the same key, so the key's permissions and the account's role
apply exactly as they do everywhere else, and tools the key can't use aren't offered at all. Deleting moves
a document to the Trash (restore_document brings it back); nothing here deletes for good.
"""
import base64
import binascii
import json
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import __version__, auth

log = logging.getLogger("paperjay")
router = APIRouter()

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")   # newest first
MAX_TEXT = 20000
INSTRUCTIONS = (
    "PaperJay is a household's document library: bills, statements, receipts, policies, letters. "
    "Use search_documents to find documents (by words, company, type, tag or date) and get_document to read "
    "one, including the text read from it. ask answers a question from the documents with numbered "
    "citations. Companies are 'correspondent' labels. Dates are YYYY-MM-DD. delete_document moves a "
    "document to the Trash for 30 days; restore_document brings it back.")


class ToolError(Exception):
    pass


# ---- tools ----

def _int(desc):
    return {"type": "integer", "description": desc}


def _ids(desc):
    return {"type": "array", "items": {"type": "integer"}, "description": desc}


KIND = {"type": "string", "enum": ["tag", "correspondent", "document_type"],
        "description": "tag, correspondent (a company or person that sends documents) or document_type"}
DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}

TOOLS = [
    # read
    {"name": "search_documents", "permission": "read", "title": "Search documents",
     "description": "Find documents by words in their text, title, company, tags or notes, and/or by filters. "
                    "With no query and no filters, lists the newest documents. Returns id, title, date, "
                    "company, type and tags for each, plus the total number of matches.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "words to look for"},
         "correspondent_id": _int("only documents from this company (see list_labels)"),
         "document_type_id": _int("only documents of this type"),
         "tag_ids": _ids("only documents with all of these tags"),
         "date_from": {**DATE, "description": "documents dated on or after this day"},
         "date_to": {**DATE, "description": "documents dated on or before this day"},
         "sort": {"type": "string", "enum": ["relevance", "date_desc", "date_asc", "added_desc", "title"]},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
         "offset": {"type": "integer", "minimum": 0, "default": 0},
         "in_trash": {"type": "boolean", "description": "search the Trash instead", "default": False}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "get_document", "permission": "read", "title": "Read a document",
     "description": "One document's details (title, date, company, type, tags, status, pages), its notes and "
                    "the text read from it.",
     "inputSchema": {"type": "object", "required": ["document_id"], "properties": {
         "document_id": _int("the document's id"),
         "include_text": {"type": "boolean", "default": True},
         "max_chars": {"type": "integer", "minimum": 100, "maximum": 200000, "default": MAX_TEXT}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "ask", "permission": "read", "title": "Ask the documents",
     "description": "Answer a question from the documents, the way PaperJay's Ask box does: an answer with "
                    "[n] citations and the sources it used. Many questions (lists, totals, policy numbers) "
                    "are answered without AI; others can take a minute on a slow AI server.",
     "inputSchema": {"type": "object", "required": ["question"], "properties": {
         "question": {"type": "string"},
         "document_id": _int("ask about this one document only"),
         "use_ai": {"type": "boolean", "default": True,
                    "description": "false: only instant answers and the best passages, never the AI"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "list_labels", "permission": "read", "title": "List tags, companies or types",
     "description": "All labels of one kind with their ids and how many documents have each.",
     "inputSchema": {"type": "object", "required": ["kind"], "properties": {"kind": KIND}},
     "annotations": {"readOnlyHint": True}},
    {"name": "upcoming", "permission": "read", "title": "What's coming up",
     "description": "Bills coming due and policies expiring soon, read from the documents.",
     "inputSchema": {"type": "object", "properties": {
         "days": {"type": "integer", "minimum": 0, "maximum": 3660, "description": "how far ahead to look"}}},
     "annotations": {"readOnlyHint": True}},
    # create
    {"name": "add_document", "permission": "create", "title": "Add a document",
     "description": "Upload a file (PDF, JPEG, PNG, HEIC, TIFF and more). PaperJay reads it (OCR) and files it "
                    "in the background; search for it or get_document a little later.",
     "inputSchema": {"type": "object", "required": ["filename", "content_base64"], "properties": {
         "filename": {"type": "string", "description": "with its extension, e.g. water-bill.pdf"},
         "content_base64": {"type": "string", "description": "the file's bytes, base64-encoded"},
         "title": {"type": "string"}, "doc_date": DATE,
         "correspondent_id": _int("company"), "document_type_id": _int("type"), "tag_ids": _ids("tags")}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "add_note", "permission": "create", "title": "Add a note",
     "description": "Add a note to a document. Notes are searchable.",
     "inputSchema": {"type": "object", "required": ["document_id", "text"], "properties": {
         "document_id": _int("the document's id"), "text": {"type": "string"}}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "create_label", "permission": "create", "title": "Create a tag, company or type",
     "inputSchema": {"type": "object", "required": ["kind", "name"], "properties": {
         "kind": KIND, "name": {"type": "string"}}},
     "description": "Create a new label and return its id.",
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    # update
    {"name": "update_document", "permission": "update", "title": "Change a document's filing",
     "description": "Change title, date, company, type, category or tags. Only the fields you send change. "
                    "tag_ids replaces all tags; add_tag_ids / remove_tag_ids change some. Use null to clear "
                    "the date, company or type.",
     "inputSchema": {"type": "object", "required": ["document_id"], "properties": {
         "document_id": _int("the document's id"),
         "title": {"type": "string"}, "doc_date": {**DATE, "type": ["string", "null"]},
         "correspondent_id": {"type": ["integer", "null"]}, "document_type_id": {"type": ["integer", "null"]},
         "category": {"type": ["string", "null"]},
         "tag_ids": _ids("the complete new list of tags"), "add_tag_ids": _ids("tags to add"),
         "remove_tag_ids": _ids("tags to take off")}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}},
    {"name": "mark_reviewed", "permission": "update", "title": "Mark a document reviewed",
     "description": "Say a document's filing looks right, so it leaves Needs review.",
     "inputSchema": {"type": "object", "required": ["document_id"], "properties": {
         "document_id": _int("the document's id")}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}},
    {"name": "restore_document", "permission": "update", "title": "Restore from the Trash",
     "description": "Bring a document back from the Trash.",
     "inputSchema": {"type": "object", "required": ["document_id"], "properties": {
         "document_id": _int("the document's id")}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}},
    {"name": "rename_label", "permission": "update", "title": "Rename a tag, company or type",
     "inputSchema": {"type": "object", "required": ["kind", "label_id", "name"], "properties": {
         "kind": KIND, "label_id": {"type": "integer"}, "name": {"type": "string"}}},
     "description": "Give a label a new name.",
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}},
    # delete
    {"name": "delete_document", "permission": "delete", "title": "Delete a document",
     "description": "Move a document to the Trash. It stays there for 30 days and restore_document brings "
                    "it back; after that it's gone for good.",
     "inputSchema": {"type": "object", "required": ["document_id"], "properties": {
         "document_id": _int("the document's id")}},
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}},
    {"name": "delete_label", "permission": "delete", "title": "Delete a tag, company or type",
     "description": "Delete a label. Documents keep everything else; they just lose this label.",
     "inputSchema": {"type": "object", "required": ["kind", "label_id"], "properties": {
         "kind": KIND, "label_id": {"type": "integer"}}},
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def tools_for(user: auth.User) -> list[dict]:
    """The tools this key and account can use (a read-only account never gets the others)."""
    role = set(auth.role_permissions(user.role))
    keep = role if user.permissions is None else role & user.permissions
    return [{k: v for k, v in t.items() if k != "permission"} for t in TOOLS if t["permission"] in keep]


# ---- carrying a tool out through PaperJay's own API ----

class Api:
    def __init__(self, request: Request):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app),
                                        base_url=f"{request.url.scheme}://{request.url.netloc}",
                                        headers={"authorization": request.headers["authorization"]},
                                        timeout=httpx.Timeout(600, connect=10))

    async def __call__(self, method: str, path: str, **kw):
        r = await self.client.request(method, path, **kw)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code >= 400:
            detail = body.get("detail") or body.get("error") if isinstance(body, dict) else body
            if isinstance(detail, list):   # FastAPI validation errors
                detail = "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
            raise ToolError(str(detail or f"PaperJay answered {r.status_code}"))
        return body


def _brief(d: dict) -> dict:
    out = {"id": d["id"], "title": d["title"], "date": d.get("doc_date"), "company": d.get("correspondent"),
           "type": d.get("document_type"), "tags": [t["name"] for t in d.get("tags") or []],
           "status": d.get("status")}
    for k in ("category", "paper_no", "snippet"):
        if d.get(k):
            out[k] = d[k]
    if d.get("needs_review"):
        out["needs_review"] = True
    if d.get("deleted_at"):
        out["in_trash_since"] = d["deleted_at"]
    return out


def _need(args: dict, key: str, kind=int):
    v = args.get(key)
    if v is None or (kind is int and (isinstance(v, bool) or not isinstance(v, int))) or \
            (kind is str and (not isinstance(v, str) or not v.strip())):
        raise ToolError(f"{key} is required" + (" (a number)" if kind is int else ""))
    return v


async def run(api: Api, name: str, args: dict):
    if name == "search_documents":
        params = {"q": args.get("query") or "", "limit": min(max(int(args.get("limit") or 10), 1), 50),
                  "offset": max(int(args.get("offset") or 0), 0)}
        for k in ("correspondent_id", "document_type_id", "date_from", "date_to", "sort"):
            if args.get(k) not in (None, ""):
                params[k] = args[k]
        if args.get("tag_ids"):
            params["tags"] = ",".join(str(int(i)) for i in args["tag_ids"])
        if args.get("in_trash"):
            params["trash"] = "true"
        if not params["q"] and "sort" not in params:
            params["sort"] = "date_desc"
        r = await api("GET", "/api/documents", params=params)
        return {"total": r.get("total"), "documents": [_brief(d) for d in r.get("results", [])]}

    if name == "get_document":
        doc_id = _need(args, "document_id")
        d = await api("GET", f"/api/documents/{doc_id}")
        out = {**_brief(d), "pages": d.get("page_count"), "added": d.get("added_at"),
               "original_name": d.get("original_name"), "url": f"/documents/{doc_id}"}
        if d.get("error"):
            out["error"] = d["error"]
        out["notes"] = [{"text": n.get("body"), "at": n.get("created_at")}
                        for n in await api("GET", f"/api/documents/{doc_id}/notes")]
        if args.get("include_text", True):
            limit = min(max(int(args.get("max_chars") or MAX_TEXT), 100), 200000)
            text = d.get("content") or ""
            out["text"] = text[:limit]
            if len(text) > limit:
                out["text_cut_at"] = f"{limit} of {len(text)} characters; ask for more with max_chars"
        return out

    if name == "ask":
        body = {"q": _need(args, "question", str), "no_llm": args.get("use_ai") is False}
        if args.get("document_id") is not None:
            body["doc"] = _need(args, "document_id")
        r = await api("POST", "/api/ask", json=body)
        return {"answer": r.get("answer"), "how": r.get("mode"), "unverified": r.get("unverified") or [],
                "sources": [{"n": s.get("n"), "id": s.get("id"), "title": s.get("title"),
                             "date": s.get("created"), "company": s.get("correspondent"),
                             "snippet": s.get("snippet")} for s in r.get("sources", [])]}

    if name == "list_labels":
        kind = _need(args, "kind", str)
        return [{"id": x["id"], "name": x["name"], "documents": x.get("document_count")}
                for x in await api("GET", f"/api/labels/{kind}")]

    if name == "upcoming":
        params = {"days": args["days"]} if args.get("days") is not None else None
        return await api("GET", "/api/upcoming", params=params)

    if name == "add_document":
        filename = _need(args, "filename", str).strip().replace("/", "_").replace("\\", "_")
        try:
            data = base64.b64decode(_need(args, "content_base64", str), validate=True)
        except (binascii.Error, ValueError):
            raise ToolError("content_base64 isn't valid base64")
        if not data:
            raise ToolError("the file is empty")
        form = {k: str(args[k]) for k in ("title", "doc_date", "correspondent_id", "document_type_id")
                if args.get(k) not in (None, "")}
        if args.get("tag_ids"):
            form["tags"] = [str(int(t)) for t in args["tag_ids"]]
        r = await api("POST", "/api/documents", data=form, files=[("file", (filename, data))])
        return r

    if name == "add_note":
        doc_id = _need(args, "document_id")
        notes = await api("POST", f"/api/documents/{doc_id}/notes", json={"body": _need(args, "text", str)})
        return {"ok": True, "notes": len(notes)}

    if name == "create_label":
        r = await api("POST", f"/api/labels/{_need(args, 'kind', str)}", json={"name": _need(args, "name", str)})
        return {"id": r["id"], "name": r["name"]}

    if name == "update_document":
        doc_id = _need(args, "document_id")
        change = {k: args[k] for k in ("title", "doc_date", "correspondent_id", "document_type_id", "category")
                  if k in args}
        if "tag_ids" in args:
            change["tags"] = [int(t) for t in args["tag_ids"] or []]
        elif args.get("add_tag_ids") or args.get("remove_tag_ids"):
            current = [t["id"] for t in (await api("GET", f"/api/documents/{doc_id}"))["tags"]]
            drop = {int(t) for t in args.get("remove_tag_ids") or []}
            change["tags"] = [t for t in dict.fromkeys(current + [int(t) for t in args.get("add_tag_ids") or []])
                              if t not in drop]
        if not change:
            raise ToolError("send at least one field to change")
        return _brief(await api("PATCH", f"/api/documents/{doc_id}", json=change))

    if name in ("mark_reviewed", "restore_document", "delete_document"):
        doc_id = _need(args, "document_id")
        action = {"mark_reviewed": "reviewed", "restore_document": "restore", "delete_document": "trash"}[name]
        await api("POST", f"/api/documents/{doc_id}/{action}")
        if name == "delete_document":
            return {"ok": True, "note": "Moved to the Trash for 30 days; restore_document brings it back."}
        return {"ok": True}

    if name == "rename_label":
        r = await api("PATCH", f"/api/labels/{_need(args, 'kind', str)}/{_need(args, 'label_id')}",
                      json={"name": _need(args, "name", str)})
        return {"id": r.get("id"), "name": r.get("name")} if isinstance(r, dict) else r

    if name == "delete_label":
        await api("DELETE", f"/api/labels/{_need(args, 'kind', str)}/{_need(args, 'label_id')}")
        return {"ok": True}

    raise ToolError(f"unknown tool {name}")


# ---- JSON-RPC ----

def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


async def _handle(request: Request, user: auth.User, msg) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "not a JSON-RPC 2.0 request")
    id_, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:          # a notification (e.g. notifications/initialized): nothing to answer
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "paperjay", "title": "PaperJay", "version": __version__},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": tools_for(user)}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in tools_for(user)}:
            return _error(id_, -32602, f"no tool called {name!r} for this access key" if name in BY_NAME
                          else f"unknown tool {name!r}")
        if not isinstance(args, dict):
            return _error(id_, -32602, "arguments must be an object")
        api = Api(request)
        try:
            result = await run(api, name, args)
            log.info("mcp %s by %s", name, user.username)
            return {"jsonrpc": "2.0", "id": id_, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}]}}
        except ToolError as e:
            return {"jsonrpc": "2.0", "id": id_, "result": {"isError": True,
                                                             "content": [{"type": "text", "text": str(e)}]}}
        except (ValueError, TypeError, KeyError) as e:
            return {"jsonrpc": "2.0", "id": id_, "result": {"isError": True,
                                                             "content": [{"type": "text", "text": f"bad arguments: {e}"}]}}
        finally:
            await api.client.aclose()
    return _error(id_, -32601, f"no method {method}")


@router.post("/mcp")
async def mcp(request: Request):
    user = getattr(request.state, "user", None)
    if user is None or not user.via_token:   # an access key only, never a browser's sign-in cookie
        return JSONResponse({"detail": "use an access key: Authorization: Bearer pj_…"}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer realm="PaperJay"'})
    origin = request.headers.get("origin")   # the spec asks servers to refuse other sites' pages
    if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.url.netloc}":
        return JSONResponse({"detail": "request from another site refused"}, status_code=403)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(_error(None, -32700, "the body isn't JSON"), status_code=400)
    if isinstance(body, list):   # a batch (2025-03-26)
        replies = [r for r in [await _handle(request, user, m) for m in body] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await _handle(request, user, body)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_other():
    """No server-to-client stream and no sessions: the spec's answer for that is 405."""
    return Response(status_code=405, headers={"Allow": "POST"})
