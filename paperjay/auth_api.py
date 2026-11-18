"""Sign-in endpoints, people (admin), API tokens, and the middleware that guards every request.

    GET  /api/auth/status             {setup_needed, user}
    POST /api/auth/setup              first run only: create the first admin and sign in
    POST /api/auth/login, /logout
    POST /api/auth/password           change your own password (signs out other devices)
    GET/POST /api/auth/tokens, DELETE /api/auth/tokens/{id}   your API and calendar tokens
    GET/POST /api/users, PATCH/DELETE /api/users/{id}         (admin)
"""
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import auth
from .db import connect

log = logging.getLogger("paperjay")
router = APIRouter()


def client_address(request: Request) -> str:
    return request.client.host if request.client else ""


def _set_cookie(request: Request, response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


def install(app) -> None:
    """Add the guard to the app: who is this, and may they do this?"""

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path, method = request.url.path, request.method
        if path in auth.OPEN or path.startswith(auth.OPEN_PREFIXES):
            return await call_next(request)
        con = connect(app.state.config.db_path)
        try:
            if not auth.has_users(con):  # first run: nothing but the setup page until an admin exists
                if path.startswith("/api/") or method != "GET":
                    return JSONResponse({"detail": "PaperJay isn't set up yet", "setup": True}, status_code=403)
                return RedirectResponse("/setup", status_code=303)
            user = _identify(request, con)
        finally:
            con.close()
        if user is None:
            if path.startswith("/api/") or path == "/calendar.ics" or path == "/share" or method != "GET":
                return JSONResponse({"detail": "sign in first"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Token realm="PaperJay"'})
            nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={nxt}" if nxt != "/" else "/login", status_code=303)
        if method not in ("GET", "HEAD", "OPTIONS") and request.cookies.get(auth.COOKIE) \
                and not request.headers.get("authorization"):
            # a browser request that changes something must come from this site (cookies are SameSite=Lax
            # already; this also covers older browsers and same-site subdomains)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.url.netloc}":
                return JSONResponse({"detail": "request from another site refused"}, status_code=403)
        if not auth.allowed(user, method, path):
            return JSONResponse({"detail": "your account isn't allowed to do that"}, status_code=403)
        request.state.user = user
        return await call_next(request)


def _identify(request: Request, con: sqlite3.Connection) -> auth.User | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() in ("token", "bearer") and value.strip():
        return auth.token_user(con, value.strip())
    if request.url.path == "/calendar.ics" and request.query_params.get("token"):
        user = auth.token_user(con, request.query_params["token"])
        return user  # calendar apps can only put the token in the address
    return auth.session_user(con, request.cookies.get(auth.COOKIE, ""))


def me(request: Request) -> auth.User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "sign in first")
    return user


def _con(request: Request):
    return connect(request.app.state.config.db_path)


@router.get("/api/auth/status")
def status(request: Request):
    con = _con(request)
    try:
        user = _identify(request, con) if auth.has_users(con) else None
        return {"setup_needed": not auth.has_users(con), "user": user.public() if user else None}
    finally:
        con.close()


@router.post("/api/auth/setup")
def setup(request: Request, payload: dict):
    con = _con(request)
    try:
        if auth.has_users(con):
            raise HTTPException(409, "PaperJay is already set up; sign in instead")
        try:
            auth.create_user(con, payload.get("username", ""), payload.get("password", ""), "admin",
                             payload.get("display_name", ""))
            user, token = auth.sign_in(con, payload["username"], payload["password"], client_address(request),
                                       request.headers.get("user-agent", ""))
        except auth.AuthError as e:
            raise HTTPException(400, str(e))
        log.info("first admin %r created", user.username)
        r = JSONResponse({"user": user.public()})
        _set_cookie(request, r, token)
        return r
    finally:
        con.close()


@router.post("/api/auth/login")
def login(request: Request, payload: dict):
    con = _con(request)
    try:
        user, token = auth.sign_in(con, str(payload.get("username", "")), str(payload.get("password", "")),
                                   client_address(request), request.headers.get("user-agent", ""))
    except auth.AuthError as e:
        log.info("failed sign-in for %r from %s", str(payload.get("username", ""))[:40], client_address(request))
        raise HTTPException(429 if isinstance(e, auth.TooManyTries) else 401, str(e))
    finally:
        con.close()
    r = JSONResponse({"user": user.public()})
    _set_cookie(request, r, token)
    return r


@router.post("/api/auth/logout")
def logout(request: Request):
    con = _con(request)
    try:
        auth.sign_out(con, request.cookies.get(auth.COOKIE, ""))
    finally:
        con.close()
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE, path="/")
    return r


@router.post("/api/auth/password")
def change_password(request: Request, payload: dict):
    user = me(request)
    con = _con(request)
    try:
        row = con.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
        if not auth.check_password(str(payload.get("current", "")), row["password_hash"]):
            raise HTTPException(400, "the current password is wrong")
        try:
            auth.update_user(con, user.id, password=str(payload.get("new", "")))
            _, token = auth.sign_in(con, user.username, str(payload["new"]), client_address(request),
                                    request.headers.get("user-agent", ""))
        except auth.AuthError as e:
            raise HTTPException(400, str(e))
    finally:
        con.close()
    r = JSONResponse({"ok": True})
    _set_cookie(request, r, token)  # this device stays signed in; the others are signed out
    return r


@router.get("/api/auth/tokens")
def tokens(request: Request):
    con = _con(request)
    try:
        return auth.list_tokens(con, me(request).id)
    finally:
        con.close()


@router.post("/api/auth/tokens")
def new_token(request: Request, payload: dict):
    con = _con(request)
    try:
        token_id, token = auth.create_token(con, me(request).id, payload.get("name", ""), payload.get("scope", "all"))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()
    return {"id": token_id, "token": token, "note": "Copy it now: it won't be shown again."}


@router.delete("/api/auth/tokens/{token_id}")
def revoke(request: Request, token_id: int):
    con = _con(request)
    try:
        if not auth.revoke_token(con, me(request).id, token_id):
            raise HTTPException(404, "no such token")
    finally:
        con.close()
    return {"ok": True}


@router.get("/api/users")
def users(request: Request):
    con = _con(request)
    try:
        return auth.list_users(con)
    finally:
        con.close()


@router.post("/api/users")
def add_user(request: Request, payload: dict):
    con = _con(request)
    try:
        return auth.create_user(con, payload.get("username", ""), payload.get("password", ""),
                                payload.get("role", "member"), payload.get("display_name", "")).public()
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()


@router.patch("/api/users/{user_id}")
def edit_user(request: Request, user_id: int, payload: dict):
    con = _con(request)
    try:
        auth.update_user(con, user_id, role=payload.get("role"), disabled=payload.get("disabled"),
                         display_name=payload.get("display_name"), password=payload.get("password"))
        return next(u for u in auth.list_users(con) if u["id"] == user_id)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()


@router.delete("/api/users/{user_id}")
def remove_user(request: Request, user_id: int):
    if user_id == me(request).id:
        raise HTTPException(400, "you can't delete your own account")
    con = _con(request)
    try:
        auth.delete_user(con, user_id)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()
    return {"ok": True}
