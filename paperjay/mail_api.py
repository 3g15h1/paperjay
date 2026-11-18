"""Mailboxes to take documents from (admin only).

    GET    /api/mail                        {accounts, mail_time, running}
    POST   /api/mail/accounts               add one (signs in first, so a wrong password is caught now)
    PATCH  /api/mail/accounts/{id}          change it; DELETE removes it (documents already taken stay)
    POST   /api/mail/test                   sign in with these settings (or {id}): folders + how many emails match
    POST   /api/mail/accounts/{id}/check    check it now, in the background
    POST   /api/mail/discover               {email} → its provider, server and how to make an app password
    POST   /api/mail/senders                sign in with these settings: who sent documents lately (to pick from)
"""
from fastapi import APIRouter, HTTPException, Request

from . import mail, mail_setup, settings
from .api import Con, User

router = APIRouter()


def _poller(request: Request) -> mail.Poller:
    return request.app.state.paperjay["mail"]


def _merged(con, payload: dict) -> dict:
    """The settings in the body, filling in the saved password (and anything else left out) for an existing account."""
    base = {"port": 993, "security": "ssl", "folder": "INBOX", "from_filter": "", "subject_filter": "",
            "unread_only": False, "look_back_days": 30, "after": "keep", "move_to": "", "enabled": True}
    if payload.get("id"):
        saved = mail.get(con, int(payload["id"]))
        if not saved:
            raise HTTPException(404, "no such mailbox")
        base.update(saved)
    values = {k: v for k, v in payload.items() if not (k == "password" and v == mail.HIDDEN)}
    try:
        return {**base, **mail._clean({**base, **values})}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/mail")
def overview(request: Request, con: Con):
    return {"accounts": mail.accounts(con), "mail_time": settings.get(con, "mail_time"),
            "running": sorted(_poller(request).running)}


@router.post("/api/mail/test")
def test(con: Con, payload: dict):
    acc = _merged(con, payload)
    try:
        return mail.test(acc)
    except mail.MailError as e:
        return {"ok": False, "message": str(e)}


@router.post("/api/mail/accounts", status_code=201)
def add(request: Request, con: Con, user: User, payload: dict):
    acc = _merged(con, {k: v for k, v in payload.items() if k != "id"})
    try:
        mail.test(acc)
    except mail.MailError as e:
        raise HTTPException(400, str(e))
    return mail.create(con, acc, user)


@router.patch("/api/mail/accounts/{account_id}")
def change(con: Con, account_id: int, payload: dict):
    try:
        out = mail.update(con, account_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if out is None:
        raise HTTPException(404, "no such mailbox")
    return out


@router.delete("/api/mail/accounts/{account_id}", status_code=204)
def remove(con: Con, account_id: int):
    if not mail.delete(con, account_id):
        raise HTTPException(404, "no such mailbox")


@router.post("/api/mail/accounts/{account_id}/check", status_code=202)
def check(request: Request, con: Con, account_id: int):
    if not mail.get(con, account_id):
        raise HTTPException(404, "no such mailbox")
    started = _poller(request).check(account_id)
    return {"started": started, "running": sorted(_poller(request).running)}


@router.post("/api/mail/discover")
def discover(payload: dict):
    try:
        return mail_setup.discover(str(payload.get("email", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/mail/senders")
def senders(con: Con, payload: dict):
    acc = _merged(con, payload)
    try:
        return mail_setup.find_senders(acc)
    except mail.MailError as e:
        return {"ok": False, "message": str(e)}
